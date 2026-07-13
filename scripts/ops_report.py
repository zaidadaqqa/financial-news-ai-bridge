#!/usr/bin/env python3
"""Operations report (Phase 6) — one read-only snapshot of everything an
operator needs, printed to stdout. No secrets, no writes, no new endpoints.

Run on the production VM (or any host with the repo + database):

    .venv/bin/python scripts/ops_report.py            # default data/news.db
    .venv/bin/python scripts/ops_report.py --db PATH  # any copy

Sections degrade gracefully: journal/systemd/health checks report
"unavailable" instead of failing when run off-host.
"""

import argparse
import json
import sqlite3
import subprocess
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

SERVICE = "financial-news-ai-bridge"
MACRO_STREAK_GATE = 3  # keep in sync with app/services/indicators/context.py


def _run(cmd: list[str]) -> str:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10, check=False
        )
        return (out.stdout or out.stderr).strip() or "(no output)"
    except Exception as err:  # noqa: BLE001 - report, never crash
        return f"unavailable ({type(err).__name__})"


def _health() -> str:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5) as r:
            return json.dumps(json.loads(r.read()))
    except Exception as err:  # noqa: BLE001
        return f"unavailable ({type(err).__name__})"


def report(db_path: str) -> int:
    now = datetime.now(UTC)
    print(f"=== OPS REPORT — {now:%Y-%m-%d %H:%M:%S} UTC ===\n")

    print("--- Code / service ---")
    print(f"deployed commit : {_run(['git', 'rev-parse', 'HEAD'])}")
    dirty = _run(["git", "status", "--short"])
    print(f"working tree    : {'clean' if dirty in ('', '(no output)') else dirty}")
    print(f"systemd active  : {_run(['systemctl', 'is-active', SERVICE])}")
    print(f"systemd enabled : {_run(['systemctl', 'is-enabled', SERVICE])}")
    print(f"health endpoint : {_health()}")

    if not Path(db_path).exists():
        print(f"\nDATABASE NOT FOUND: {db_path}")
        return 1
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = conn.cursor()

    print("\n--- Database ---")
    cur.execute("SELECT version_num FROM alembic_version")
    print(f"migration head  : {cur.fetchone()[0]}")
    cur.execute("SELECT status, COUNT(*) FROM news GROUP BY status ORDER BY 2 DESC")
    for status, count in cur.fetchall():
        print(f"  news {status:<16}: {count}")
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT source_message_id) FROM news")
    total, distinct = cur.fetchone()
    dup_note = "OK" if total == distinct else "DUPLICATES PRESENT — INVESTIGATE"
    print(f"duplicate check : {total} rows / {distinct} distinct GUIDs → {dup_note}")
    cur.execute(
        "SELECT COUNT(*) FROM news WHERE created_at > datetime('now', '-1 day')"
    )
    print(f"items last 24h  : {cur.fetchone()[0]}")
    cur.execute("SELECT MAX(created_at) FROM news")
    print(f"latest item     : {cur.fetchone()[0]}")

    print("\n--- Story intelligence ---")
    cur.execute("SELECT COUNT(*) FROM stories")
    print(f"stories         : {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM story_news")
    print(f"story links     : {cur.fetchone()[0]}")
    cur.execute(
        "SELECT COUNT(*) FROM story_news sn "
        "LEFT JOIN news n ON n.id = sn.news_id WHERE n.id IS NULL"
    )
    orphans = cur.fetchone()[0]
    print(f"orphan links    : {orphans}{'' if orphans == 0 else '  ← INVESTIGATE'}")

    print("\n--- Indicator memory / macro context ---")
    cur.execute("SELECT COUNT(*) FROM indicator_series")
    print(f"series          : {cur.fetchone()[0]}")
    cur.execute(
        "SELECT COUNT(*), SUM(CASE WHEN series_id IS NULL THEN 1 ELSE 0 END) "
        "FROM indicator_prints"
    )
    prints, unkeyed = cur.fetchone()
    unkeyed = unkeyed or 0
    print(f"prints          : {prints} (keyed {prints - unkeyed}, unkeyed {unkeyed})")
    cur.execute(
        "SELECT unkeyed_reason, COUNT(*) FROM indicator_prints "
        "WHERE series_id IS NULL GROUP BY unkeyed_reason"
    )
    print(f"unkeyed reasons : {dict(cur.fetchall())}")
    cur.execute(
        "SELECT canonical_key, print_count FROM indicator_series "
        "ORDER BY print_count DESC LIMIT 5"
    )
    print("deepest series  :")
    for key, count in cur.fetchall():
        gate = " (macro-eligible)" if count >= MACRO_STREAK_GATE else ""
        print(f"  {count:>3}  {key}{gate}")
    cur.execute(
        "SELECT COUNT(*) FROM indicator_series WHERE print_count >= ?",
        (MACRO_STREAK_GATE,),
    )
    print(f"series at macro gate (≥{MACRO_STREAK_GATE}): {cur.fetchone()[0]}")

    print("\n--- Market data ---")
    print(
        "status          : not implemented (Phase 5 externally blocked — "
        "awaiting owner data-source decision)"
    )

    print("\n--- Logs (last 24h; needs journal access) ---")

    def _count(grep_output: str) -> str:
        if "unavailable" in grep_output:
            return grep_output
        if grep_output == "(no output)":
            return "0"
        return str(len(grep_output.splitlines()))

    warn = _run(
        [
            "journalctl",
            "-u",
            SERVICE,
            "--since",
            "-24h",
            "-q",
            "--no-pager",
            "-g",
            '"level": "warning"',
        ]
    )
    err = _run(
        [
            "journalctl",
            "-u",
            SERVICE,
            "--since",
            "-24h",
            "-q",
            "--no-pager",
            "-g",
            '"level": "error"',
        ]
    )
    print(f"warnings        : {_count(warn)}")
    print(f"errors          : {_count(err)}")

    print("\n--- Backups ---")
    backup_dir = Path.home() / f"{SERVICE}-backups"
    if backup_dir.exists():
        backups = sorted(backup_dir.glob("news_*.db"))
        if backups:
            latest = backups[-1]
            age_h = (now.timestamp() - latest.stat().st_mtime) / 3600
            print(
                f"latest backup   : {latest.name} "
                f"({latest.stat().st_size // 1024}K, {age_h:.1f}h old)"
            )
            print(f"backup count    : {len(backups)}")
        else:
            print("latest backup   : NONE FOUND — INVESTIGATE")
    else:
        print(f"backup dir      : {backup_dir} not present on this host")

    conn.close()
    print("\n=== END OPS REPORT ===")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/news.db")
    args = parser.parse_args()
    return report(args.db)


if __name__ == "__main__":
    raise SystemExit(main())
