"""
multi_key_router.py - OpenRouter Multi-Key Strategy
====================================================
Manages multiple OpenRouter API keys with automatic fallback.

KEY DESIGN:
  - Keys are pure credentials — they are NOT tied to any specific model.
  - Each AGENT picks its model from .env (MODEL_SQL, MODEL_VALIDATOR, MODEL_ANALYTICS).
  - On failure, the SAME model is retried across all available keys in order.
  - Changing a model in .env is the ONLY thing you ever need to do.

Fallback order (all agents):
  Attempt 1 → key_1 with agent's model
  Attempt 2 → key_2 with agent's model
  Attempt 3 → key_3 with agent's model
"""

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)


# ================================================================
# KEY STATE
# ================================================================

@dataclass
class KeyState:
    """Track health state of one API key (not tied to any model)."""
    key_id: str
    api_key: str
    failures: int = 0
    last_failure: float = 0.0
    cooldown_seconds: float = 60.0
    requests_served: int = 0

    def is_healthy(self) -> bool:
        if self.failures >= 3:
            if (time.time() - self.last_failure) < self.cooldown_seconds:
                return False
            self.failures = 0          # cooldown expired → reset
        return True

    def record_failure(self):
        self.failures += 1
        self.last_failure = time.time()

    def record_success(self):
        self.failures = max(0, self.failures - 1)
        self.requests_served += 1


@dataclass
class AgentCircuitBreaker:
    """
    Global cooldown for a specific agent when ALL keys are rate-limited.

    Problem this solves
    ───────────────────
    KeyState tracks per-key health, but when all 3 keys hit 429 simultaneously
    the router raises RuntimeError and the *next* request immediately retries
    all 3 keys — which are all still rate-limited — burning quota again and
    producing another round of 429s.

    With this breaker the router fast-fails at the agent level (before touching
    any key) for `cooldown_seconds` after a full-fan-out 429 event.
    """
    tripped_at: float = 0.0
    cooldown_seconds: float = 90.0   # separate from per-key 60s cooldown

    def is_open(self) -> bool:
        """True = breaker is tripped; caller should raise without trying keys."""
        if self.tripped_at == 0.0:
            return False
        elapsed = time.time() - self.tripped_at
        if elapsed < self.cooldown_seconds:
            return True
        # Cooldown expired — reset automatically
        self.tripped_at = 0.0
        return False

    def trip(self):
        self.tripped_at = time.time()

    def reset(self):
        self.tripped_at = 0.0

    @property
    def remaining_seconds(self) -> float:
        if not self.is_open():
            return 0.0
        return self.cooldown_seconds - (time.time() - self.tripped_at)


# ================================================================
# ROUTER
# ================================================================

class MultiKeyRouter:
    """
    Calls OpenRouter with automatic key-level fallback.

    One model per agent (from .env).
    All keys are tried for that same model — no cross-model fallback.
    """

    # Fixed try-order for all agents
    _KEY_ORDER = ["key_1", "key_2", "key_3"]

    def __init__(self):
        # Keys are just credentials — no model attached
        self._keys: dict[str, KeyState] = {}
        for key_id, env_val in [
            ("key_1", settings.OPENROUTER_KEY_1),
            ("key_2", settings.OPENROUTER_KEY_2),
            ("key_3", settings.OPENROUTER_KEY_3),
        ]:
            if env_val:
                self._keys[key_id] = KeyState(key_id=key_id, api_key=env_val)

        # Agent → model (edit .env to change models; nothing else needed)
        self._agent_model: dict[str, str] = {
            "sql_generator": settings.MODEL_SQL,
            "validator":     settings.MODEL_VALIDATOR,
            "analytics":     settings.MODEL_ANALYTICS,
        }

        self._timeout      = 90.0
        self._max_retries  = 2

        # Cache of (key_id, model) pairs that returned 404 — skip on future requests
        # Cleared on server restart; models are unlikely to appear mid-session.
        self._model_404_cache: set[tuple[str, str]] = set()

        # Per-agent circuit breakers — trip when all keys return 429 simultaneously.
        # Each breaker is agent-scoped so a validator rate-limit doesn't block the
        # sql_generator or analytics agents.
        self._agent_breakers: dict[str, AgentCircuitBreaker] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def call(
        self,
        agent: str,
        messages: list[dict],
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ) -> tuple[str, int, int]:
        """
        Call OpenRouter for *agent* using its configured model.
        Tries every key in order; raises RuntimeError only when all fail.

        Returns: (content, input_tokens, output_tokens)
        """
        model = self._agent_model.get(agent)
        if not model:
            raise RuntimeError(f"No MODEL configured for agent '{agent}'. "
                               f"Check MODEL_SQL / MODEL_VALIDATOR / MODEL_ANALYTICS in .env")

        # ── Agent-level circuit breaker ────────────────────────────────────────
        # If all keys were rate-limited on a recent call we fast-fail immediately
        # instead of burning the quota again.  The breaker resets automatically
        # after cooldown_seconds (90 s by default).
        breaker = self._agent_breakers.get(agent)
        if breaker and breaker.is_open():
            remaining = breaker.remaining_seconds
            raise RuntimeError(
                f"[circuit_open] Agent '{agent}' circuit breaker is open — "
                f"all keys were rate-limited. Retry in {remaining:.0f}s."
            )
        # ───────────────────────────────────────────────────────────────────────

        # Per-key failure summary for a clear final error
        key_results: dict[str, str] = {}

        for key_id in self._KEY_ORDER:
            key_state = self._keys.get(key_id)
            if not key_state:
                continue                          # key not configured

            if not key_state.is_healthy():
                log.info(f"[{agent}] {key_id} is in cooldown — skipping")
                key_results[key_id] = "cooldown"
                continue

            # Skip keys that already returned 404 for this exact model
            if (key_id, model) in self._model_404_cache:
                log.info(f"[{agent}] {key_id} skipped — model '{model}' returned 404 previously")
                key_results[key_id] = "404 (cached)"
                continue

            for attempt in range(self._max_retries):
                try:
                    result = await self._single_call(
                        model=model,
                        api_key=key_state.api_key,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    key_state.record_success()
                    log.info(f"[{agent}] Success via {key_id} ({model})")
                    return result

                except (httpx.TimeoutException, httpx.ConnectError) as e:
                    wait = 2 ** attempt
                    log.warning(
                        f"[{agent}] {key_id} network error (attempt {attempt + 1}): {e} "
                        f"— retrying in {wait}s"
                    )
                    key_results[key_id] = f"timeout/connect error"
                    await asyncio.sleep(wait)

                except httpx.HTTPStatusError as e:
                    status = e.response.status_code
                    if status == 429:
                        log.warning(f"[{agent}] {key_id} rate-limited — trying next key")
                        key_state.record_failure()
                        key_results[key_id] = "429 rate-limited"
                        break
                    elif status in (401, 402):
                        log.error(f"[{agent}] {key_id} auth/quota error ({status}) — trying next key")
                        key_state.record_failure()
                        key_results[key_id] = f"{status} auth/quota"
                        break
                    elif status == 404:
                        log.warning(
                            f"[{agent}] {key_id} → model '{model}' not found (404) — trying next key"
                        )
                        # Remember so we skip this key for this model on future requests
                        self._model_404_cache.add((key_id, model))
                        key_results[key_id] = "404 model-not-found"
                        break
                    else:
                        log.warning(f"[{agent}] {key_id} HTTP {status} (attempt {attempt + 1})")
                        key_results[key_id] = f"HTTP {status}"
                        if attempt < self._max_retries - 1:
                            await asyncio.sleep(2 ** attempt)

                except RuntimeError as e:        # empty-response sentinel
                    log.warning(f"[{agent}] {key_id} empty response — trying next key")
                    key_results[key_id] = "empty response"
                    break

                except Exception as e:
                    log.error(f"[{agent}] {key_id} unexpected error: {e}")
                    key_state.record_failure()
                    key_results[key_id] = f"error: {e}"
                    break

        # ── Trip circuit breaker if every key was rate-limited or in cooldown ──
        rate_limit_outcomes = {"429 rate-limited", "cooldown"}
        all_rate_limited = bool(key_results) and all(
            v in rate_limit_outcomes for v in key_results.values()
        )
        if all_rate_limited:
            cb = self._agent_breakers.setdefault(agent, AgentCircuitBreaker())
            cb.trip()
            log.warning(
                f"[{agent}] Circuit breaker TRIPPED — all keys rate-limited. "
                f"Agent locked for {cb.cooldown_seconds:.0f}s."
            )
        # ───────────────────────────────────────────────────────────────────────

        # Build a clear diagnostic message
        summary = ", ".join(f"{k}={v}" for k, v in key_results.items())
        raise RuntimeError(
            f"All OpenRouter keys failed for agent '{agent}' "
            f"(model='{model}'). Per-key results: [{summary}]"
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_headers(self, api_key: str) -> dict:
        return {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer":  settings.APP_SITE,
            "X-Title":       settings.APP_NAME,
            "Content-Type":  "application/json",
        }

    async def _single_call(
        self,
        model: str,
        api_key: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, int, int]:
        """Execute one OpenRouter call and return (content, in_tok, out_tok)."""
        payload = {
            "model":       model,
            "messages":    messages,
            "temperature": temperature,
            "max_tokens":  max_tokens,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                settings.OPENROUTER_BASE_URL,
                headers=self._get_headers(api_key),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        try:
            raw = data["choices"][0]["message"].get("content")
            content = raw if raw is not None else ""
        except (KeyError, IndexError, TypeError, AttributeError):
            content = ""

        if not content:
            log.warning(f"Empty/null content from model '{model}'")
            raise RuntimeError(f"Empty response from '{model}'")

        usage   = data.get("usage", {})
        in_tok  = usage.get("prompt_tokens",      len(str(messages)) // 4)
        out_tok = usage.get("completion_tokens",   len(content) // 4)

        return content, in_tok, out_tok

    # ------------------------------------------------------------------
    # Monitoring helpers
    # ------------------------------------------------------------------

    async def health_check(self) -> dict:
        return {
            key_id: {
                "healthy":          state.is_healthy(),
                "failures":         state.failures,
                "requests_served":  state.requests_served,
            }
            for key_id, state in self._keys.items()
        }

    def get_key_stats(self) -> dict:
        return {
            key_id: {
                "requests": state.requests_served,
                "failures": state.failures,
                "healthy":  state.is_healthy(),
            }
            for key_id, state in self._keys.items()
        }

    def get_agent_circuit_breakers(self) -> dict:
        """Show circuit-breaker state per agent (open/closed + remaining cooldown)."""
        return {
            agent: {
                "open":              cb.is_open(),
                "remaining_seconds": round(cb.remaining_seconds, 1),
                "cooldown_seconds":  cb.cooldown_seconds,
            }
            for agent, cb in self._agent_breakers.items()
        }

    def get_agent_models(self) -> dict:
        """Show which model each agent is currently using."""
        return dict(self._agent_model)


# ================================================================
# Global singleton
# ================================================================
key_router = MultiKeyRouter()


async def call_llm(
    agent: str,
    messages: list[dict],
    temperature: float = 0.1,
    max_tokens: int = 2048,
) -> tuple[str, int, int]:
    """Public helper — call the configured model for *agent* with automatic key fallback."""
    return await key_router.call(agent, messages, temperature, max_tokens)