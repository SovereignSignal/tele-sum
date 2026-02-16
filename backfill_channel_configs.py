#!/usr/bin/env python3
"""
Backfill Channel Configurations

Creates ChannelConfig entries for channels and links messages to them.
Run this after deploying the channel segmentation update.

Usage:
    python backfill_channel_configs.py [--dry-run]
"""
import sys
import os
import argparse
from datetime import datetime
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set")
    sys.exit(1)

# Handle Railway postgres:// vs postgresql://
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)


def ensure_column_exists(conn):
    """Ensure channel_config_id column exists on messages table"""
    result = conn.execute(text("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name = 'messages' AND column_name = 'channel_config_id'
    """))
    if result.fetchone() is None:
        print("Adding channel_config_id column to messages table...")
        conn.execute(text("""
            ALTER TABLE messages 
            ADD COLUMN channel_config_id INTEGER REFERENCES channel_configs(id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_messages_channel_config_id 
            ON messages(channel_config_id)
        """))
        conn.commit()
        print("Column added successfully")
    else:
        print("channel_config_id column already exists")


def discover_unconfigured_channels(conn):
    """Find all channel_ids that don't have a ChannelConfig"""
    result = conn.execute(text("""
        SELECT DISTINCT m.channel_id 
        FROM messages m
        LEFT JOIN channel_configs cc ON m.channel_id = cc.channel_id
        WHERE cc.id IS NULL AND m.channel_id IS NOT NULL
    """))
    return [row[0] for row in result.fetchall()]


def get_channel_stats(conn, channel_id):
    """Get statistics for a channel"""
    result = conn.execute(text("""
        SELECT 
            COUNT(*) as count,
            MIN(timestamp) as oldest,
            MAX(timestamp) as newest
        FROM messages 
        WHERE channel_id = :channel_id
    """), {"channel_id": channel_id})
    row = result.fetchone()
    return {
        'count': row[0],
        'oldest': row[1],
        'newest': row[2]
    }


def create_channel_configs(conn, unconfigured_channels, dry_run=False):
    """Create ChannelConfig entries for unconfigured channels"""
    created = []
    
    for channel_id in unconfigured_channels:
        stats = get_channel_stats(conn, channel_id)
        
        print(f"Channel {channel_id}:")
        print(f"  - Messages: {stats['count']}")
        print(f"  - Date range: {stats['oldest']} to {stats['newest']}")
        
        if dry_run:
            print(f"  [DRY RUN] Would create ChannelConfig")
            created.append(channel_id)
            continue
        
        conn.execute(text("""
            INSERT INTO channel_configs (channel_id, channel_title, is_active, created_at)
            VALUES (:channel_id, :title, true, :now)
            ON CONFLICT (channel_id) DO NOTHING
        """), {
            "channel_id": channel_id,
            "title": f"Channel {channel_id}",
            "now": datetime.utcnow()
        })
        created.append(channel_id)
        print(f"  Created ChannelConfig for {channel_id}")
    
    if not dry_run and created:
        conn.commit()
        print(f"Committed {len(created)} new ChannelConfig entries")
    
    return created


def link_messages_to_configs(conn, dry_run=False):
    """Link messages to their ChannelConfig via channel_config_id FK"""
    # Get all channel configs
    result = conn.execute(text("""
        SELECT id, channel_id FROM channel_configs
    """))
    configs = {row[1]: row[0] for row in result.fetchall()}
    
    total_updated = 0
    
    for channel_id, config_id in configs.items():
        # Count messages that need linking
        result = conn.execute(text("""
            SELECT COUNT(*) FROM messages 
            WHERE channel_id = :channel_id AND channel_config_id IS NULL
        """), {"channel_id": channel_id})
        unlinked = result.fetchone()[0]
        
        if unlinked == 0:
            continue
        
        print(f"Channel {channel_id}: {unlinked} messages to link")
        
        if dry_run:
            print(f"  [DRY RUN] Would link {unlinked} messages to config_id={config_id}")
            total_updated += unlinked
            continue
        
        conn.execute(text("""
            UPDATE messages 
            SET channel_config_id = :config_id
            WHERE channel_id = :channel_id AND channel_config_id IS NULL
        """), {"config_id": config_id, "channel_id": channel_id})
        
        total_updated += unlinked
        print(f"  Linked {unlinked} messages to config_id={config_id}")
    
    if not dry_run and total_updated > 0:
        conn.commit()
        print(f"Committed {total_updated} message updates")
    
    return total_updated


def main():
    parser = argparse.ArgumentParser(description='Backfill channel configurations')
    parser.add_argument('--dry-run', action='store_true', 
                        help='Show what would be done without making changes')
    args = parser.parse_args()
    
    print("=" * 60)
    print("Channel Backfill Script")
    print("=" * 60)
    
    if args.dry_run:
        print("Running in DRY RUN mode - no changes will be made")
    
    with engine.connect() as conn:
        # Step 0: Ensure schema is updated
        print("\n--- Step 0: Ensuring schema is updated ---")
        ensure_column_exists(conn)
        
        # Step 1: Discover unconfigured channels
        print("\n--- Step 1: Discovering unconfigured channels ---")
        unconfigured = discover_unconfigured_channels(conn)
        
        if unconfigured:
            print(f"Found {len(unconfigured)} channels without ChannelConfig")
            
            # Step 2: Create ChannelConfig entries
            print("\n--- Step 2: Creating ChannelConfig entries ---")
            created = create_channel_configs(conn, unconfigured, args.dry_run)
            print(f"Created {len(created)} ChannelConfig entries")
        else:
            print("All channels have ChannelConfig entries")
        
        # Step 3: Link messages to configs
        print("\n--- Step 3: Linking messages to ChannelConfig ---")
        updated = link_messages_to_configs(conn, args.dry_run)
        print(f"Updated {updated} messages with channel_config_id")
        
        # Summary
        print("\n" + "=" * 60)
        print("Summary:")
        print(f"  - ChannelConfigs created: {len(unconfigured) if unconfigured else 0}")
        print(f"  - Messages linked: {updated}")
        
        if args.dry_run:
            print("\nThis was a dry run. Run without --dry-run to apply changes.")
        else:
            print("\nBackfill complete!")


if __name__ == "__main__":
    main()
