# incident-commander

Real-time AI-powered incident response platform.

## Features

- **Real-time Live Audio Transcription**: Uses Agora RTC SDK and Deepgram STT (or Browser Web Speech API) to capture live meeting audio.
- **AI Agent Intelligence**: Continuously processes transcripts using Gemini 2.0 Flash or Groq Llama 3 to extract:
  - Verifiable Facts
  - Implicit Assumptions
  - Key Decisions
  - Action Items
  - Conflicts & Risks
- **Human-in-the-Loop Action Approvals**: The AI agent proposes integrations like Jira tickets, Slack posts, or PagerDuty pages. These stay pending until approved by a human operator in real-time.
- **Final Synthesis**: Generates a comprehensive executive summary once the meeting ends.

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, FastAPI, SQLAlchemy 2.x (async), LangGraph |
| Database | PostgreSQL 16 + pgvector |
| Cache | Redis 7 |
| Frontend | React 19 + Vite + Tailwind CSS |
| Real-time Audio | Agora RTC SDK |
| LLMs | Google Gemini 2.0 Flash, Groq |

## Quick Start

### 1. Environment Setup

Copy the example environment file and fill in your secrets:

```bash
cp .env.example .env
```

You must provide either `GEMINI_API_KEY` or `GROQ_API_KEY` for the AI processing to work.

### 2. Run with Docker Compose (Recommended)

Start the PostgreSQL, Redis, and FastAPI backend services:

```bash
docker-compose up -d --build
```

### 3. Database Migrations

Apply the Alembic migrations to set up the 14 database tables (including pgvector embeddings):

```bash
docker-compose exec backend alembic upgrade head
```

### 4. Start the Frontend

In a separate terminal, install dependencies and start the Vite dev server:

```bash
cd frontend
npm install
npm run dev
```

Navigate to `http://localhost:5173` to create or join an incident room.

## Project Structure

```
incident-commander/
├── backend/
│   ├── app/
│   │   ├── api/          # REST route handlers (Meetings, WS, Health)
│   │   ├── ws/           # WebSocket managers for live intel streaming
│   │   ├── agent/        # LLM agent orchestration & TTS
│   │   ├── graph/        # LangGraph state machine workflow
│   │   ├── ingestion/    # Real-time transcript ingestion & DB dedup
│   │   ├── reasoning/    # Core AI logic (Extraction, Synthesis, Approval Engine)
│   │   ├── temporal/     # Event ordering and timeline tracking
│   │   ├── models/       # SQLAlchemy ORM (14 tables)
│   │   ├── services/     # Core business logic
│   │   └── config/       # Pydantic settings parsing
│   ├── alembic/          # DB Migrations
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/             # React + Vite + Tailwind 
│   └── src/
│       ├── pages/        # CreateRoom, Room, Report
│       └── components/   # UI blocks and Report generation
├── docker-compose.yml
└── .env.example
```

## API Docs

When the backend is running, you can access the API documentation at:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## Database Schema

The platform relies on 14 PostgreSQL tables:
- Core: `meetings`, `participants`, `transcript_segments`
- Intelligence: `facts`, `assumptions`, `decisions`, `action_items`, `conflicts`, `risks`
- Chronology: `timeline_events`, `evidence`
- Machine Learning: `embeddings` (pgvector 1536-dim)
- State & Workflow: `agent_runs`, `graph_checkpoints`, `pending_approvals`
