# Financial News AI Bridge

Financial News AI Bridge listens for financial news in Discord, enriches and translates each item with an AI provider, deduplicates repeated headlines, and publishes formatted alerts to Telegram.

## Features

- Discord message ingestion with channel and guild checks.
- AI-powered Arabic translation, summarization, classification, and market impact metadata.
- Duplicate detection using normalized headline hashes.
- Telegram publishing with retry handling.
- FastAPI health endpoint for deployment checks.
- Async SQLAlchemy storage with Alembic migrations.
- Docker and GitHub Actions CI support.

## Requirements

- Python 3.13
- Docker, optional but recommended for deployment parity
- Discord bot token with Message Content Intent enabled
- Telegram bot token and destination chat/channel
- AI provider API key, currently OpenAI-compatible by default

## Configuration

Copy the example file and fill in local values:

```bash
cp .env.example .env
```

Never commit `.env`. Only `.env.example` belongs in Git.

Required environment variables:

| Variable | Description |
| --- | --- |
| `DISCORD_BOT_TOKEN` | Discord bot token. |
| `DISCORD_GUILD_ID` | Discord server ID to monitor. |
| `DISCORD_SOURCE_CHANNEL_ID` | Discord channel ID to read from. |
| `DISCORD_APPLICATION_ID` | Discord application ID, optional for some flows. |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token. |
| `TELEGRAM_CHAT_ID` | Telegram target chat ID, channel username, or numeric ID. |
| `AI_PROVIDER` | AI provider name. Use `openai` unless adding another provider. |
| `AI_MODEL` | Model name, for example `gpt-4o-mini`. |
| `AI_API_KEY` | AI provider API key. |
| `AI_BASE_URL` | Optional OpenAI-compatible base URL. Leave blank for OpenAI. |
| `DATABASE_URL` | SQLAlchemy database URL. Defaults to SQLite. |
| `APP_ENV` | Runtime environment, for example `development` or `production`. |
| `LOG_LEVEL` | Logging level, for example `INFO` or `DEBUG`. |
| `TIMEZONE` | Application timezone string. |

Optional feature flags are supported by settings and default to enabled:

- `ENABLE_TRANSLATION`
- `ENABLE_AI_CACHE`
- `ENABLE_MARKET_IMPACT`
- `ENABLE_DUPLICATE_DETECTION`

## Local Development

Install dependencies:

```bash
python -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Run the service:

```bash
python -m app.main
```

Health check:

```bash
curl http://localhost:8000/health
```

## Quality Checks

Run the same checks used by CI:

```bash
black --check .
ruff check .
mypy app tests
pytest
docker build -t financial-news-ai-bridge:local .
```

Use `black .` and `ruff check . --fix` for formatting and safe lint fixes during development.

## Docker

Build and run with Docker Compose:

```bash
cp .env.example .env
# edit .env with real local values
docker compose up --build -d
```

View logs:

```bash
docker compose logs -f ai-bridge
```

Stop the service:

```bash
docker compose down
```

The compose file mounts `./data` to persist the SQLite database locally. For managed production hosting, prefer a managed PostgreSQL database or a persistent disk for SQLite.

## Deployment

1. Build from the Dockerfile or deploy this repository to a Docker-capable host.
2. Add the required environment variables in the hosting platform's secrets/settings UI.
3. Ensure the app exposes port `8000`.
4. Configure the health check path as `/health`.
5. Use persistent storage for `/app/data` if `DATABASE_URL` points to SQLite.
6. For PostgreSQL, set `DATABASE_URL` to an async SQLAlchemy URL and include the required driver dependency before deploying.

Hosting secrets to add:

```text
DISCORD_BOT_TOKEN
DISCORD_GUILD_ID
DISCORD_SOURCE_CHANNEL_ID
DISCORD_APPLICATION_ID
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
AI_PROVIDER
AI_MODEL
AI_API_KEY
AI_BASE_URL
DATABASE_URL
APP_ENV
LOG_LEVEL
TIMEZONE
ENABLE_TRANSLATION
ENABLE_AI_CACHE
ENABLE_MARKET_IMPACT
ENABLE_DUPLICATE_DETECTION
```

Do not place real secret values in GitHub Actions workflow files, Dockerfiles, Compose files, or documentation.

## GitHub Actions

The CI workflow runs on pushes and pull requests to `main`:

- Install dependencies
- `black --check .`
- `ruff check .`
- `mypy app tests`
- `pytest`
- Docker image build

## License

This project is released under the MIT License. See [LICENSE](LICENSE).
