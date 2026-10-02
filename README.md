# AI YouTube Automation Platform

Turn a single text prompt into a fully produced, YouTube-ready video:
script → scenes → visuals → narration → captions → rendered MP4 → thumbnail →
metadata → human approval → YouTube upload.

> **Status:** Phase 2 (FastAPI + PostgreSQL) complete. See [Roadmap](#roadmap) below.

## Architecture

```
                     ┌─────────────┐
   User Prompt  ───▶ │   FastAPI   │  REST API, validates request, creates Project row
                     └──────┬──────┘
                            │ enqueues job
                            ▼
                     ┌─────────────┐
                     │    Redis    │  broker + progress cache
                     └──────┬──────┘
                            ▼
                     ┌─────────────┐
                     │Celery Worker│  runs the pipeline stage-by-stage
                     └──────┬──────┘
        ┌───────────────────┼───────────────────────────────┐
        ▼                   ▼                                ▼
 AIProvider          VisualProvider                    TTSProvider
 (OpenAI/Groq/          (image gen)                    (Edge/11Labs/
  Gemini)                                                OpenAI TTS)
        │                   │                                │
        └───────────────────┴──────────────┬─────────────────┘
                                            ▼
                                   VideoRenderer (FFmpeg)
                                            ▼
                                   ThumbnailService
                                            ▼
                              PostgreSQL (projects, scenes,
                               assets, jobs, youtube_uploads,
                                    generation_logs)
                                            ▼
                                YouTubeService (OAuth2, Data API v3)
```

Every external capability (LLM, image gen, TTS, storage) sits behind an
interface (`AIProvider`, `VisualProvider`, `TTSProvider`, ...) so a provider
can be swapped via environment variables without touching business logic.

## Tech Stack

| Layer         | Choice                                   |
|---------------|-------------------------------------------|
| API           | FastAPI + Pydantic                        |
| DB            | PostgreSQL + SQLAlchemy + Alembic         |
| Jobs          | Celery + Redis                            |
| LLM           | OpenAI / Groq / Gemini (pluggable)        |
| Video         | FFmpeg                                    |
| TTS           | Edge TTS (MVP) → ElevenLabs/OpenAI later  |
| Frontend      | (Phase 10)                                |
| Orchestration | n8n (external automation, Phase 14)       |
| Containers    | Docker + docker-compose                   |

## Project Structure

```
ai-youtube-automation/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI entrypoint
│   │   ├── config.py          # all env vars, read once, typed
│   │   ├── database.py        # SQLAlchemy engine/session (Phase 2)
│   │   ├── api/                # route modules
│   │   ├── models/             # SQLAlchemy ORM models
│   │   ├── schemas/             # Pydantic request/response schemas
│   │   ├── services/           # business logic + provider abstractions
│   │   ├── workers/             # Celery tasks
│   │   └── utils/                # logger, ffmpeg helpers, file helpers
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                  # dashboard (Phase 10)
├── n8n/workflows/              # orchestration workflow exports (Phase 14)
├── assets/                    # generated images/audio/music/captions/videos
├── output/                     # final rendered MP4s
├── .env.example
├── docker-compose.yml
└── README.md
```

## Local Development

### Prerequisites
- Docker + Docker Compose
- (For running the backend outside Docker) Python 3.12+

### 1. Configure environment
```bash
cp .env.example .env
# then fill in OPENAI_API_KEY / GROQ_API_KEY / GEMINI_API_KEY, YOUTUBE_CLIENT_ID, etc.
```
`.env` is git-ignored — never commit real secrets.

### 2. Start everything with Docker
```bash
docker compose up --build
```
This starts PostgreSQL, Redis, the FastAPI backend (with hot reload), and a
Celery worker.

### 3. Verify it's alive
```bash
curl http://localhost:8000/api/health
```
Interactive API docs: http://localhost:8000/docs

### Running the backend without Docker (optional)
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```
You'll need a local PostgreSQL and Redis instance, with `DATABASE_URL` /
`REDIS_URL` in `.env` pointed at them.

## Database

Schema is managed with Alembic — never edit tables by hand.

Tables (matching section 6 of the spec): `projects`, `scenes`, `assets`, `jobs`,
`youtube_uploads`, `generation_logs`. Every child table cascades on delete —
deleting a project removes its scenes/assets/jobs/uploads/logs automatically.

**Migrations run automatically** when the `backend` container starts
(`entrypoint.sh` runs `alembic upgrade head` before launching uvicorn).

To create a new migration after changing a model:
```bash
docker compose exec backend alembic revision --autogenerate -m "describe the change"
docker compose exec backend alembic upgrade head
```

Or without Docker, from `backend/` with your venv active:
```bash
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

### API (Phase 2 endpoints)
| Method | Path | Description |
|---|---|---|
| POST | `/api/projects` | Create a project from a prompt (status: `CREATED`) |
| GET | `/api/projects` | List projects, most recent first |
| GET | `/api/projects/{id}` | Get one project |
| GET | `/api/projects/{id}/status` | Lightweight status for polling |
| DELETE | `/api/projects/{id}` | Delete a project and all related rows |

Pipeline-triggering endpoints (`/generate`, `/render`, `/approve`,
`/youtube/upload`, etc.) are added as their corresponding phase is built.

## Security Notes
- API keys and OAuth secrets live only in `.env` (git-ignored) and are read
  through `app/config.py` — never hardcoded, never logged.
- YouTube uses OAuth 2.0 only; no username/password automation.
- Default YouTube upload privacy is `private`; automatic public publishing
  requires explicitly setting `YOUTUBE_AUTO_PUBLISH=true`.

## Roadmap

| Phase | Scope                                   | Status |
|-------|------------------------------------------|--------|
| 1     | Project setup                            | ✅ Done |
| 2     | FastAPI + PostgreSQL                     | ✅ Done |
| 3     | Prompt → structured script (AIProvider)  | Next   |
| 4     | Script → scenes                          | Planned |
| 5     | Scene → images (VisualProvider)          | Planned |
| 6     | Scene → TTS (TTSProvider)                | Planned |
| 7     | FFmpeg renderer                          | Planned |
| 8     | Captions (SRT/VTT)                       | Planned |
| 9     | Thumbnail generation                     | Planned |
| 10    | Frontend dashboard                       | Planned |
| 11    | Background workers + Redis (Celery wiring)| Planned |
| 12    | YouTube OAuth                            | Planned |
| 13    | YouTube upload                           | Planned |
| 14    | n8n automation                           | Planned |
| 15    | Docker (hardening)                       | Planned |
| 16    | Testing                                  | Planned |
| 17    | Deployment                               | Planned |

## License
Use appropriately licensed / royalty-free assets only for background music
and stock visuals. This project does not source or bundle copyrighted media.
