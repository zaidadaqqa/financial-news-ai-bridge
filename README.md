# Financial News AI Bridge

A production-grade service that polls the FinancialJuice RSS feed, processes financial news with AI, and publishes professional Arabic translations and market analysis to a Telegram channel — running continuously on Oracle Cloud Always Free.

## Architecture

```
FinancialJuice RSS
       │  poll every 30s
       ▼
  RSS Poller ──► Deduplication (SQLite) ──► Telegram (initial English)
                                         ──► OpenAI GPT-4o-mini (Arabic + analysis)
                                         ──► Telegram (edit with Arabic version)
```

**Processing pipeline:**
1. Poll RSS feed every 30 seconds with ETag/Last-Modified caching
2. Filter already-seen GUIDs (in-memory set seeded from DB on startup)
3. Cold-start protection: on empty DB, silently mark existing items as seen
4. Store news record (status: `RECEIVED`)
5. Send initial English headline to Telegram
6. Call OpenAI with structured JSON schema for Arabic translation + analysis
7. Validate AI output (required fields, number preservation)
8. Edit the Telegram message with the Arabic version
9. Mark record as `PUBLISHED`

## Technology Stack

| Component | Technology |
|-----------|------------|
| Runtime | Python 3.12 |
| Web framework | FastAPI + Uvicorn |
| News source | FinancialJuice RSS (free, no auth) |
| Telegram client | httpx (direct Bot API) |
| AI provider | OpenAI GPT-4o-mini |
| Database | SQLite + aiosqlite |
| ORM | SQLAlchemy 2.0 (async) |
| Migrations | Alembic (auto-run at startup) |
| Retries | tenacity (exponential backoff) |
| Logging | structlog (JSON) |
| Deployment | Docker Compose on Oracle Cloud Always Free |

## Environment Variables

Copy `.env.example` to `.env` and fill in your values.

| Variable | Required | Description |
|----------|----------|-------------|
| `FJ_RSS_URL` | No | FinancialJuice RSS URL (default: official feed) |
| `RSS_POLL_INTERVAL` | No | Seconds between polls (default: 30) |
| `TELEGRAM_BOT_TOKEN` | Yes | Telegram bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Yes | Target channel/group (e.g. `@mychannel`) |
| `TELEGRAM_THREAD_ID` | No | Thread/topic ID for supergroups |
| `AI_PROVIDER` | No | `openai` (default) |
| `AI_MODEL` | No | `gpt-4o-mini` (default) |
| `AI_API_KEY` | Yes | OpenAI API key |
| `AI_BASE_URL` | No | Override for OpenAI-compatible endpoint |
| `DATABASE_URL` | No | `sqlite+aiosqlite:///data/news.db` (default) |
| `APP_ENV` | No | `production` |
| `LOG_LEVEL` | No | `INFO` (default) |
| `PORT` | No | `8000` (default) |

## Local Setup

```bash
git clone https://github.com/zaidadaqqa/financial-news-ai-bridge.git
cd financial-news-ai-bridge

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, AI_API_KEY

python -m app.main
```

## Docker (local)

```bash
cp .env.example .env
# Edit .env with your credentials

docker compose up -d --build
docker compose logs -f ai-bridge

# Health check
curl http://localhost:8000/health
```

The SQLite database is stored in the `db_data` Docker volume and survives container restarts.

## Cloud Deployment: Oracle Cloud Always Free

This service runs permanently on Oracle Cloud Always Free (ARM Ampere A1 instance).
No credit card charges — the Always Free tier is permanently free.

### Why Oracle Cloud

| Platform | Free Worker | Persistent Storage | 24/7 | Verdict |
|----------|-------------|-------------------|------|---------|
| Oracle Cloud Always Free | Yes (full VM) | 200 GB block storage | Yes | **Best** |
| Google Cloud e2-micro | Yes (full VM) | 30 GB disk | Yes | Backup |
| Fly.io | No free tier (2026) | — | — | No |
| Render | Workers not free | — | Sleeps | No |
| Railway | Trial only ($5) | — | — | No |
| Koyeb | Workers excluded | — | Sleeps | No |

Oracle Always Free specs (as of June 2026): 2 OCPU + 12 GB RAM, 200 GB block storage.

### First-Time VM Setup

**Step 1 — Create Oracle Cloud account**
- Go to [cloud.oracle.com/free](https://cloud.oracle.com/free)
- Credit card required for identity verification (not charged on Always Free)
- Choose Frankfurt (`eu-frankfurt-1`) or Singapore (`ap-singapore-1`) for best ARM availability
- Select "Always Free" during signup

**Step 2 — Provision ARM VM**
- Compute → Instances → Create Instance
- Image: Ubuntu 22.04 (Canonical)
- Shape: VM.Standard.A1.Flex → 2 OCPU, 12 GB RAM
- Add your SSH public key
- Create instance

**Step 3 — SSH and run setup**
```bash
# SSH into the VM
ssh ubuntu@<your-vm-ip>

# Run setup script
curl -fsSL https://raw.githubusercontent.com/zaidadaqqa/financial-news-ai-bridge/main/scripts/setup_oracle_vm.sh | bash
```

If Docker was just installed, log out and log back in, then run the script again.

**Step 4 — Copy secrets to VM**
```bash
# From your local machine — NEVER put secrets in git
scp .env ubuntu@<your-vm-ip>:/opt/financial-news-ai-bridge/.env
```

**Step 5 — Start the service**
```bash
# On the VM
cd /opt/financial-news-ai-bridge
docker compose up -d --build

# Verify
curl http://localhost:8000/health
```

Expected response:
```json
{
  "status": "ok",
  "service": "Financial News AI Bridge",
  "uptime_seconds": "42",
  "db_status": "ok",
  "last_rss_poll": "never"
}
```

After the first poll cycle (30 seconds), `last_rss_poll` shows a timestamp.

### Automatic Updates

After a git push to main, pull updates on the VM:

```bash
ssh ubuntu@<your-vm-ip> bash /opt/financial-news-ai-bridge/scripts/update_oracle.sh
```

Or enable automated deploys via GitHub Actions (optional):
1. In GitHub → Settings → Variables: add `ORACLE_DEPLOY_ENABLED = true`
2. In GitHub → Settings → Secrets: add `ORACLE_SSH_HOST`, `ORACLE_SSH_USER`, `ORACLE_SSH_KEY`
3. Every push to `main` will automatically SSH into the VM and run the update script

### Oracle Cloud Networking

Oracle's VCN blocks all ports by default. Configure ingress rules:
- VCN → Security Lists → Default Security List → Add Ingress Rule
- Source CIDR: `0.0.0.0/0`, Protocol: TCP, Port: `22` (SSH)
- Port 8000 is **not** exposed externally — health checks run from localhost only

### Common Oracle Issues

**"Out of host capacity" when creating ARM VM**
- Switch to Frankfurt or Singapore region
- Retry a few minutes later — capacity opens as other users release instances

**Account signup rejected**
- Use a physical credit card (not prepaid/virtual)
- Try Frankfurt or Ashburn as your home region

## Transition from Local Service

Both the local systemd service and Oracle Cloud can run simultaneously — their SQLite databases are independent, and each will publish news to Telegram. To stop the local service after Oracle Cloud is verified:

```bash
systemctl --user stop financial-news-ai-bridge
systemctl --user disable financial-news-ai-bridge
```

## Testing

```bash
pytest                  # Run all tests
pytest -v               # Verbose output
mypy app tests          # Type check
black --check .         # Format check
ruff check .            # Lint
```

## Folder Structure

```
financial-news-ai-bridge/
├── app/
│   ├── api/health.py              # /health endpoint (status, db, last poll)
│   ├── config/settings.py         # Pydantic settings
│   ├── database/connection.py     # Async SQLAlchemy engine
│   ├── main.py                    # FastAPI app + lifespan
│   ├── models/news.py             # SQLAlchemy models
│   └── services/
│       ├── ai/                    # OpenAI provider
│       ├── ingestion/rss_poller.py # RSS polling + deduplication
│       ├── formatting/            # Telegram HTML formatter
│       ├── news/orchestrator.py   # Processing pipeline
│       ├── telegram/              # Telegram publisher
│       └── validation/            # AI output validator
├── alembic/                       # Database migrations
├── prompts/
│   ├── translator.txt             # AI system prompt
│   └── glossary.txt               # Financial Arabic glossary
├── tests/
├── scripts/
│   ├── setup_oracle_vm.sh         # First-time Oracle Cloud VM setup
│   ├── update_oracle.sh           # Pull updates on Oracle VM
│   ├── backup_database.sh         # SQLite online backup
│   └── restore_database.sh        # Restore from backup
├── .env.example                   # Environment variable template
├── Dockerfile                     # Multi-arch Python 3.12 image
└── docker-compose.yml             # Compose with persistent volume
```

## Health Check

`GET /health` returns:

```json
{
  "status": "ok",
  "service": "Financial News AI Bridge",
  "uptime_seconds": "3600",
  "db_status": "ok",
  "last_rss_poll": "2026-07-10T12:00:00+00:00"
}
```

## Security Notes

- Never commit `.env` to version control (protected by `.gitignore`)
- Never commit `data/news.db` (protected by `.gitignore`)
- `.env.example` contains only placeholder values — no real credentials
- Logs never output tokens, API keys, or sensitive response content
- The Docker image does not copy `.env` into the image layer
- Container runs as non-root user (`appuser`)

## Logging

Key log events:

```
RSS poller started           — startup successful
RSS: initial scan complete   — cold-start protection set, no flood
RSS poll: new items found    — items being processed
Telegram message sent        — initial English message published
Telegram message edited      — Arabic version published
Duplicate news detected      — skipping already-seen item
RSS: rate limited (429)      — 120s backoff applied
```

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `Telegram: chat not found` | Wrong `TELEGRAM_CHAT_ID` | Use numeric ID (`-100...`) or `@handle` |
| `Missing required field` | AI response schema mismatch | Check `prompts/translator.txt` |
| `Number X missing from AI output` | AI dropped a value | Record marked `AI_FAILED` automatically |
| `RSS: rate limited (429)` | Too many requests | 120s backoff applied automatically |
| `db_status: error` in /health | DB file permissions | Check `data/` directory ownership |
| Port 8000 unreachable externally | Oracle VCN security list | Keep port 8000 localhost-only; access via SSH tunnel |
