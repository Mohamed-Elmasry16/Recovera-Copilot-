# Recovera Backend 

Recovera Backend v9 is a backend-only AI analytics copilot for revenue leakage detection. It exposes a FastAPI API that turns business questions into safe PostgreSQL analytics using multi-agent orchestration, deterministic question-pattern routing, RAG retrieval, SQL generation, PostgreSQL compiler validation, read-only query execution, and grounded chart generation for frontend consumption.

> Internal note: the packaged backend is the v9 Grounded Charts build. Some runtime metadata in `main.py` still reports API version `7.0.0`; that is an internal API label retained by the current code.

## Key Features

- **FastAPI backend** with chat, session, history, health, stats, and compatibility copilot endpoints.
- **Multi-agent orchestration** for planning, retrieval, SQL generation, validation, execution, interpretation, and visualization.
- **Deterministic question-pattern coverage** for common revenue leakage question families.
- **SQL generation** through OpenRouter-hosted models with domain-specific revenue leakage prompts.
- **Deterministic SQL safety checks** using SQL parsing rather than simple substring matching.
- **PostgreSQL `EXPLAIN` validation** before execution to validate syntax, table names, columns, aliases, CTE output, casts, and functions against the real database.
- **Read-only execution** using `BEGIN READ ONLY`, local timeouts, search path control, lock timeout, and an enforced outer row cap.
- **Dynamic schema registry** from the live database, with exported JSON schema files as fallback.
- **RAG retrieval** over schema, business rules, metrics, leakage reasons, and review embeddings.
- **Query result caching** through `rag.query_result_cache`.
- **Grounded chart grammar** that chooses charts by analytical semantics rather than blindly plotting numeric columns.
- **Golden question regression checks** for deterministic routing and chart behavior.

## Architecture

```text
User Question
  → Intent / Question Profile Detection
  → Planner Agent
  → RAG Retrieval when needed
  → SQL Generator or Deterministic SQL Template
  → SQL Safety Validator
  → PostgreSQL EXPLAIN Compiler Check
  → Read-only Query Executor
  → Analytics Interpreter
  → Chart Grammar
  → API Response
```

The system has two complementary paths:

1. **Deterministic fast path** for known analytics families such as KPI summaries, leakage trends, seller risk, scenario rankings, payment leakage, shipping leakage, refund analysis, campaign performance, web analytics, and dual-ranking questions.
2. **LLM-assisted path** for broader analytical questions, still constrained by deterministic safety checks and PostgreSQL compiler validation before any SQL is executed.

## Repository Structure

```text
.
├── main.py                         # FastAPI app, request/response models, endpoints
├── pyproject.toml                  # Package metadata and dependencies
├── .env.example                    # Required runtime configuration template
├── app/
│   ├── agents/
│   │   ├── planner_agent.py        # Query planning
│   │   ├── sql_generator.py        # SQL generation and deterministic templates
│   │   ├── sql_validator.py        # Validator API and optional LLM critic integration
│   │   ├── sql_safety.py           # Deterministic read-only AST safety checks
│   │   ├── sql_compiler.py         # PostgreSQL EXPLAIN validation
│   │   ├── query_executor.py       # Read-only execution, timeout, row cap, cache
│   │   └── analytics_interpreter.py# Natural-language result interpretation
│   ├── core/
│   │   ├── orchestrator.py         # End-to-end multi-agent pipeline
│   │   ├── config.py               # Environment/config loading
│   │   ├── question_patterns.py    # Deterministic question-family classifier
│   │   ├── chart_grammar.py        # Deterministic chart selection and shaping
│   │   ├── schema_registry.py      # Live DB / exported JSON schema contract
│   │   ├── intent_classifier.py    # Intent support
│   │   ├── memory_layer.py         # Conversation memory handling
│   │   └── golden_evaluator.py     # Golden question regression utility
│   ├── database/
│   │   └── postgres.py             # PostgreSQL pool, schema context, logging, history
│   └── retrieval/
│       └── retrieval_engine.py     # Jina embeddings and hybrid RAG retrieval
├── data/
│   ├── db_schema/                  # Exported database schema/documentation fallback
│   │   └── DATABASE_DOCUMENTATION.md
│   └── evaluation/
│       └── golden_questions.json   # Regression questions
└── tests/                          # Unit and regression tests
```

## Tech Stack

- Python 3.11+
- FastAPI
- Uvicorn
- Pydantic v2
- asyncpg
- PostgreSQL / Supabase
- sqlglot
- httpx
- python-dotenv
- pytest / pytest-asyncio
- Jina AI embeddings
- Groq
- OpenRouter

## Environment Variables

Create a local `.env` file from `.env.example` and fill in the required values.

```bash
cp .env.example .env
```

### API Keys

| Variable | Purpose |
|---|---|
| `JINA_API_KEY` | Jina AI embedding API key used by the RAG retrieval engine. |
| `GROQ_API_KEY` | Groq API key used by the planner model. |
| `OPENROUTER_API_KEY_1` | First OpenRouter key used by the multi-key router. |
| `OPENROUTER_API_KEY_2` | Second OpenRouter key used by the multi-key router. |
| `OPENROUTER_API_KEY_3` | Third OpenRouter key used by the multi-key router. |

### Models

| Variable | Default | Purpose |
|---|---:|---|
| `JINA_MODEL` | `jina-embeddings-v5` | Embedding model. |
| `JINA_EMBED_DIM` | `1024` | Expected embedding vector dimension. |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Planner model via Groq. |
| `MODEL_SQL` | `z-ai/glm-4.5-air:free` | SQL generator model via OpenRouter. |
| `MODEL_VALIDATOR` | `deepseek/deepseek-v4-flash:free` | Optional SQL critic model. |
| `MODEL_ANALYTICS` | `minimax/minimax-m2.5:free` | Analytics interpretation model. |
| `VALIDATOR_LLM_ENABLED` | `false` | Enables optional LLM SQL critic. Deterministic validation remains the primary safety layer. |

### Database

| Variable | Default | Purpose |
|---|---:|---|
| `DB_HOST` | `localhost` | PostgreSQL or Supabase session pooler host. |
| `DB_PORT` | `5432` | PostgreSQL port. |
| `DB_NAME` | `revenue_leakage` | Database name. |
| `DB_USER` | `chatbot_readonly` | Database user. Prefer a read-only role for business schemas. |
| `DB_PASSWORD` | empty | Database password. |
| `DB_POOL_MIN` | `2` | Minimum asyncpg pool size. |
| `DB_POOL_MAX` | `10` | Maximum asyncpg pool size. |

### Runtime and Caching

| Variable | Default | Purpose |
|---|---:|---|
| `STATEMENT_TIMEOUT_MS` | `15000` | Per-query PostgreSQL statement timeout. |
| `MAX_QUERY_ROWS` | `200` | Maximum rows returned to the application. Queries are wrapped with an outer limit of `MAX_QUERY_ROWS + 1` to detect truncation. |
| `QUERY_CACHE_TTL_SECONDS` | `86400` | Query-result cache TTL. |
| `SCHEMA_CACHE_TTL_SECONDS` | `3600` | Schema cache TTL. |
| `ENVIRONMENT` | `development` | Runtime environment. Controls reload behavior in the direct `python main.py` entrypoint. |
| `LOG_LEVEL` | `INFO` | Python logging level. |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:5173` | Comma-separated allowed frontend origins. Restrict in production. |

`app/core/config.py` also supports `APP_HOST`, `APP_PORT`, `APP_SITE`, `APP_NAME`, `EMBED_CACHE_TTL_SECONDS`, and optional Langfuse keys, although they are not listed in the shipped `.env.example`.

## Installation

```bash
python -m venv .venv
```

macOS / Linux:

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Install the backend package:

```bash
pip install -e .
```

For development and tests:

```bash
pip install -e ".[dev]"
```

Create the environment file:

```bash
cp .env.example .env
```

Then edit `.env` with your database credentials and API keys.

## Running the API

Development server:

```bash
uvicorn main:app --reload
```

Default direct-entrypoint settings from `app/core/config.py` are:

```text
APP_HOST=0.0.0.0
APP_PORT=8000
```

The API will be available at:

```text
http://localhost:8000
```

The root endpoint serves `static/index.html` if a static frontend build exists. This package is backend-only, so the fallback page is expected unless a frontend build has been added under `static/`.

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/session` | Creates a new chat session UUID. |
| `POST` | `/api/chat` | Main chat endpoint. Runs the full copilot pipeline. |
| `POST` | `/api/copilot` | Compatibility alias for frontends that call `/api/copilot`. |
| `GET` | `/api/history/{session_id}` | Returns up to 50 conversation messages for a session. |
| `GET` | `/api/health` | Returns environment, database health, and model configuration. |
| `GET` | `/api/stats` | Returns key-router and embedding statistics. |
| `GET` | `/` | Serves static frontend if present, otherwise returns a fallback HTML page. |

## API Examples

### Create a Session

```bash
curl -X POST http://localhost:8000/api/session
```

Example response:

```json
{
  "session_id": "b13f9f8d-f8c2-4a1d-b1b8-8c8d4f5e1e31"
}
```

### Send a Chat Message

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "b13f9f8d-f8c2-4a1d-b1b8-8c8d4f5e1e31",
    "message": "Which leakage scenario has the highest revenue at risk?"
  }'
```

`/api/chat` accepts the user question under `message`. For frontend compatibility, `query`, `text`, and `content` are also normalized to `message`.

Example response shape:

```json
{
  "session_id": "b13f9f8d-f8c2-4a1d-b1b8-8c8d4f5e1e31",
  "answer": "The highest revenue-at-risk scenario is ...",
  "sql_used": "SELECT scenario, total_orders, revenue_at_risk FROM ml_output.mv_leakage_by_scenario ORDER BY revenue_at_risk DESC LIMIT 20",
  "row_count": 20,
  "execution_ms": 42,
  "error": null,
  "steps": [
    "3. Planner: intent=leakage_detection, route=sql_only, ...",
    "6. SQL generated: true",
    "9. Analytics: SQL results interpreted"
  ],
  "intent": "leakage_detection",
  "route": "sql_only",
  "difficulty": "medium",
  "rag_retrieved": 0,
  "rag_cached": false,
  "total_tokens_in": 0,
  "total_tokens_out": 0,
  "confidence": 0.94,
  "chart_data": {
    "type": "bar",
    "labels": ["scenario_a", "scenario_b"],
    "datasets": [
      {
        "label": "revenue_at_risk",
        "data": [125000.0, 99000.0]
      }
    ],
    "title": "Leakage by Scenario",
    "unit": "money",
    "chart_note": "Chart shows one coherent metric family; full query table contains the remaining metrics."
  }
}
```

## Chat Response Shape

| Field | Type | Meaning |
|---|---|---|
| `session_id` | string | Chat session ID. |
| `answer` | string | Natural-language answer generated from the pipeline result. |
| `sql_used` | string or null | SQL that was actually used, when the route executed SQL. |
| `row_count` | integer | Number of rows returned after the enforced row cap. |
| `execution_ms` | integer | SQL execution time in milliseconds. Cache hits may report `0`. |
| `error` | string or null | SQL or pipeline error message, if any. |
| `steps` | array of strings | Trace of major pipeline decisions and actions. |
| `intent` | string | Classified business intent. |
| `route` | string | Execution route, such as `sql_only`, `hybrid`, `rag_only`, or `non_database`. |
| `difficulty` | string | Planner difficulty estimate. |
| `rag_retrieved` | integer | Number of retrieved RAG documents/chunks. |
| `rag_cached` | boolean | Whether RAG context came from retrieval cache. |
| `total_tokens_in` | integer | Aggregate prompt tokens reported by model calls. |
| `total_tokens_out` | integer | Aggregate completion tokens reported by model calls. |
| `confidence` | number | Pipeline confidence score. |
| `chart_data` | object or null | Frontend-ready chart payload selected by deterministic chart grammar. |

## Database Notes

The database schema is organized around ecommerce, marketing, model output, and retrieval schemas:

```text
ecommerce   # orders, payments, shipping, refunds, reviews, customers, sellers, products
marketing   # campaigns, attribution, leads, website sessions, customer interactions
ml_output   # anomaly scores, leakage reasons, chatbot logs, materialized views
rag         # documents, embeddings, retrieval cache, SQL guard, query result cache
```

Preferred analytics sources are the materialized views under `ml_output`:

| View | Purpose |
|---|---|
| `ml_output.mv_leakage_dashboard` | Pre-joined order-level dashboard view. Preferred for most order/customer/city/payment/shipping queries. |
| `ml_output.mv_monthly_leakage` | Monthly leakage aggregates, leakage rate, revenue at risk, revenue, and profit. |
| `ml_output.mv_seller_risk` | Seller-level leakage and risk aggregates. |
| `ml_output.mv_leakage_by_scenario` | Leakage scenario counts and financial impact. |

Important assumptions:

- All money values are in **EGP**.
- Business-data access should be read-only.
- `rag.query_result_cache` is assumed to already exist in the target database for SQL result caching.
- The schema registry loads from the live database when possible and falls back to `data/db_schema/*.json` when the database is unavailable during startup.
- `ecommerce.reviews` can contain multiple reviews per order. Prefer `ml_output.mv_leakage_dashboard` for order-level analytics to avoid row multiplication.
- Do not use `LIKE` on `review_comment`; text search and semantic review analysis should go through RAG.

## SQL Safety Model

The backend uses layered SQL safety. LLM output is never treated as safe by default.

1. **Shape validation**
   - Only `SELECT`, `WITH SELECT`, and read-only SELECT set operations are allowed.
   - DML, DDL, administrative commands, and multiple statements are blocked.
   - SQL comments are stripped before validation.

2. **AST-based table access validation**
   - SQL is parsed with `sqlglot` in PostgreSQL mode.
   - Access is limited to allowed schemas from the schema registry.
   - Unknown tables/views are blocked when resolvable.
   - CTE names are handled separately so valid analytical CTEs are not blocked.

3. **SQL guard rules**
   - Runtime guard patterns from `rag.sql_guard` can block or warn on configured SQL patterns.

4. **PostgreSQL compiler validation**
   - The executor runs `EXPLAIN (FORMAT JSON, COSTS TRUE)` before execution.
   - This validates actual database syntax, columns, aliases, CTE outputs, function names, enum casts, and table names without running the query.

5. **Read-only execution**
   - Queries run inside `BEGIN READ ONLY`.
   - The executor sets a controlled search path: `ml_output, ecommerce, marketing, rag, public`.
   - Local `statement_timeout`, `lock_timeout`, and `idle_in_transaction_session_timeout` are applied.
   - The query is wrapped with an outer row limit to enforce `MAX_QUERY_ROWS`.

## Chart Grammar

`app/core/chart_grammar.py` builds deterministic `chart_data` from SQL results and the query plan.

Rules:

- Time, month, quarter, or ordered date results become **line charts**.
- Rankings and categorical comparisons become **bar charts**.
- Explicit share, mix, distribution, or part-to-whole questions can become **doughnut charts** when the category count is suitable.
- KPI summaries, ID lookups, detail rows, ambiguous correlation questions, and incompatible metric mixes may return **table-only** results with `chart_data = null`.
- Money, rate, count, score, and duration metrics are not mixed on one misleading axis.
- Dual-ranking results use the business dimension as the label, not the technical `ranking_type` field.
- `chart_data` is a visual summary for the frontend. The full query result remains the source of detail.

## Testing

Install dev dependencies first:

```bash
pip install -e ".[dev]"
```

Run all tests:

```bash
pytest -q
```

Run deterministic golden-question checks:

```bash
PYTHONPATH=. python -m app.core.golden_evaluator
```

Run chart and golden coverage tests:

```bash
PYTHONPATH=. pytest -q tests/test_chart_grammar.py tests/test_golden_question_coverage.py
```

Useful focused tests:

```bash
pytest -q tests/test_sql_safety.py tests/test_sql_validator.py
pytest -q tests/test_question_patterns.py tests/test_sql_templates.py
pytest -q tests/test_v9_profit_loss_and_grounding.py
```

Some tests are non-DB regression checks. End-to-end chat behavior requires PostgreSQL/Supabase credentials and API keys in `.env`.

## Development Notes

- Keep `VALIDATOR_LLM_ENABLED=false` unless you specifically need the optional LLM critic.
- Do not rely on LLM validation as the primary safety mechanism. Deterministic safety plus PostgreSQL `EXPLAIN` is the primary validator.
- Prefer the materialized views before base-table joins.
- Avoid `SELECT *`; use explicit columns.
- Avoid `LIKE` on `review_comment`; use the RAG path for text and review analysis.
- Keep SQL generation prompts focused on the database contract under `data/db_schema/`.
- Keep deterministic templates for high-frequency business questions where correctness matters more than flexibility.
- Treat `chart_data` as a compact visual representation, not a replacement for tabular results.

## Known Assumptions / Requirements

- Python 3.11 or newer.
- PostgreSQL or Supabase database is available for live operation.
- Required API keys are configured in `.env`.
- The database includes the ecommerce, marketing, ml_output, and rag schemas expected by the exported schema files.
- `rag.query_result_cache` exists if query result caching is enabled.
- Exported schema JSON files under `data/db_schema/` can be used as a startup fallback for the schema registry.
- This is a backend-only bundle. It can serve a static frontend if one is placed under `static/`, but the repository does not depend on a frontend build.

## Security Considerations

- Never commit `.env` or real credentials.
- Use a least-privilege database role. The app should read business schemas and only write to approved logging/cache/conversation tables as needed.
- Restrict `CORS_ORIGINS` in production.
- Rotate Groq, OpenRouter, Jina, and database credentials regularly.
- Keep `STATEMENT_TIMEOUT_MS` and `MAX_QUERY_ROWS` enabled.
- Review `rag.sql_guard` rules before exposing the API to untrusted users.
- Monitor query logs and cache hit patterns for suspicious usage.

## Status

Backend-only v9 Grounded Charts build for the Revenue Leakage AI Copilot / Recovera backend. It includes deterministic question coverage, grounded chart grammar, SQL safety hardening, PostgreSQL compiler validation, and regression assets for common analytics paths.

## License

Not specified.
