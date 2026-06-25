<div align="center">

# Recovera

### Revenue Intelligence Command Center

Detect leakage, surface seller risk, monitor RAG quality, and ask business questions through an AI copilot — all in a polished Next.js dashboard.

<br />

![Next.js](https://img.shields.io/badge/Next.js-14-black?style=for-the-badge&logo=nextdotjs)
![React](https://img.shields.io/badge/React-18-61DAFB?style=for-the-badge&logo=react&logoColor=0B1220)
![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?style=for-the-badge&logo=typescript&logoColor=white)
![Postgres](https://img.shields.io/badge/PostgreSQL-Supabase-3ECF8E?style=for-the-badge&logo=postgresql&logoColor=0B1220)
![Recharts](https://img.shields.io/badge/Recharts-visuals-8B5CF6?style=for-the-badge)

</div>

---

## Overview

**Recovera** is a frontend command center for revenue leakage analytics. It connects to Supabase/Postgres materialized views, renders executive dashboards, and proxies AI copilot requests securely through Next.js API routes.

```txt
Dark-first UI · Arabic/RTL ready · Live database mode · Responsive charts · Secure server-side DB access
```

---

## Product surface

| Page | Purpose | Key visuals |
|---|---|---|
| **Overview** | System-wide revenue, profit, order volume, leakage, and vector corpus health | KPI cards, monthly revenue/leakage, scenario breakdown, AI health |
| **Risk** | Seller exposure and leakage concentration | Seller risk board, leakage rates, tier badges |
| **Vector RAG** | Corpus and retrieval system visibility | document/cache/guard metrics |
| **AI Monitor** | AI/RAG operations and query quality | latency, confidence, hallucination flags |
| **Marketing** | Channel and campaign attribution | ROAS by channel, campaign ranking, interactive filters |
| **Copilot** | Natural-language revenue investigation | structured answer cards, SQL, steps, dynamic charts |

---

## Architecture

```mermaid
flowchart LR
  U[User] --> UI[Next.js Frontend]

  UI --> DASH[/api/dashboard/]
  UI --> HEALTH[/api/health/]
  UI --> COPILOT[/api/copilot/]

  DASH --> PG[(Supabase Postgres)]
  HEALTH --> PG
  COPILOT --> API[AI / FastAPI Backend]

  PG --> MV[Materialized Views]
  MV --> UI
  API --> UI

  subgraph Database Schemas
    E[ecommerce]
    M[marketing]
    ML[ml_output]
    R[rag]
  end

  PG --> E
  PG --> M
  PG --> ML
  PG --> R
```

---

## Data flow

```mermaid
sequenceDiagram
  participant Browser
  participant NextAPI as Next.js API Routes
  participant DB as Supabase/Postgres
  participant AI as Copilot Backend

  Browser->>NextAPI: GET /api/dashboard
  NextAPI->>DB: Read analytics views
  DB-->>NextAPI: KPI + chart data
  NextAPI-->>Browser: live / partial / demo payload

  Browser->>NextAPI: POST /api/copilot
  NextAPI->>AI: session_id + message
  AI-->>NextAPI: answer + SQL + chart_data
  NextAPI-->>Browser: structured copilot response
```

---

## Database views used

```txt
ml_output.mv_leakage_dashboard
ml_output.mv_monthly_leakage
ml_output.mv_seller_risk
ml_output.mv_leakage_by_scenario
rag.documents
rag.retrieval_log
rag.retrieval_cache
rag.sql_guard
marketing.marketing_campaigns
marketing.campaign_attribution
marketing.website_sessions
```

If a query fails, the dashboard returns a safe `partial` payload with warnings instead of crashing.

---

## Tech stack

```txt
Next.js App Router
React + TypeScript
Recharts
Postgres pg client
Supabase Transaction Pooler
Server-side API proxying
CSS design system with dark mode + RTL support
```

---

## Quick start

```bash
npm install
cp .env.example .env.local
npm run dev
```

Open:

```bash
http://localhost:3000
```

Build check:

```bash
npm run typecheck
npm run build
```

---

## Environment variables

```env
# Supabase/Postgres — server-side only
DATABASE_URL=postgresql://postgres.PROJECT_REF:PASSWORD@aws-0-region.pooler.supabase.com:6543/postgres?sslmode=require
PG_SSL_REJECT_UNAUTHORIZED=false

# Copilot backend
CHAT_API_URL=https://your-backend-domain.com/api/chat
```

Supported database aliases:

```env
SUPABASE_DB_URL=
SUPABASE_POOLER_URL=
POSTGRES_URL=
DATABASE_POOL_URL=
DATABASE_POOL=
```

Never expose database credentials through `NEXT_PUBLIC_*`.

---

## Repository map

```txt
app/
  api/
    copilot/      AI backend proxy
    dashboard/    live dashboard payload
    health/       DB/API diagnostics
  globals.css     visual system, dark mode, RTL, responsive layout
  layout.tsx      app shell metadata
  page.tsx        full dashboard interface

lib/
  dashboard.ts    SQL mapping and live data transforms
  db.ts           Postgres pool + SSL handling
  fallback.ts     demo-safe fallback payloads
  types.ts        shared dashboard types
```

---

## Deployment

Deploy cleanly on Vercel or any Node 20 host.

```bash
npm run build
npm run start
```

Recommended production setup:

```txt
Node.js 20+
Supabase Transaction Pooler on port 6543
Read-only database role
Copilot backend behind HTTPS
Environment variables stored in hosting provider settings
```

---

## Health checks

```bash
/api/health
/api/dashboard
```

Dashboard source states:

| Source | Meaning |
|---|---|
| `live` | All dashboard queries succeeded |
| `partial` | Some live queries succeeded; warnings explain missing sections |
| `demo` | No live database data was available |

---

## Design notes

- Dark mode is the default experience.
- Arabic and RTL layouts are supported.
- Sidebar labels expand on hover while icons remain visible.
- Copilot responses render as compact business cards, charts, SQL, and pipeline steps.
- EGP is used across financial displays.

---

<div align="center">

**Recovera — recover · revenue · growth**

</div>
