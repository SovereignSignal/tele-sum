"""Fix Message uniqueness

Revision ID: 20260116_0001
Revises: 
Create Date: 2026-01-16

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260116_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop any existing UNIQUE constraint on messages.telegram_id (Telegram IDs are only unique per chat)
    # This is written defensively because the constraint name may differ across environments.
    op.execute(
        """
        DO $$
        DECLARE
            c record;
        BEGIN
            FOR c IN
                SELECT con.conname
                FROM pg_constraint con
                JOIN pg_class rel ON rel.oid = con.conrelid
                JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
                WHERE rel.relname = 'messages'
                  AND con.contype = 'u'
                  AND pg_get_constraintdef(con.oid) ILIKE '%(telegram_id)%'
                  AND pg_get_constraintdef(con.oid) NOT ILIKE '%channel_id%'
            LOOP
                EXECUTE format('ALTER TABLE %I.%I DROP CONSTRAINT IF EXISTS %I', nsp.nspname, 'messages', c.conname);
            END LOOP;
        END $$;
        """
    )

    # Create composite unique constraint (channel_id, telegram_id)
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint con
                JOIN pg_class rel ON rel.oid = con.conrelid
                WHERE rel.relname = 'messages'
                  AND con.contype = 'u'
                  AND con.conname = 'uq_messages_channel_telegram_id'
            ) THEN
                ALTER TABLE messages
                ADD CONSTRAINT uq_messages_channel_telegram_id UNIQUE (channel_id, telegram_id);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Best-effort rollback
    op.execute("ALTER TABLE messages DROP CONSTRAINT IF EXISTS uq_messages_channel_telegram_id")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint con
                JOIN pg_class rel ON rel.oid = con.conrelid
                WHERE rel.relname = 'messages'
                  AND con.contype = 'u'
                  AND pg_get_constraintdef(con.oid) ILIKE '%(telegram_id)%'
            ) THEN
                ALTER TABLE messages
                ADD CONSTRAINT messages_telegram_id_key UNIQUE (telegram_id);
            END IF;
        END $$;
        """
    )
