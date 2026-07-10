# Financial News AI Bridge

A production-grade service that polls the FinancialJuice RSS feed, processes financial news with AI, and publishes professional Arabic translations and market analysis to a Telegram channel.

## Architecture

```
FinancialJuice RSS Feed
        │  (every 30s, ETag/If-None-Match)
        ▼
  RSS Poller (async)
        │  new items only (GUID deduplication)
        ▼
  Deduplication DB (SQLite, source_message_id unique)
        │
        ├─► Telegram (initial English message — fast path)
        │
        └─► AI Worker (background task)
                │  OpenAI gpt-4o-mini
                │  Structured JSON: Arabic translation + market analysis
                ▼
           Telegram (edit original message with Arabic version)
```

## Features

- **30-second RSS polling** — FinancialJuice official feed with ETag conditional requests
- **Cold-start protection** — On empty DB, silently marks existing feed items as seen (no flood)
- **Instant Telegram delivery** — Posts initial English message within seconds of new item
- **Professional Arabic translation** — GPT-4o-mini with structured JSON output
- **Market analysis** — Importance, market bias, affected assets, impact summary
- **Strict number preservation** — Validates all percentages, prices, and values are exact
- **Duplicate prevention** — GUID uniqueness constraint + content fingerprint hashing
- **Rate-limit handling** — 429 backoff (120s), ETag to avoid redundant transfers
- **Restart recovery** — Seeds seen IDs from DB on restart; resumes interrupted AI tasks
- **Graceful shutdown** — SIGTERM handling and clean asyncio task cancellation
- **Structured logging** — JSON logs, no secrets exposed
- **Health endpoint** — `GET /health` for uptime monitoring

## Technology Stack

| Component | Technology |
|-----------|------------|
| Runtime | Python 3.12 |
| Web framework | FastAPI + Uvicorn |
| RSS client | httpx (async, ETag/If-None-Match) |
| Telegram client | httpx (Telegram Bot API) |
| AI provider | OpenAI GPT-4o-mini |
| Database | SQLite + aiosqlite |
| ORM | SQLAlchemy 2.0 (async) |
| Migrations | Alembic |
| Logging | structlog (JSON) |
| Deployment | GCP e2-micro (Always Free) / Docker / systemd |

## Processing Pipeline

1. Start service — apply database migrations automatically
2. Seed seen GUIDs from database (empty DB → initial feed scan)
3. Poll RSS feed every 30 seconds (ETag conditional requests)
4. For each new GUID: insert `NewsEvent` row (RECEIVED)
5. Send initial English headline to Telegram → save `telegram_message_id`
6. Background task: call OpenAI with structured JSON schema
7. Validate AI output (required fields + number preservation)
8. Edit the same Telegram message with Arabic version + analysis
9. Mark database record as `PUBLISHED`

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | — | Telegram bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Yes | — | Target channel (`@handle` or numeric `-100...`) |
| `TELEGRAM_THREAD_ID` | No | — | Thread/topic ID for supergroups |
| `AI_PROVIDER` | Yes | `openai` | AI provider |
| `AI_MODEL` | Yes | `gpt-4o-mini` | Model name |
| `AI_API_KEY` | Yes | — | OpenAI API key |
| `AI_BASE_URL` | No | — | Override for OpenAI-compatible endpoint |
| `FJ_RSS_URL` | No | FinancialJuice RSS | Override RSS feed URL |
| `RSS_POLL_INTERVAL` | No | `30` | Seconds between polls |
| `DATABASE_URL` | No | `sqlite+aiosqlite:///data/news.db` | DB connection string |
| `LOG_LEVEL` | No | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `APP_ENV` | No | `development` | `production` enables stricter settings |

## Folder Structure

```
financial-news-ai-bridge/
├── app/
│   ├── api/health.py              # GET /health endpoint
│   ├── config/settings.py         # Pydantic v2 settings (env-based)
│   ├── constants/enums.py         # NewsStatus, NewsCategory, MarketBias
│   ├── database/connection.py     # Async SQLAlchemy engine + session factory
│   ├── log/logger.py              # structlog JSON configuration
│   ├── main.py                    # FastAPI app + lifespan + SIGTERM handler
│   ├── models/news.py             # NewsEvent SQLAlchemy model
│   ├── repositories/              # Database access layer (CRUD)
│   ├── services/
│   │   ├── ai/                    # OpenAI provider + structured output
│   │   ├── ingestion/rss_poller.py  # RSS polling loop (ETag, cold-start)
│   │   ├── formatting/            # Telegram HTML formatter
│   │   ├── news/orchestrator.py   # Main processing pipeline
│   │   ├── telegram/              # Telegram send + edit publisher
│   │   └── validation/            # AI output + number validator
│   └── utils/                     # Hashing, text normalization
├── alembic/                       # Database migrations
│   └── versions/                  # Migration files (run automatically at startup)
├── prompts/
│   ├── translator.txt             # AI system prompt (Arabic financial translation)
│   └── glossary.txt               # Financial terms Arabic glossary
├── scripts/
│   ├── backup_database.sh         # SQLite online backup (safe while running)
│   ├── restore_database.sh        # Restore from backup
│   ├── setup_gcp_vm.sh            # One-shot setup for GCP e2-micro VM
│   ├── update_gcp_vm.sh           # Pull latest + restart on GCP VM
│   └── update_vps.sh              # Pull latest + restart (user systemd variant)
├── tests/                         # Pytest test suite
├── .env.example                   # Environment variable template (no real values)
├── Dockerfile                     # Python 3.12-slim image
└── docker-compose.yml             # Local dev with named SQLite volume
```

## Local Setup

```bash
git clone https://github.com/zaidadaqqa/financial-news-ai-bridge.git
cd financial-news-ai-bridge

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your credentials

# Starts the service — migrations run automatically
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Deploy as a Permanent Local Service (systemd user)

```bash
# Install service unit
cp deploy/financial-news-ai-bridge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable financial-news-ai-bridge
systemctl --user start financial-news-ai-bridge
loginctl enable-linger   # keeps service alive after logout

# Check status
systemctl --user status financial-news-ai-bridge
journalctl --user -u financial-news-ai-bridge -f
```

## Deploy to Google Cloud (Always Free e2-micro)

This is the recommended cloud deployment. Google Cloud's e2-micro is genuinely always-on — no sleep, no idle reclamation, no forced restarts. Free tier includes 1 VM + 30 GB persistent disk in US regions.

### Step 1 — Create the VM (Google Cloud Console)

1. Go to [console.cloud.google.com](https://console.cloud.google.com) → Compute Engine → VM Instances
2. Click **Create Instance**
3. Set these values (everything else leave as default):

| Setting | Value |
|---------|-------|
| Name | `financial-news-bridge` |
| Region | `us-east1` (or `us-central1`, `us-west1`) |
| Zone | any in that region |
| Machine type | `e2-micro` |
| Boot disk OS | Debian 12 (Bookworm) |
| Boot disk size | 30 GB Standard |
| Firewall | leave unchecked (no inbound traffic needed) |

4. Click **Create**

> **Always-Free limit:** The e2-micro in a US region with a 30 GB standard disk costs $0/month within the always-free quota. Do not add SSD disks, GPUs, or additional VMs — those are charged.

### Step 2 — SSH into the VM

In the Google Cloud Console, click the **SSH** button next to your new VM. A browser terminal opens automatically (no SSH key setup required).

Or via gcloud CLI (if installed locally):
```bash
gcloud compute ssh financial-news-bridge --zone us-east1-b
```

### Step 3 — Run the setup script

```bash
curl -fsSL https://raw.githubusercontent.com/zaidadaqqa/financial-news-ai-bridge/main/scripts/setup_gcp_vm.sh | bash
```

The script will:
1. Install Python 3.12 and git
2. Clone this repository
3. Create virtual environment and install dependencies
4. Prompt for your credentials (Telegram token, OpenAI key, etc.)
5. Install and start the `financial-news-ai-bridge` systemd service
6. Verify the health endpoint

### Step 4 — Verify

```bash
# Service status
sudo systemctl status financial-news-ai-bridge

# Live logs
sudo journalctl -u financial-news-ai-bridge -f

# Health endpoint
curl http://127.0.0.1:8000/health
```

Within 30 seconds you should see RSS poll activity in the logs. New FinancialJuice items will appear in Telegram.

### Updating the deployment

```bash
bash ~/financial-news-ai-bridge/scripts/update_gcp_vm.sh
```

This pulls the latest code from `main`, installs any new dependencies, and restarts the service with zero downtime.

### Transitioning from local to cloud

Once the GCP service is confirmed working:

```bash
# On your local machine — stop the local service
systemctl --user stop financial-news-ai-bridge
systemctl --user disable financial-news-ai-bridge
```

The local SQLite DB and the cloud SQLite DB are independent. The GCP instance starts fresh but the cold-start protection ensures no duplicate publishing — it marks all current feed items as seen on first run.

## Docker Setup (local dev)

```bash
docker compose up -d --build
docker compose logs -f ai-bridge
docker compose restart ai-bridge
docker compose down
```

The SQLite database is stored in a named Docker volume (`db_data`) for persistence.

## Testing

```bash
pytest                    # run all tests
pytest -v                 # verbose
mypy app tests            # type check
ruff check .              # lint
black --check .           # format check
```

## Database and Migrations

Migrations run automatically at startup — no manual action required.

```bash
# Manual migration commands
alembic upgrade head      # apply all pending migrations
alembic current           # show current revision
alembic history           # show migration history

# If tables are accidentally dropped
alembic stamp base && alembic upgrade head
```

The database is **never deleted on startup**. Existing records are always preserved.

## Backup and Restore

```bash
# Backup (safe to run while service is running)
bash scripts/backup_database.sh

# Restore
bash scripts/restore_database.sh path/to/backup.db
```

Backups are written to `../financial-news-ai-bridge-backups/` and rotated after 7 days.

## Logging

Logs are structured JSON to stdout. Safe fields only — no tokens, API keys, or raw AI content.

Key events:
- `RSS poller started` — service up, initial seeding complete
- `RSS poll: new items found` — new headlines detected
- `Telegram message sent` — fast-path delivery
- `Telegram message edited` — AI translation applied
- `Successfully processed and published news`
- `RSS: rate limited (429), backing off` — 120s backoff active
- `Duplicate news detected by hash, skipping`

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `Telegram: chat not found` | Wrong `TELEGRAM_CHAT_ID` | Use numeric `-100...` ID for channels |
| RSS 429 errors on startup | Too many rapid restarts during testing | Normal — 120s backoff, self-resolves |
| No Telegram messages | Feed items all marked as seen (cold-start) | Wait for next new FinancialJuice item |
| `Missing required field` | AI response schema mismatch | Check `prompts/translator.txt` |
| `Number X missing from AI output` | AI dropped a numerical value | Record marked `AI_FAILED` automatically |
| Port 8000 already in use | Another process | Change `PORT` in `.env` |
| GCP VM shows extra charges | Wrong region or disk type | Must use `us-east1/central1/west1` + Standard disk |

## Security Notes

- Never commit `.env` to version control (excluded by `.gitignore`)
- Never commit `data/news.db` (excluded by `.gitignore`)
- `.env.example` contains only variable names, never real values
- Logs never output tokens, API keys, or sensitive content
- The Docker image does not copy `.env` or `data/` into the image
- The container runs as a non-root user (`appuser`)
- On GCP: `.env` is created with `chmod 600` (owner-read-only)
