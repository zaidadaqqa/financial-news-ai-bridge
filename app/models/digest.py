"""Persistent state for the six-hour pinned digest (single-row table).

The digest subsystem owns exactly one permanent Telegram message per
channel: created once, pinned, then edited in place every six hours.
This table is the durable memory that makes that behavior restart-safe
and idempotent — it records WHICH message we own (``message_id``), the
last window we completed (the exactly-once authority), the last attempt,
and a fingerprint of the last rendered content.

Single-row design: the application serves one channel (settings
TELEGRAM_CHAT_ID), so the state is a singleton row with
``id = DIGEST_STATE_ID``. The repository enforces get-or-create on that
fixed id; the UNIQUE primary key makes concurrent creation race-safe.

No secrets are stored here: the Telegram message id and chat id are
ordinary non-secret metadata (message ids already live in ``news``);
tokens, API keys, and prompts never touch this table.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.news import utcnow

DIGEST_STATE_ID = 1


class DigestState(Base):
    __tablename__ = "digest_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[str] = mapped_column(String, nullable=False)
    message_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # Exactly-once authority: the start of the last window whose digest
    # was successfully published. A window is re-runnable until this is
    # advanced, and never re-run after (idempotent under double trigger).
    last_completed_window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_attempted_window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # sha256 hex of the last rendered digest text — observability plus a
    # cheap identical-content signal; correctness never depends on it
    # (Telegram's "message is not modified" is already treated as success).
    content_fingerprint: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
