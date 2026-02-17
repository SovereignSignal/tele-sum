"""Backfill entities.protocols to entities.orgs

This migration renames the 'protocols' key to 'orgs' in the entities JSON column
of the summaries table. The dual-read support in the API ensures zero downtime.

Revision ID: backfill_protocols_to_orgs
Revises: 
Create Date: 2026-02-16

"""
from alembic import op
import sqlalchemy as sa

revision = 'backfill_protocols_to_orgs'
down_revision = '001_channel_config_fk'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Rename entities.protocols to entities.orgs in all existing summaries.
    
    This uses jsonb_set to add the 'orgs' key with the value from 'protocols',
    then removes the 'protocols' key.
    """
    op.execute("""
        UPDATE summaries
        SET entities = (entities - 'protocols') || jsonb_build_object('orgs', entities->'protocols')
        WHERE entities ? 'protocols'
          AND NOT (entities ? 'orgs')
    """)


def downgrade() -> None:
    """
    Revert: rename entities.orgs back to entities.protocols.
    """
    op.execute("""
        UPDATE summaries
        SET entities = (entities - 'orgs') || jsonb_build_object('protocols', entities->'orgs')
        WHERE entities ? 'orgs'
          AND NOT (entities ? 'protocols')
    """)
