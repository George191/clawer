"""Persist the team API-key status already used by the model.

Revision ID: 81a1b2c3d4e5
Revises: 80a1b2c3d4e5
"""
from alembic import op


revision = "81a1b2c3d4e5"
down_revision = "80a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade():
    # Prior to the status column, revocation deleted the key and rotation replaced
    # its credential in place. The existing rows alone represent retained keys.
    # ORM-created schemas already have the enum and may have disabled keys.
    op.execute("""
        ALTER TABLE app.apikey ADD COLUMN IF NOT EXISTS status app.statustypes
        NOT NULL DEFAULT 'ENABLE'::app.statustypes
    """)
    op.execute("ALTER TABLE app.apikey ALTER COLUMN status DROP DEFAULT")


def downgrade():
    # An old schema cannot represent a disabled, expired, or locked key.
    op.execute("LOCK TABLE app.apikey IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM app.apikey WHERE status <> 'ENABLE') THEN
                RAISE EXCEPTION 'Cannot downgrade team API keys with retained non-enabled states';
            END IF;
        END $$
    """)
    op.execute("ALTER TABLE app.apikey DROP COLUMN status")
