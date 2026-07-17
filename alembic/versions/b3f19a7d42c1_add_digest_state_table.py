"""add_digest_state_table

Revision ID: b3f19a7d42c1
Revises: 87c34e0343ed
Create Date: 2026-07-17 00:00:00.000000

Six-hour pinned digest: one new single-row state table, purely additive.
- digest_state: the permanent digest message identity (chat_id +
  message_id), the exactly-once window authority
  (last_completed_window_start), the last attempt, and the last rendered
  content fingerprint. Restart safety and idempotency live here.
No existing table, column, or row is touched. Downgrade drops only the
new table. Design: .claude_memory/SIX_HOUR_DIGEST_ARCHITECTURE.md.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b3f19a7d42c1"
down_revision: str | None = "87c34e0343ed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "digest_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.String(), nullable=False),
        sa.Column("message_id", sa.String(), nullable=True),
        sa.Column(
            "last_completed_window_start", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "last_attempted_window_start", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_fingerprint", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("digest_state")
