# Hybrid Bot Architecture Documentation

## Overview

Tele-Sum now supports a **hybrid architecture** that combines the best of both worlds:
- **Phone Authentication**: For complete historical message import (100% of channel history)
- **Bot Monitoring**: For ongoing, phone-free message collection

This allows you to import all historical data once, then switch to bot-based monitoring without ever needing your phone again.

## Architecture Components

### 1. Phone-Based Import (Historical)
- Uses Telethon library with phone authentication
- Can access 100% of channel history
- One-time setup process
- Phone only needed during initial import

### 2. Bot-Based Monitoring (Ongoing)
- Uses python-telegram-bot library
- No phone number required
- Multiple bots supported
- Real-time message collection
- Continues from where historical import ended

### 3. Hybrid Manager
- Coordinates between phone and bot services
- Manages transitions from phone to bot mode
- Handles multiple bots and channels
- Ensures no duplicate message imports

## Setup Options

### Option 1: Full Hybrid (Recommended)

**Best for**: Users who want complete historical data AND phone-free operation

```bash
# Environment Variables
TELEGRAM_API_ID=123456              # For historical import
TELEGRAM_API_HASH=abcdef...         # For historical import  
TELEGRAM_PHONE=+1234567890          # For historical import
TELEGRAM_BOT_TOKENS=bot1,bot2       # For ongoing monitoring
ENABLE_BOT_MODE=true
ENABLE_PHONE_IMPORT=true
```

**Workflow**:
1. Use phone auth to import complete channel history (one-time)
2. Create bot and add as admin to channel
3. System automatically transitions to bot monitoring
4. Phone never needed again

### Option 2: Bot-Only Mode

**Best for**: Users who don't need historical data or want completely phone-free setup

```bash
# Environment Variables
TELEGRAM_BOT_TOKENS=your_bot_token
ENABLE_BOT_MODE=true
ENABLE_PHONE_IMPORT=false
```

**Limitations**:
- Only captures messages from when bot joins channel forward
- Cannot access historical messages
- Requires bot to be admin in channel/group

### Option 3: Phone-Only Mode (Legacy)

**Best for**: Users who prefer traditional phone-based approach

```bash
# Environment Variables
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=abcdef...
TELEGRAM_PHONE=+1234567890
TELEGRAM_CHANNEL_USERNAME=@channel
ENABLE_BOT_MODE=false
```

## Step-by-Step Setup Guide

### Step 1: Deploy to Railway

1. Fork this repository
2. Create new Railway project
3. Add PostgreSQL service
4. Deploy from GitHub

### Step 2: Initial Configuration

1. Add `SECRET_KEY` environment variable
2. Verify database connection at `/health`
3. Visit `/bots` page for setup wizard

### Step 3: Historical Import (Optional but Recommended)

1. **Get Telegram API Credentials**:
   ```
   - Go to https://my.telegram.org
   - Log in with phone number
   - Create new application
   - Copy API ID and API Hash
   ```

2. **Configure Phone Auth**:
   ```bash
   TELEGRAM_API_ID=your_id
   TELEGRAM_API_HASH=your_hash
   TELEGRAM_PHONE=+1234567890
   ```

3. **Import History**:
   - Visit `/bots` page
   - Enter channel username
   - Click "Import Complete History"
   - Wait for import to complete

### Step 4: Setup Bot Monitoring

1. **Create Telegram Bot**:
   ```
   - Open Telegram
   - Search for @BotFather
   - Send /newbot
   - Choose name and username
   - Copy bot token
   ```

2. **Add Bot to Channel**:
   ```
   - Open your channel/group
   - Go to settings
   - Add administrators
   - Search for your bot
   - Grant necessary permissions
   ```

3. **Register Bot in System**:
   - Visit `/bots` page
   - Paste bot token
   - Click "Validate" then "Register"
   - Select channel to monitor
   - Click "Setup Monitoring"

### Step 5: Verify Setup

Check status at:
- `/api/hybrid/setup/wizard` - Setup status
- `/api/hybrid/channels` - Channel list
- `/api/hybrid/bots` - Registered bots

## API Endpoints

### Bot Management

**Register Bot**
```bash
POST /api/hybrid/bot/register
{
  "bot_token": "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
}
```

**Setup Monitoring**
```bash
POST /api/hybrid/bot/setup-monitoring
{
  "bot_token": "your_bot_token",
  "channel_identifier": "@channelname or -1001234567890"
}
```

### Historical Import

**Import Channel History**
```bash
POST /api/hybrid/import/historical
{
  "channel_username": "@channelname",
  "phone": "+1234567890",  # Optional if configured in env
  "api_id": 123456,        # Optional if configured in env
  "api_hash": "hash"       # Optional if configured in env
}
```

### Channel Status

**Get Channel Status**
```bash
GET /api/hybrid/channels/{channel_id}/status
```

**List All Channels**
```bash
GET /api/hybrid/channels
```

## Database Schema

### New Tables

**telegram_bots**
- Stores bot tokens and configuration
- Tracks bot status and webhook URLs
- Links to channels being monitored

**channel_configs**
- Tracks import mode per channel (phone/bot/hybrid)
- Stores historical import status
- Links channels to bots

### Modified Tables

**messages**
- Added `bot_id` to track which bot imported
- Added `import_method` (phone/bot)

**sync_status**
- Added `last_phone_sync_id` and `last_bot_sync_id`
- Added `transition_to_bot_complete` flag

## How It Works

### Message Flow

1. **Historical Import** (if enabled):
   ```
   Phone Auth → Connect to Telegram → Fetch All History → Store in DB
   ```

2. **Bot Monitoring** (ongoing):
   ```
   Bot Receives Message → Extract Content → Store in DB → Queue for Processing
   ```

3. **Processing Pipeline**:
   ```
   Pending Messages → Grok AI Analysis → Categorization → Summary Storage
   ```

### Transition Process

When transitioning from phone to bot:
1. System notes last message ID from phone import
2. Bot starts monitoring from that point forward
3. No messages are duplicated or missed
4. Phone session can be safely terminated

## Troubleshooting

### Bot Not Receiving Messages

**Check**:
- Bot is added as admin to channel
- Bot has message read permissions
- Channel is public or bot has access
- Bot token is valid

**Fix**:
```bash
# Verify bot access
POST /api/hybrid/bot/check-access?channel_id=@channel&bot_token=token
```

### Historical Import Fails

**Check**:
- Phone number includes country code
- API credentials are correct
- Phone can receive SMS codes
- Channel username is correct

### Duplicate Messages

**Prevention**:
- System tracks message IDs
- Deduplication on `telegram_id` field
- Separate tracking for phone vs bot imports

## Best Practices

1. **Start with Historical Import**: Get complete data first
2. **Use Multiple Bots**: Distribute load across channels
3. **Monitor Bot Health**: Check `/api/hybrid/bots` regularly
4. **Secure Bot Tokens**: Never commit tokens to git
5. **Test in Staging**: Use test channels first

## Migration from Phone-Only

If you're currently using phone-only mode:

1. Complete any pending imports
2. Create and register bot
3. Add bot to channels
4. Use transition endpoint:
   ```bash
   POST /api/hybrid/transition-to-bot
   {
     "channel_id": "channel_id",
     "bot_token": "bot_token"
   }
   ```
5. Disable phone variables once confirmed working

## Security Considerations

- Bot tokens are stored encrypted (in production)
- Each bot only accesses assigned channels
- Phone credentials can be removed after import
- Use environment variables, never hardcode
- Regularly rotate bot tokens

## Limitations

### Bot Limitations
- Cannot access messages before joining
- Requires admin permissions for full features
- Subject to Telegram's bot API limits
- Cannot access deleted messages

### Phone Import Limitations
- Requires active phone number
- SMS verification needed
- Session can expire
- Rate limits apply

## Support

For issues or questions:
- Check `/api/hybrid/setup/wizard` for setup status
- Review logs in Railway dashboard
- Open issue on GitHub
- Check bot permissions in Telegram