#!/usr/bin/env python3
"""
Fix channel_id mismatch in messages table.

Some messages were imported with channel_id missing the -100 prefix.
This script corrects those entries.

Usage:
    python fix_channel_ids.py          # Preview changes
    python fix_channel_ids.py --apply  # Apply fixes
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / '.env')

from src.models import database
from src.models.models import Message, ChannelConfig


def fix_channel_ids(apply: bool = False):
    """Fix channel_id values that are missing the -100 prefix"""
    print("=" * 60)
    print("🔧 Channel ID Fix Script")
    print("=" * 60)
    
    database.get_engine()
    db = database.SessionLocal()
    
    try:
        # Find messages with channel_ids that don't match any ChannelConfig
        from sqlalchemy import func
        channel_counts = db.query(
            Message.channel_id, 
            func.count(Message.id)
        ).group_by(Message.channel_id).all()
        
        fixes_needed = []
        
        for channel_id, count in channel_counts:
            config = db.query(ChannelConfig).filter(ChannelConfig.channel_id == channel_id).first()
            if not config:
                # Check if -100 prefix is missing
                if not channel_id.startswith('-'):
                    possible_id = f"-100{channel_id}"
                    matching = db.query(ChannelConfig).filter(ChannelConfig.channel_id == possible_id).first()
                    if matching:
                        fixes_needed.append({
                            'old_id': channel_id,
                            'new_id': possible_id,
                            'count': count,
                            'username': matching.channel_username
                        })
        
        if not fixes_needed:
            print("\n✅ No channel_id fixes needed!")
            return
        
        print("\n📋 Fixes needed:")
        total = 0
        for fix in fixes_needed:
            print(f"  - {fix['old_id']} → {fix['new_id']} (@{fix['username']}): {fix['count']} messages")
            total += fix['count']
        
        print(f"\n  Total: {total} messages to fix")
        
        if not apply:
            print("\n⚠️  This is a preview. Run with --apply to make changes.")
            return
        
        print("\n🔄 Applying fixes...")
        
        for fix in fixes_needed:
            # Update all messages with the old channel_id
            updated = db.query(Message).filter(
                Message.channel_id == fix['old_id']
            ).update({Message.channel_id: fix['new_id']})
            
            print(f"  ✅ Updated {updated} messages: {fix['old_id']} → {fix['new_id']}")
        
        db.commit()
        print("\n✅ All fixes applied successfully!")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Fix channel_id values in messages table")
    parser.add_argument("--apply", action="store_true", help="Apply the fixes (otherwise preview only)")
    args = parser.parse_args()
    
    fix_channel_ids(apply=args.apply)
