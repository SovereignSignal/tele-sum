#!/usr/bin/env python3
"""
Diagnostic script to check and fix channel configuration issues.

Usage:
    python diagnose_channels.py          # Show current status
    python diagnose_channels.py --fix    # Fix issues found
"""
import asyncio
import os
import sys
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / '.env')

import httpx
from src.models import database
from src.models.models import Message, Summary, TelegramBot, ChannelConfig, SyncStatus
from src.config import settings


def diagnose_channels():
    """Check channel configuration and sync status"""
    print("=" * 60)
    print("🔍 Tele-Sum Channel Diagnostic")
    print("=" * 60)
    
    database.get_engine()
    db = database.SessionLocal()
    
    try:
        # Check configuration
        print("\n📋 Configuration Check:")
        print(f"  - TELEGRAM_CHANNEL_USERNAME: {settings.telegram_channel_username or '❌ Not set'}")
        print(f"  - TELEGRAM_BOT_TOKENS: {'✅ Configured' if settings.telegram_bot_tokens else '❌ Not set'}")
        print(f"  - Bot mode enabled: {'✅' if settings.enable_bot_mode else '❌'}")
        print(f"  - Phone auth configured: {'✅' if settings.is_telegram_configured() else '❌'}")
        
        # Check registered bots
        print("\n🤖 Registered Bots:")
        bots = db.query(TelegramBot).all()
        if not bots:
            print("  ❌ No bots registered in database")
        else:
            for bot in bots:
                print(f"  - Bot ID {bot.id}: @{bot.bot_username or 'unknown'}")
                print(f"    Token: {bot.bot_token[:20]}...")
                print(f"    Active: {'✅' if bot.is_active else '❌'}")
                print(f"    Last update ID: {bot.last_update_id or 'None'}")
        
        # Check channel configs
        print("\n📺 Channel Configurations:")
        channels = db.query(ChannelConfig).all()
        if not channels:
            print("  ❌ No channels configured in database")
        else:
            for channel in channels:
                print(f"  - Channel: {channel.channel_title or channel.channel_username or 'Unknown'}")
                print(f"    ID: {channel.channel_id}")
                print(f"    Username: @{channel.channel_username or 'none'}")
                print(f"    Active: {'✅' if channel.is_active else '❌'}")
                print(f"    Import mode: {channel.import_mode.value if channel.import_mode else 'unknown'}")
                print(f"    Bot ID: {channel.bot_id or 'None'}")
                
                # Check for placeholder ID issue
                if channel.channel_id and channel.channel_id.startswith('@'):
                    print(f"    ⚠️  WARNING: channel_id is a placeholder (@username), not resolved yet!")
                
                # Check sync status
                sync = db.query(SyncStatus).filter(SyncStatus.channel_id == channel.channel_id).first()
                if sync:
                    print(f"    Last sync: {sync.last_sync_at or 'Never'}")
                    print(f"    Total synced: {sync.total_messages_synced or 0}")
                else:
                    print(f"    Sync status: Not initialized")
        
        # Check message counts
        print("\n📊 Message Statistics:")
        total_messages = db.query(Message).count()
        print(f"  - Total messages: {total_messages}")
        
        # Messages per channel
        from sqlalchemy import func
        channel_counts = db.query(
            Message.channel_id, 
            func.count(Message.id)
        ).group_by(Message.channel_id).all()
        
        for channel_id, count in channel_counts:
            print(f"  - Channel {channel_id}: {count} messages")
        
        # Check pending summaries
        from src.models.models import ProcessingStatus
        pending = db.query(Summary).filter(Summary.processing_status.in_([ProcessingStatus.PENDING, ProcessingStatus.RETRY])).count()
        failed = db.query(Summary).filter(Summary.processing_status == ProcessingStatus.FAILED).count()
        print(f"  - Pending processing: {pending}")
        print(f"  - Failed: {failed}")
        
        # Check for channel_id inconsistencies
        print("\n⚠️  Channel ID Issues:")
        for channel_id, count in channel_counts:
            # Check if this channel_id exists in ChannelConfig
            config = db.query(ChannelConfig).filter(ChannelConfig.channel_id == channel_id).first()
            if not config:
                print(f"  ❌ Messages for unconfigured channel_id {channel_id}: {count} messages")
                # Try to find matching config by checking if -100 prefix is missing
                if not channel_id.startswith('-'):
                    possible_id = f"-100{channel_id}"
                    matching = db.query(ChannelConfig).filter(ChannelConfig.channel_id == possible_id).first()
                    if matching:
                        print(f"     → Likely should be {possible_id} (@{matching.channel_username})")
        
        print("\n" + "=" * 60)
        
    finally:
        db.close()


async def test_bot_access():
    """Test if bots can access configured channels"""
    print("\n🔗 Testing Bot Channel Access:")
    
    database.get_engine()
    db = database.SessionLocal()
    
    try:
        bots = db.query(TelegramBot).filter(TelegramBot.is_active == True).all()
        channels = db.query(ChannelConfig).filter(ChannelConfig.is_active == True).all()
        
        if not bots:
            print("  ❌ No active bots to test")
            return
        
        if not channels:
            print("  ❌ No active channels to test")
            return
        
        for bot in bots:
            print(f"\n  Testing bot @{bot.bot_username or 'unknown'}...")
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Get bot info
                response = await client.get(
                    f'https://api.telegram.org/bot{bot.bot_token}/getMe'
                )
                info = response.json()
                
                if not info.get('ok'):
                    print(f"    ❌ Bot token invalid: {info}")
                    continue
                
                print(f"    ✅ Bot is valid: @{info['result']['username']}")
                
                # Check if bot has any pending updates
                response = await client.get(
                    f'https://api.telegram.org/bot{bot.bot_token}/getUpdates',
                    params={'limit': 5}
                )
                updates = response.json()
                
                if updates.get('ok'):
                    count = len(updates.get('result', []))
                    print(f"    📬 Pending updates: {count}")
                    
                    # Show channels seen in updates
                    seen_channels = set()
                    for update in updates.get('result', []):
                        post = update.get('channel_post') or update.get('message')
                        if post:
                            chat = post.get('chat', {})
                            seen_channels.add((
                                str(chat.get('id')),
                                chat.get('username', ''),
                                chat.get('title', '')
                            ))
                    
                    if seen_channels:
                        print(f"    📺 Channels in updates:")
                        for cid, username, title in seen_channels:
                            print(f"       - {title or username or cid} (ID: {cid})")
                else:
                    print(f"    ⚠️  Could not get updates: {updates}")
                
    finally:
        db.close()


def fix_channel_configs():
    """Attempt to fix common channel configuration issues"""
    print("\n🔧 Attempting to fix channel configurations...")
    
    database.get_engine()
    db = database.SessionLocal()
    
    try:
        channels = db.query(ChannelConfig).all()
        
        for channel in channels:
            # Fix 1: Ensure username is stored without @
            if channel.channel_username and channel.channel_username.startswith('@'):
                old = channel.channel_username
                channel.channel_username = channel.channel_username.lstrip('@')
                print(f"  ✅ Fixed username: {old} -> {channel.channel_username}")
        
        db.commit()
        print("  Done!")
        
    finally:
        db.close()


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Diagnose channel sync issues")
    parser.add_argument("--fix", action="store_true", help="Attempt to fix issues")
    parser.add_argument("--test-access", action="store_true", help="Test bot channel access")
    args = parser.parse_args()
    
    diagnose_channels()
    
    if args.test_access:
        await test_bot_access()
    
    if args.fix:
        fix_channel_configs()


if __name__ == "__main__":
    asyncio.run(main())
