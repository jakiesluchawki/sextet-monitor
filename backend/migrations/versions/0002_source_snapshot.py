"""Track authoritative catalog snapshot time separately from event publication."""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("provider_records", sa.Column("source_snapshot_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    raise RuntimeError("Destructive schema downgrades require explicit review and backup")
