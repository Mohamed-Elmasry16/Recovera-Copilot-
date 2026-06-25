"""
config.py - Centralized Configuration Management
================================================
Loads all environment variables with sensible defaults.
Provides a single source of truth for system configuration.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path,override=True)


class Config:
    """Centralized configuration for the Revenue Leakage AI Copilot."""

    # --- Jina AI ---
    JINA_API_KEY = os.getenv("JINA_API_KEY", "")
    JINA_MODEL = os.getenv("JINA_MODEL", "jina-embeddings-v5")
    JINA_EMBED_DIM = int(os.getenv("JINA_EMBED_DIM", "1024"))
    JINA_BASE_URL = "https://api.jina.ai/v1/embeddings"

    # --- Groq ---
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    GROQ_BASE_URL = "https://api.groq.com/openai/v1/chat/completions"

    # --- OpenRouter Keys (Multi-Key Strategy) ---
    OPENROUTER_KEY_1 = os.getenv("OPENROUTER_API_KEY_1", "")
    OPENROUTER_KEY_2 = os.getenv("OPENROUTER_API_KEY_2", "")
    OPENROUTER_KEY_3 = os.getenv("OPENROUTER_API_KEY_3", "")
    OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

    # --- Model Mapping ---
    MODEL_PLANNER = GROQ_MODEL  # Groq Llama 3.3 70B
    MODEL_SQL = os.getenv("MODEL_SQL", "z-ai/glm-4.5-air:free")
    MODEL_VALIDATOR = os.getenv("MODEL_VALIDATOR", "deepseek/deepseek-v4-flash:free")
    MODEL_ANALYTICS = os.getenv("MODEL_ANALYTICS", "minimax/minimax-m2.5:free")

    # --- PostgreSQL (Supabase Session Pooler) ---
    # Session pooler host:  aws-0-<region>.pooler.supabase.com
    # Session pooler port:  5432
    # Direct connection port was 5432/5433 — pooler replaces it.
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = int(os.getenv("DB_PORT", "5432"))  # Supabase session pooler port
    DB_NAME = os.getenv("DB_NAME", "revenue_leakage")
    DB_USER = os.getenv("DB_USER", "chatbot_readonly")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "2"))
    DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))

    # --- Execution ---
    VALIDATOR_LLM_ENABLED = os.getenv("VALIDATOR_LLM_ENABLED", "false").lower() in {"1", "true", "yes"}
    STATEMENT_TIMEOUT_MS = int(os.getenv("STATEMENT_TIMEOUT_MS", "15000"))
    MAX_QUERY_ROWS = int(os.getenv("MAX_QUERY_ROWS", "200"))

    # --- Cache TTLs ---
    SCHEMA_CACHE_TTL = int(os.getenv("SCHEMA_CACHE_TTL_SECONDS", "3600"))
    QUERY_CACHE_TTL = int(os.getenv("QUERY_CACHE_TTL_SECONDS", "86400"))
    EMBED_CACHE_TTL = int(os.getenv("EMBED_CACHE_TTL_SECONDS", "300"))

    # --- App ---
    APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
    APP_PORT = int(os.getenv("APP_PORT", "8000"))
    APP_SITE = os.getenv("APP_SITE", "http://localhost:8000")
    APP_NAME = os.getenv("APP_NAME", "RevenueLeakageCopilot")
    ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

    # --- Logging ---
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # --- Langfuse ---
    LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
    LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

    # --- CORS ---
    CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",")

    # --- Security ---
    # A strong random secret shared between this backend and the Vercel frontend.
    # Generate with:  python -c "import secrets; print(secrets.token_hex(32))"
    # Store in HuggingFace Space Secrets AND as a Vercel Environment Variable.
    API_SECRET_KEY = os.getenv("API_SECRET_KEY", "")

    # --- Model routing ---
    # Keys are pure credentials — NOT tied to specific models.
    # Each agent uses the model defined above in .env.
    # Fallback: all three keys are tried in order (key_1 → key_2 → key_3)
    # for the SAME model.  To swap a model, edit only the MODEL_* lines above.

    @classmethod
    def validate(cls) -> list[str]:
        """Validate that required configuration is present. Returns list of missing keys."""
        required = [
            ("JINA_API_KEY", cls.JINA_API_KEY),
            ("GROQ_API_KEY", cls.GROQ_API_KEY),
            ("OPENROUTER_API_KEY_1", cls.OPENROUTER_KEY_1),
            ("OPENROUTER_API_KEY_2", cls.OPENROUTER_KEY_2),
            ("OPENROUTER_API_KEY_3", cls.OPENROUTER_KEY_3),
            ("DB_PASSWORD", cls.DB_PASSWORD),
        ]
        missing = [name for name, value in required if not value]
        return missing


# Global instance
settings = Config()