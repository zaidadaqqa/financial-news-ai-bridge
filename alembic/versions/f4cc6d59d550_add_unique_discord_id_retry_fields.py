"""add_unique_discord_id_retry_fields

Revision ID: f4cc6d59d550
Revises: a651b1de9b92
Create Date: 2026-07-10 04:24:16.484931

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f4cc6d59d550"
down_revision: str | None = "a651b1de9b92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("news") as batch_op:
        batch_op.add_column(
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("last_error", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_news_discord_message_id", ["discord_message_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("news") as batch_op:
        batch_op.drop_constraint("uq_news_discord_message_id", type_="unique")
        batch_op.drop_column("last_error_at")
        batch_op.drop_column("last_error")
        batch_op.drop_column("retry_count")
