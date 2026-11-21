"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "urls",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("short_code", sa.String(32), nullable=False),
        sa.Column("original_url", sa.String(2048), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", TS, nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("total_clicks", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint("char_length(short_code) BETWEEN 3 AND 32", name="ck_urls_short_code_length"),
        sa.CheckConstraint("total_clicks >= 0", name="ck_urls_total_clicks_non_negative"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_urls_owner_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_urls"),
        sa.UniqueConstraint("short_code", name="uq_urls_short_code"),
    )
    op.create_index("ix_urls_owner_id_id", "urls", ["owner_id", "id"])
    op.create_index(
        "ix_urls_expires_at", "urls", ["expires_at"], postgresql_where=sa.text("expires_at IS NOT NULL")
    )

    op.create_table(
        "click_events",
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("short_code", sa.String(32), nullable=False),
        sa.Column("timestamp", TS, nullable=False),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.Column("referrer", sa.String(512), nullable=True),
        sa.PrimaryKeyConstraint("event_id", name="pk_click_events"),
    )
    op.create_index("ix_click_events_timestamp", "click_events", ["timestamp"])

    fk = lambda name: sa.ForeignKeyConstraint(  # noqa: E731
        ["short_code"], ["urls.short_code"], name=f"fk_{name}_short_code_urls", ondelete="CASCADE"
    )
    clicks = lambda: sa.Column(  # noqa: E731
        "total_clicks", sa.BigInteger(), server_default=sa.text("0"), nullable=False
    )

    op.create_table(
        "daily_analytics",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("short_code", sa.String(32), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        clicks(),
        sa.CheckConstraint("total_clicks >= 0", name="ck_daily_analytics_total_clicks_non_negative"),
        fk("daily_analytics"),
        sa.PrimaryKeyConstraint("id", name="pk_daily_analytics"),
        sa.UniqueConstraint("short_code", "date", name="uq_daily_analytics_short_code_date"),
    )
    op.create_index("ix_daily_analytics_date", "daily_analytics", ["date"])

    for table, col, width in (
        ("browser_analytics", "browser_name", 64),
        ("os_analytics", "os_name", 64),
        ("device_analytics", "device_type", 32),
        ("country_analytics", "country_code", 2),
        ("referrer_analytics", "referrer", 255),
    ):
        op.create_table(
            table,
            sa.Column("short_code", sa.String(32), nullable=False),
            sa.Column(col, sa.String(width), nullable=False),
            clicks(),
            fk(table),
            sa.PrimaryKeyConstraint("short_code", col, name=f"pk_{table}"),
        )

    op.create_table(
        "id_allocator",
        sa.Column("allocator_name", sa.String(64), nullable=False),
        sa.Column("next_id", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("allocator_name", name="pk_id_allocator"),
    )


def downgrade() -> None:
    for table in (
        "id_allocator",
        "referrer_analytics",
        "country_analytics",
        "device_analytics",
        "os_analytics",
        "browser_analytics",
        "daily_analytics",
        "click_events",
        "urls",
        "users",
    ):
        op.drop_table(table)
