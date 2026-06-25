"""
main.py - Revenue Leakage AI Copilot API
==========================================
FastAPI application with full endpoints:
  POST /api/chat          - Main chat endpoint
  POST /api/session       - Create new session
  GET  /api/history/{id}  - Get conversation history
  GET  /api/health        - Full health check
  GET  /api/stats         - System statistics
  GET  /                  - Serve React frontend
y
"""

import os
import sys
import uuid
import asyncio
import logging
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(override=True)

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from pydantic import BaseModel, Field, model_validator
from typing import Optional
import secrets

from app.core.config import settings
from app.database.postgres import init_pool, load_schema_context, load_sql_guard, health_check as db_health
from app.core.orchestrator import Orchestrator, get_orchestrator
from app.core.memory_layer import memory_store
from app.core.intent_classifier import ensure_intent_embeddings_table
from app.database.postgres import get_pool
from app.core.schema_registry import init_schema_registry

log = logging.getLogger(__name__)
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)

# ================================================================
# SECURITY MIDDLEWARE
# ================================================================

# Routes that are publicly accessible (no API key required).
# /api/health  → HuggingFace uptime monitoring
# /            → fallback HTML page (no sensitive data)
_PUBLIC_PATHS = {"/", "/api/health"}


class APIKeyMiddleware(BaseHTTPMiddleware):
    """
    Reject every request to /api/* (except /api/health) that does NOT carry
    the correct X-API-Key header.  Uses constant-time comparison to prevent
    timing attacks.

    If API_SECRET_KEY is not configured (local dev without the var set),
    the middleware lets requests through with a warning so local development
    still works without the key.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip check for public paths and anything outside /api/
        path = request.url.path
        if path in _PUBLIC_PATHS or not path.startswith("/api/"):
            return await call_next(request)

        secret = settings.API_SECRET_KEY
        if not secret:
            # Dev mode: key not configured → warn but allow
            log.warning("[AUTH] API_SECRET_KEY not set – running unprotected!")
            return await call_next(request)

        provided = request.headers.get("X-API-Key", "")
        if not secrets.compare_digest(provided, secret):
            log.warning(
                f"[AUTH] Rejected request to {path} "
                f"from {request.client.host} – bad or missing X-API-Key"
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized – invalid or missing API key."},
            )

        return await call_next(request)


# ================================================================
# FASTAPI APP
# ================================================================

app = FastAPI(
    title="Revenue Leakage AI Copilot",
    description="Enterprise-grade AI analytics platform for revenue leakage detection",
    version="7.0.0",
)

# CORS — restrict to allowed origins only
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "X-API-Key"],
)

# API Key guard — runs AFTER CORS so pre-flight OPTIONS are not blocked
app.add_middleware(APIKeyMiddleware)

# Static files (React frontend)
BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ================================================================
# ERROR HANDLERS
# ================================================================

@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Return readable 422 errors so frontend field mismatches are obvious in logs."""
    log.warning(f"[422] Validation error on {request.url.path}: {exc.errors()} | body={exc.body}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": str(exc.body)},
    )


# ================================================================
# STARTUP
# ================================================================

@app.on_event("startup")
async def startup():
    """Initialize all subsystems."""
    print("=" * 60)
    print("Revenue Leakage AI Copilot v7.0")
    print("Enterprise Multi-Agent System")
    print("=" * 60)

    # Validate config
    missing = settings.validate()
    if missing:
        print(f"[WARN] Missing config: {', '.join(missing)}")
    else:
        print("[OK] All API keys configured")

    # 1. Database
    try:
        await init_pool()
        await load_schema_context()
        await load_sql_guard()
        await init_schema_registry(get_pool())
        print("[OK] Database pool + schema registry + SQL guard")
    except Exception as e:
        print(f"[ERROR] Database initialization failed: {e}")
        # Keep local/dev startup possible with exported schema files, but /api/chat
        # will still fail clearly if the DB pool is unavailable.
        try:
            await init_schema_registry(None)
            print("[WARN] Schema registry loaded from JSON fallback")
        except Exception as schema_e:
            print(f"[ERROR] Schema registry fallback failed: {schema_e}")

    # 2. Intent embeddings table
    try:
        pool = get_pool()
        await ensure_intent_embeddings_table(pool)
        print("[OK] Intent embeddings table initialized")
    except Exception as e:
        print(f"[WARN] Intent embeddings init failed: {e}")

    # 3. Embedding check
    try:
        from app.retrieval.retrieval_engine import _embed_client
        print(f"[OK] Jina AI embedding configured (dim={_embed_client.get_dim()})")
    except Exception as e:
        print(f"[WARN] Embedding client: {e}")

    print("=" * 60)
    print("Ready for requests.")
    print("=" * 60)


# ================================================================
# MODELS
# ================================================================

class ChatRequest(BaseModel):
    session_id: str
    message: str = Field(default="", min_length=0, max_length=2000)

    @model_validator(mode="before")
    @classmethod
    def normalize_message_field(cls, values):
        """
        Accept any common field name the frontend might send:
          message / query / text / content
        Whichever is present and non-empty wins.
        """
        msg = (
            values.get("message")
            or values.get("query")
            or values.get("text")
            or values.get("content")
            or ""
        )
        values["message"] = msg
        return values

    @model_validator(mode="after")
    def check_message_not_empty(self):
        if not self.message or not self.message.strip():
            raise ValueError(
                "message field is required and must not be empty "
                "(also accepted: 'query', 'text', 'content')"
            )
        return self


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    sql_used: Optional[str] = None
    row_count: int = 0
    execution_ms: int = 0
    error: Optional[str] = None
    steps: list[str] = []
    intent: str = ""
    route: str = ""
    difficulty: str = ""
    rag_retrieved: int = 0
    rag_cached: bool = False
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    confidence: float = 0.0
    chart_data: Optional[dict] = None


class SessionResponse(BaseModel):
    session_id: str


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    database: dict
    config: dict


# ================================================================
# ENDPOINTS
# ================================================================

# Hard ceiling on the full multi-agent pipeline. Kept comfortably under the
# Vercel frontend's maxDuration (and HF Spaces' own proxy timeout, which tends
# to sit around 60s) so that on a pathological case (e.g. a free-tier
# OpenRouter model rate-limited across all retry keys) the backend itself
# returns a clean, friendly error within budget instead of letting the
# connection hang until an external proxy kills it with a bare 504.
# Does NOT affect any query that already completes quickly today — it only
# ever fires for requests that are already past the point of being usable.
PIPELINE_TIMEOUT_SECONDS = float(os.getenv("PIPELINE_TIMEOUT_SECONDS", "45"))


async def _run_chat_pipeline(req: ChatRequest) -> ChatResponse:
    """Shared implementation for /api/chat and /api/copilot compatibility aliases."""
    try:
        pool = get_pool()
        orchestrator = get_orchestrator(pool)

        try:
            result = await asyncio.wait_for(
                orchestrator.process(req.session_id, req.message),
                timeout=PIPELINE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            log.warning(
                f"[chat] Pipeline exceeded {PIPELINE_TIMEOUT_SECONDS}s — "
                f"returning graceful timeout response (session={req.session_id})"
            )
            return ChatResponse(
                session_id=req.session_id,
                answer=(
                    "This question is taking longer than expected to analyze "
                    "(usually a busy AI provider). Please try again, or rephrase "
                    "it as a more specific question (e.g. a date range or metric)."
                ),
                error="pipeline_timeout",
            )

        return ChatResponse(
            session_id=req.session_id,
            answer=result.answer,
            sql_used=result.sql_used,
            row_count=result.row_count,
            execution_ms=result.execution_ms,
            error=result.sql_error,
            steps=result.steps,
            intent=result.intent,
            route=result.route,
            difficulty=result.difficulty,
            rag_retrieved=result.rag_retrieved,
            rag_cached=result.rag_cached,
            total_tokens_in=result.total_tokens_in,
            total_tokens_out=result.total_tokens_out,
            confidence=result.confidence,
            chart_data=result.chart_data,
        )
    except Exception as e:
        log.error(f"Chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Main chat endpoint - runs the full multi-agent pipeline."""
    return await _run_chat_pipeline(req)


@app.post("/api/copilot", response_model=ChatResponse)
async def copilot(req: ChatRequest):
    """Compatibility alias for frontends that call /api/copilot directly."""
    return await _run_chat_pipeline(req)


@app.post("/api/session", response_model=SessionResponse)
async def new_session():
    """Create a new chat session."""
    session_id = str(uuid.uuid4())
    return SessionResponse(session_id=session_id)


@app.get("/api/history/{session_id}")
async def history(session_id: str):
    """Get conversation history for a session."""
    try:
        from app.database.postgres import load_history
        messages = await load_history(session_id, 50)
        return {"session_id": session_id, "messages": messages}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
async def health():
    """Full health check of all subsystems."""
    db_health_result = await db_health()

    status = "ok" if db_health_result.get("connected") else "degraded"

    return HealthResponse(
        status=status,
        version="7.0.0",
        environment=settings.ENVIRONMENT,
        database=db_health_result,
        config={
            "sql_model":       settings.MODEL_SQL,
            "validator_model": settings.MODEL_VALIDATOR,
            "analytics_model": settings.MODEL_ANALYTICS,
            "planner_model":   settings.MODEL_PLANNER,
            "embedding_dim":   settings.JINA_EMBED_DIM,
        },
    )


@app.get("/api/stats")
async def stats():
    """Get system statistics."""
    try:
        from app.core.multi_key_router import key_router
        from app.retrieval.retrieval_engine import _embed_client

        return {
            "key_stats":     key_router.get_key_stats(),
            "embedding_dim": _embed_client.get_dim(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ================================================================
# FRONTEND
# ================================================================

@app.get("/", response_class=HTMLResponse)
async def frontend():
    """Serve React frontend, or a fallback page if not built yet."""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="""
    <!DOCTYPE html>
    <html>
    <head><title>Revenue Leakage AI Copilot</title></head>
    <body>
        <h1>Revenue Leakage AI Copilot v7.0</h1>
        <p>Frontend not built. Run the React build to serve the UI.</p>
        <p>API is available at /api/chat, /api/health, etc.</p>
    </body>
    </html>
    """)


# ================================================================
# ENTRYPOINT
# ================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=settings.ENVIRONMENT == "development",
        log_level=settings.LOG_LEVEL.lower(),
    )