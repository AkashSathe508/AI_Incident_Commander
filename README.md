# incident-commander

Real-time AI-powered incident response platform.

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, FastAPI, SQLAlchemy 2.x (async) |
| Database | PostgreSQL 16 + pgvector |
| Cache | Redis 7 |
| Frontend | React 18 + Vite + Tailwind CSS |
| Real-time Audio | Agora RTC SDK |
| Migrations | Alembic |
| Infra | Docker Compose |

## Quick Start

```bash
# 1. Copy env file and fill in secrets
cp .env.example .env

# 2. Start all services
docker-compose up -d

# 3. Apply migrations
docker-compose exec backend alembic upgrade head

# 4. Verify health
curl http://localhost:8000/health

# 5. Start frontend dev server
cd frontend
npm run dev
```

## Project Structure

```
incident-commander/
├── backend/
│   ├── app/
│   │   ├── api/          # REST route handlers
│   │   ├── ws/           # WebSocket handlers
│   │   ├── agent/        # LLM agent orchestration
│   │   ├── graph/        # LangGraph state machines
│   │   ├── ingestion/    # Audio/data ingestion
│   │   ├── reasoning/    # LLM reasoning chains
│   │   ├── temporal/     # Event ordering
│   │   ├── models/       # SQLAlchemy ORM (14 tables)
│   │   ├── services/     # Business logic
│   │   ├── workers/      # Background workers
│   │   └── config/       # Pydantic settings
│   ├── alembic/          # Migrations
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/             # React + Vite + Tailwind
├── docker-compose.yml
└── .env.example
```

## API

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **Health**: http://localhost:8000/health

## Database Schema

14 tables:
`meetings`, `participants`, `transcript_segments`, `facts`, `assumptions`,
`decisions`, `action_items`, `conflicts`, `timeline_events`, `risks`,
`evidence`, `embeddings` (pgvector 1536-dim), `agent_runs`, `graph_checkpoints`
