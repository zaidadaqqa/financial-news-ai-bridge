# Operations Runbooks

Sanitized operational procedures for running Financial News AI Bridge in production. Host
names, IPs, and credentials are placeholders — real values live in the operator's private
notes and the VM's `.env` (never in git).

Conventions: `<vm>` = your production host alias; `$APP` = the application directory on the VM
(e.g. `~/financial-news-ai-bridge`); the systemd unit is `financial-news-ai-bridge`.

---

## 1. Local setup

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # test credentials only; never production secrets
pytest
```

## 2. Quality gates (run before every commit)

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/black --check .
.venv/bin/mypy app tests
git diff --check
```

CI runs the same gates plus a migration up/down/up round-trip and a Docker build on every push.
Never deploy on a red CI.

## 3. Migrations

- Always Alembic; always additive where possible.
- New migration: `alembic revision -m "describe the schema change"` → edit → test.
- **Rehearse on a production copy before deploying** (see §5 for taking a snapshot):
  ```bash
  DATABASE_URL=sqlite+aiosqlite:///$PWD/copy.db .venv/bin/python -m alembic upgrade head
  DATABASE_URL=sqlite+aiosqlite:///$PWD/copy.db .venv/bin/python -m alembic downgrade -1
  DATABASE_URL=sqlite+aiosqlite:///$PWD/copy.db .venv/bin/python -m alembic upgrade head
  ```
  Verify row counts of existing tables are preserved at every step.
- The app also runs `upgrade head` automatically at startup.

## 4. Deployment

Pre-flight: local tree clean · target commit pushed and on `origin/main` · CI green ·
production tree clean (`ssh <vm> "git -C $APP status --short"`).

```bash
ssh <vm> "bash $APP/scripts/update_gcp_vm.sh"
```

The script: backs up the DB → `git reset --hard origin/main` → `pip install` → `alembic
upgrade head` → restarts the service. Then verify:

```bash
ssh <vm> "git -C $APP rev-parse HEAD"                # equals the intended commit
ssh <vm> "curl -s http://127.0.0.1:8000/health"      # status ok, db ok
ssh <vm> "sudo journalctl -u financial-news-ai-bridge -n 50 --no-pager"  # no errors
ssh <vm> ".venv/bin/python $APP/scripts/ops_report.py"                    # full snapshot
```

Only ONE publisher may run: the production VM. Any local service must stay stopped and
disabled (`systemctl --user is-active financial-news-ai-bridge` → `inactive`).

## 5. Backups and snapshots

- Daily cron runs `scripts/backup_database.sh` (SQLite online backup — safe while running).
- The deploy script takes a backup before every deploy.
- Manual backup: `ssh <vm> "bash $APP/scripts/backup_database.sh"`.
- Consistent snapshot for rehearsal/analysis (read-only, safe while live):
  ```bash
  ssh <vm> "python3 - <<'EOF'
  import sqlite3
  src = sqlite3.connect('file:$APP/data/news.db?mode=ro', uri=True)
  dst = sqlite3.connect('/tmp/snapshot.db'); src.backup(dst)
  EOF"
  scp <vm>:/tmp/snapshot.db ./copy.db && ssh <vm> rm /tmp/snapshot.db
  ```

## 6. Rollback

1. Identify the last known-good commit (see the project changelog / `git log`).
2. `ssh <vm> "git -C $APP fetch origin && git -C $APP reset --hard <good-commit>"`
3. If the bad deploy added a migration: `alembic downgrade -1` **only after confirming** the
   downgrade drops exclusively the new objects (rehearse on the pre-deploy backup first).
4. Restore the pre-deploy backup ONLY if database integrity itself is in question:
   `bash scripts/restore_database.sh <backup-file>` (stops service, restores, restarts).
5. `sudo systemctl restart financial-news-ai-bridge` → verify §4's checklist.
6. Document the incident honestly in the changelog: what broke, what was rolled back, why.

Never delete production records to make a deployment look clean.

## 7. Incident: Telegram outage / send failures

Symptoms: `Failed to publish initial telegram message` errors; items marked `FAILED`.

1. Check Telegram Bot API reachability from the VM:
   `curl -s https://api.telegram.org` (connectivity) — do NOT paste the bot token into shells
   or logs.
2. Check for `chat not found` (wrong `TELEGRAM_CHAT_ID`) vs. 5xx (Telegram-side outage).
3. Telegram-side outages self-heal; new items publish when the API recovers. Items lost
   during the outage are NOT auto-resent (deliberate — prevents stale-news floods). Leaving
   them `FAILED` is the accepted behavior; a manual replay needs an explicit decision.
4. If the bot token leaked or was revoked: rotate via @BotFather, update `.env` on the VM
   (`nano $APP/.env`), restart the service. Never commit the token anywhere.

## 8. Incident: OpenAI outage / AI failures

Symptoms: items stuck briefly at `AI_PENDING` then `AI_FAILED`; `AI validation/generation
failed` in logs.

- The initial English message is ALREADY published (send-then-edit) — readers are not blind.
- Transient 429/5xx are retried 3× with backoff automatically; sustained outage marks items
  `AI_FAILED` and the pipeline continues with new items.
- Known benign cause: the number-preservation validator misreads date ranges like
  "July 1-10" as the number "-10" — such items fail AI validation by design (never publish
  unverified numbers). No action needed.
- No automatic retry of old `AI_FAILED` records exists; replaying them requires an explicit
  decision (they would edit old messages).

## 9. Incident: RSS rate limiting (HTTP 429)

Symptoms: `RSS: rate limited (429)` log lines; slower item flow.

- Automatic: 120 s backoff per 429, then normal polling resumes. No data is lost — the next
  successful poll picks up everything still in the feed window.
- Persistent aggressive limiting (new VM IP): expected to normalize within 24–48 h. If it
  doesn't, consider raising `RSS_POLL_INTERVAL` in `.env` (e.g. 90 → 120 s) and restart.

## 10. Incident: database corruption

Symptoms: `db_status: error` in `/health`; `database disk image is malformed` in logs.

1. Stop the service: `sudo systemctl stop financial-news-ai-bridge`.
2. Preserve the damaged file: `cp $APP/data/news.db /tmp/corrupt-$(date +%s).db`.
3. Integrity check: `sqlite3 $APP/data/news.db 'PRAGMA integrity_check;'`.
4. If corrupt: restore the newest backup via `scripts/restore_database.sh` (verify its
   integrity first: `PRAGMA integrity_check` on the backup).
5. Restart, then verify: health OK, poller re-seeds seen-IDs from the DB (log line
   `RSS poller seeded seen IDs`), and the duplicate check in `ops_report.py` passes.
6. Items published after the backup was taken exist in Telegram but not in the DB — the
   in-memory GUID seed prevents most re-publishes within the feed window; accept the small
   residual risk or leave the service stopped until the feed window rolls past.

## 11. Incident: duplicate publication

Should be impossible (three layers). If it happens:

1. Capture evidence: both Telegram message IDs, both DB rows (`source_message_id`, `hash`).
2. Determine the breached layer: same GUID twice (DB constraint should have blocked) vs.
   different GUIDs same content (hash layer should have blocked).
3. Check for TWO publishers running (the classic cause):
   local `systemctl --user is-active financial-news-ai-bridge` must be `inactive`; exactly one
   VM service active.
4. Delete nothing. Fix the root cause; the extra Telegram message may be manually deleted in
   the channel if editorially necessary.

## 12. Incident: Story Intelligence malfunction

Symptoms: wrong «تطور سابق» context, or story-engine warnings in logs.

- A story-engine failure NEVER blocks publication (isolated try/except) — items degrade to
  no-story behavior. `Story intelligence failed` warnings are therefore urgent only if
  constant.
- Wrong story linkage (false merge): capture both headlines + the log's evidence scores;
  matching is conservative by design (uncertainty never links) — a confirmed false merge is a
  vocabulary/threshold defect: file it with evidence, do not hot-patch thresholds in
  production.
- Stories are restart-safe; no recovery procedure is needed after crashes.

## 13. Incident: Indicator Memory / Macro Context malfunction

Symptoms: `Indicator memory failed` / `Macro context failed` warnings.

- Both are isolated: items still publish identically; the cost is a missed history record or
  a missing context line. Occasional warnings = log and monitor; constant warnings = treat as
  a defect.
- Never hand-edit `indicator_series` / `indicator_prints`. Unkeyed prints are intentional
  (honesty over coverage); re-keying them requires a reviewed deterministic rule + a bounded
  script.
- The backfill script (`scripts/backfill_indicator_memory.py`) is idempotent and refuses the
  live DB without `--confirm-production`; always rehearse on a snapshot (§5) first, always
  back up before a production run.
- Macro context emitting nothing is usually CORRECT (evidence gates: ≥3 prints for streaks,
  ≥6 for extremes) — check `ops_report.py`'s "series at macro gate" line before suspecting a
  bug.

## 14. Incident: market-data outage

Not applicable — no market-data integration exists (externally gated pending a licensed
provider; see the README roadmap). Any message text claiming a market reaction would be a
severe defect: capture it and treat as an incident (the pipeline is designed to make this
impossible).

## 15. Routine health check

```bash
ssh <vm> ".venv/bin/python $APP/scripts/ops_report.py"
```

Green looks like: service active/enabled · health `ok` · duplicate check OK · orphan links 0 ·
warnings/errors ~0 · latest backup < 26 h old. Anything else → the matching runbook above.
