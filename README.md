# Tele-Sum

[![Deployed on Railway](https://railway.app/button.svg)](https://railway.app)

🚀 **Currently deployed and running on Railway** - A production-ready system that extracts and organizes historical data from Telegram channels, using Grok AI to analyze and summarize content.

## 🎯 Vision & Purpose

**Transform Telegram's append-only message stream into searchable, AI-enriched knowledge.**

Tele-Sum solves the problem of Telegram channels being write-only archives where valuable links and content get buried. Most posts are links without context - you can't know what's valuable without clicking through. Our system changes that by:

- **Extracting 100% of channel history** and maintaining ongoing synchronization
- **Using Grok AI's unique X/Twitter access** to analyze and summarize content that other AIs can't see
- **User-defined categorization** letting each user organize content their way
- **Graceful degradation** ensuring the system works even when AI services are unavailable

📖 **[Read the full vision and project overview →](VISION.md)**

## 🚀 Railway Deployment

This application is **designed for and deployed on Railway**. Railway provides:
- Automatic PostgreSQL database provisioning
- Environment variable management
- Automatic HTTPS and custom domains
- Built-in health checks and monitoring
- Scheduled jobs for message synchronization

📚 **[Complete Railway deployment guide →](RAILWAY_DEPLOYMENT.md)**

### Quick Deploy

1. Fork this repository
2. Connect to Railway via GitHub
3. Railway auto-configures the database
4. Add your API keys in Railway's dashboard
5. Your instance is live!

## Features

- **Complete History Import**: Extracts all historical messages from Telegram channels
- **AI-Powered Summaries**: Uses Grok to analyze content and generate 2-3 sentence summaries
- **Smart Categorization**: User-defined categories with AI classification
- **Twitter/X Integration**: Grok can directly analyze X/Twitter posts
- **Search & Filter**: Full-text search across original messages and summaries
- **Export Functionality**: Export filtered data as CSV or JSON
- **Graceful Degradation**: System remains functional even when Grok is unavailable
- **Automatic Sync**: Periodic synchronization for new messages

## Configuration

### Required API Keys

To fully utilize all features, you'll need:

1. **Telegram API Credentials** (for importing messages)
   - Get from https://my.telegram.org
   - Required: API ID, API Hash, Phone Number

2. **Grok (xAI) API Key** (for AI summaries)
   - Get from https://x.ai
   - Essential for analyzing X/Twitter content

3. **PostgreSQL Database**
   - Automatically provisioned by Railway
   - No manual configuration needed

### Environment Variables

Railway makes configuration simple - just add these in your service settings:

```bash
# Telegram Configuration
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_PHONE=+1234567890
TELEGRAM_CHANNEL_USERNAME=channelname

# AI Configuration
GROK_API_KEY=your_grok_api_key

# Security (auto-generated if not set)
SECRET_KEY=your-secure-random-string
```

📚 **[Full configuration guide →](RAILWAY_DEPLOYMENT.md#environment-variable-reference)**

## Usage

Access your deployed instance at your Railway-provided URL

### Initial Import

1. Navigate to the Import page
2. Click "Start Import" to begin importing channel history
3. Monitor progress on the dashboard

### Managing Categories

1. Go to Categories page
2. Add custom categories for your content
3. AI will use these for classification

### Search and Export

- Use the dashboard to search and filter messages
- Export filtered results as CSV or JSON
- View AI summaries and categorizations

## API Endpoints

- `GET /api/messages` - Search and filter messages
- `GET /api/categories` - List categories
- `POST /api/categories` - Create category
- `POST /api/import/start` - Start initial import
- `GET /api/import/status` - Check import status
- `GET /api/export/csv` - Export as CSV
- `GET /api/export/json` - Export as JSON

## Production Status

✅ **This system is production-ready and currently deployed on Railway**

The deployed instance includes:
- Full historical message import from Telegram channels
- Grok AI integration for X/Twitter content analysis
- PostgreSQL database with proper indexing
- Automated synchronization every 6 hours
- Graceful degradation when AI services are unavailable
- Web interface accessible via Railway's provided URL

## Architecture

- **Backend**: FastAPI with async support
- **Database**: PostgreSQL with SQLAlchemy ORM
- **Telegram**: Telethon library for API access
- **AI Processing**: Grok API for content analysis
- **Background Jobs**: APScheduler for synchronization
- **Frontend**: Server-side rendered with HTMX

## Graceful Degradation

The system handles Grok failures gracefully:
- Messages remain searchable by original content
- Failed processing is logged with error details
- Retry mechanism for temporary failures
- Core functionality continues without AI features

## Monitoring

- Check `/health` endpoint for system status
- View import status at `/api/import/status`
- Monitor logs for processing errors
- Track API usage and costs

## License

MIT