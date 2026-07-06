# Contributing

Thank you for improving Financial News AI Bridge. Keep changes focused and avoid committing local runtime state or secrets.

## Development Setup

```bash
python -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Fill `.env` with local test credentials only. Do not commit it.

## Checks

Run before opening a pull request:

```bash
black --check .
ruff check .
mypy app tests
pytest
docker build -t financial-news-ai-bridge:local .
```

Formatting fixes can be applied with:

```bash
black .
ruff check . --fix
```

## Pull Requests

1. Create a branch from `main`.
2. Keep the change scoped to one concern.
3. Add or update tests when behavior changes.
4. Update documentation when configuration or deployment changes.
5. Confirm no secrets, `.env` files, caches, logs, virtual environments, or database files are staged.
