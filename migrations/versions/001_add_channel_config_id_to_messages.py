"""Add channel_config_id FK to messages table

Revision ID: 001_channel_config_fk
Revises: 
Create Date: 2026-02-16

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '001_channel_config_fk'
down_revision = '20260116_0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add channel_config_id column to messages table
    op.add_column('messages', sa.Column('channel_config_id', sa.Integer(), nullable=True))
    
    # Create index for the new column
    op.create_index('ix_messages_channel_config_id', 'messages', ['channel_config_id'], unique=False)
    
    # Add foreign key constraint
    op.create_foreign_key(
        'fk_messages_channel_config_id',
        'messages', 'channel_configs',
        ['channel_config_id'], ['id'],
        ondelete='SET NULL'
    )


def downgrade() -> None:
    # Remove foreign key constraint
    op.drop_constraint('fk_messages_channel_config_id', 'messages', type_='foreignkey')
    
    # Remove index
    op.drop_index('ix_messages_channel_config_id', table_name='messages')
    
    # Remove column
    op.drop_column('messages', 'channel_config_id')
