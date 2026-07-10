"""rss_ingestion_rename_source_fields

Revision ID: c3a1e8f92b40
Revises: f4cc6d59d550
Create Date: 2026-07-10 10:00:00.000000

Migrate from Discord-specific schema to source-agnostic schema for RSS ingestion.
- Rename discord_message_id → source_message_id
- Rename unique constraint accordingly
- Add source column (defaults to 'rss')
- Make source_channel_id nullable (no channel concept in RSS)
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3a1e8f92b40"
down_revision: str | None = "f4cc6d59d550"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("news") as batch_op:
        batch_op.alter_column(
            "discord_message_id",
            new_column_name="source_message_id",
            existing_type=sa.String(),
            nullable=False,
        )
        batch_op.add_column(
            sa.Column("source", sa.String(), nullable=False, server_default="rss")
        )
        batch_op.alter_column(
            "source_channel_id",
            existing_type=sa.String(),
            nullable=True,
        )
        batch_op.drop_constraint("uq_news_discord_message_id", type_="unique")
        batch_op.create_unique_constraint(
            "uq_news_source_message_id", ["source_message_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("news") as batch_op:
        batch_op.drop_constraint("uq_news_source_message_id", type_="unique")
        batch_op.alter_column(
            "source_channel_id",
            existing_type=sa.String(),
            nullable=False,
        )
        batch_op.drop_column("source")
        batch_op.alter_column(
            "source_message_id",
            new_column_name="discord_message_id",
            existing_type=sa.String(),
            nullable=False,
        )
        batch_op.create_unique_constraint(
            "uq_news_discord_message_id", ["discord_message_id"]
        )
