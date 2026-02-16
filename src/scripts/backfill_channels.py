#!/usr/bin/env python3
"""
Backfill Channel Configurations

This script performs two operations:
1. Creates ChannelConfig entries for any channel_ids in messages that don't have one
2. Links existing messages to their ChannelConfig via channel_config_id FK

Run this after deploying the channel segmentation update to backfill
historical data.

Usage:
    python -m src.scripts.backfill_channels [--dry-run]

Options:
    --dry-run    Show what would be done without making changes
"""

import sys
import argparse
import logging
from datetime import datetime

# Setup path for imports
sys.path.insert(0, '.')

from src.models.database import get_engine, SessionLocal
from src.models.models import Message, ChannelConfig

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def discover_unconfigured_channels(db):
    """Find all channel_ids that don't have a ChannelConfig"""
    # Get all unique channel_ids from messages
    message_channels = db.query(Message.channel_id).distinct().all()
    message_channel_ids = {c[0] for c in message_channels if c[0]}
    
    # Get all configured channel_ids
    config_channels = db.query(ChannelConfig.channel_id).all()
    config_channel_ids = {c[0] for c in config_channels if c[0]}
    
    # Find unconfigured channels
    unconfigured = message_channel_ids - config_channel_ids
    
    return unconfigured


def get_channel_stats(db, channel_id):
    """Get statistics for a channel"""
    count = db.query(Message).filter(Message.channel_id == channel_id).count()
    
    oldest = db.query(Message).filter(
        Message.channel_id == channel_id
    ).order_by(Message.timestamp.asc()).first()
    
    newest = db.query(Message).filter(
        Message.channel_id == channel_id
    ).order_by(Message.timestamp.desc()).first()
    
    return {
        'count': count,
        'oldest': oldest.timestamp if oldest else None,
        'newest': newest.timestamp if newest else None
    }


def create_channel_configs(db, unconfigured_channels, dry_run=False):
    """Create ChannelConfig entries for unconfigured channels"""
    created = []
    
    for channel_id in unconfigured_channels:
        stats = get_channel_stats(db, channel_id)
        
        logger.info(f"Channel {channel_id}:")
        logger.info(f"  - Messages: {stats['count']}")
        logger.info(f"  - Date range: {stats['oldest']} to {stats['newest']}")
        
        if dry_run:
            logger.info(f"  [DRY RUN] Would create ChannelConfig")
            created.append(channel_id)
            continue
        
        # Create config with placeholder title
        # The title will be updated when we receive new messages from this channel
        config = ChannelConfig(
            channel_id=channel_id,
            channel_title=f"Channel {channel_id}",
            is_active=True
        )
        db.add(config)
        created.append(channel_id)
        logger.info(f"  Created ChannelConfig for {channel_id}")
    
    if not dry_run and created:
        db.commit()
        logger.info(f"Committed {len(created)} new ChannelConfig entries")
    
    return created


def link_messages_to_configs(db, dry_run=False):
    """Link messages to their ChannelConfig via channel_config_id FK"""
    # Get all channel configs
    configs = db.query(ChannelConfig).all()
    config_map = {c.channel_id: c.id for c in configs}
    
    total_updated = 0
    
    for channel_id, config_id in config_map.items():
        # Count messages that need linking
        unlinked = db.query(Message).filter(
            Message.channel_id == channel_id,
            Message.channel_config_id.is_(None)
        ).count()
        
        if unlinked == 0:
            continue
        
        logger.info(f"Channel {channel_id}: {unlinked} messages to link")
        
        if dry_run:
            logger.info(f"  [DRY RUN] Would link {unlinked} messages to config_id={config_id}")
            total_updated += unlinked
            continue
        
        # Batch update for efficiency
        db.query(Message).filter(
            Message.channel_id == channel_id,
            Message.channel_config_id.is_(None)
        ).update(
            {Message.channel_config_id: config_id},
            synchronize_session=False
        )
        
        total_updated += unlinked
        logger.info(f"  Linked {unlinked} messages to config_id={config_id}")
    
    if not dry_run and total_updated > 0:
        db.commit()
        logger.info(f"Committed {total_updated} message updates")
    
    return total_updated


def main():
    parser = argparse.ArgumentParser(description='Backfill channel configurations')
    parser.add_argument('--dry-run', action='store_true', 
                        help='Show what would be done without making changes')
    args = parser.parse_args()
    
    logger.info("=" * 60)
    logger.info("Channel Backfill Script")
    logger.info("=" * 60)
    
    if args.dry_run:
        logger.info("Running in DRY RUN mode - no changes will be made")
    
    # Initialize database
    get_engine()
    db = SessionLocal()
    
    try:
        # Step 1: Discover unconfigured channels
        logger.info("\n--- Step 1: Discovering unconfigured channels ---")
        unconfigured = discover_unconfigured_channels(db)
        
        if unconfigured:
            logger.info(f"Found {len(unconfigured)} channels without ChannelConfig")
            
            # Step 2: Create ChannelConfig entries
            logger.info("\n--- Step 2: Creating ChannelConfig entries ---")
            created = create_channel_configs(db, unconfigured, args.dry_run)
            logger.info(f"Created {len(created)} ChannelConfig entries")
        else:
            logger.info("All channels have ChannelConfig entries")
        
        # Step 3: Link messages to configs
        logger.info("\n--- Step 3: Linking messages to ChannelConfig ---")
        updated = link_messages_to_configs(db, args.dry_run)
        logger.info(f"Updated {updated} messages with channel_config_id")
        
        # Summary
        logger.info("\n" + "=" * 60)
        logger.info("Summary:")
        logger.info(f"  - ChannelConfigs created: {len(unconfigured) if unconfigured else 0}")
        logger.info(f"  - Messages linked: {updated}")
        
        if args.dry_run:
            logger.info("\nThis was a dry run. Run without --dry-run to apply changes.")
        else:
            logger.info("\nBackfill complete!")
        
    except Exception as e:
        logger.error(f"Error during backfill: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
