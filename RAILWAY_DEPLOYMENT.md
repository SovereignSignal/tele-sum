# Railway Deployment Guide for Tele-Sum

This guide walks you through deploying Tele-Sum on Railway with proper configuration.

## Quick Start

1. **Fork/Clone the Repository**
   - Ensure you have this repository in your GitHub account

2. **Create a New Railway Project**
   - Go to [Railway](https://railway.app)
   - Click "New Project"
   - Select "Deploy from GitHub repo"
   - Choose your Tele-Sum repository

3. **The app will now deploy and start!**
   - Railway automatically detects the `railway.json` configuration
   - The app will start even without full configuration
   - Visit the deployed URL to see the configuration status

## Step-by-Step Configuration

### 1. Add PostgreSQL Database (Required)

Railway makes this super easy:

1. In your Railway project dashboard, click "New Service"
2. Select "Database" → "PostgreSQL"
3. Railway automatically connects it to your app
4. The `DATABASE_URL` is automatically configured!

No manual configuration needed - Railway handles the connection string automatically.

### 2. Configure Environment Variables

In your Railway service settings, go to the "Variables" tab and add:

#### Essential Variables (for basic operation)
```bash
# Automatically set by Railway
PORT=8080                    # Railway sets this automatically
DATABASE_URL=...             # Auto-configured when you add PostgreSQL

# Security (generate a random string)
SECRET_KEY=your-secure-random-string-here
```

#### Telegram Configuration (for import features)
```bash
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_PHONE=+1234567890
TELEGRAM_CHANNEL_USERNAME=channelname
```

To get Telegram credentials:
1. Go to https://my.telegram.org
2. Log in with your phone number
3. Go to "API development tools"
4. Create a new application
5. Copy the `api_id` and `api_hash`

#### Grok AI Configuration (for AI summaries)
```bash
GROK_API_KEY=your_grok_api_key
```

To get a Grok API key:
1. Go to https://x.ai
2. Sign up for API access
3. Generate an API key

#### Optional Configuration
```bash
APP_ENV=production                    # Default: production
SYNC_INTERVAL_HOURS=6                 # Default: 6
MAX_GROK_REQUESTS_PER_HOUR=100       # Default: 100
GROK_API_URL=https://api.x.ai/v1     # Default: https://api.x.ai/v1
```

### 3. Check Configuration Status

After deployment, visit these endpoints to verify your configuration:

- `https://your-app.railway.app/health` - Basic health check
- `https://your-app.railway.app/api/config/status` - Detailed configuration status
- `https://your-app.railway.app/api/config/required-vars` - List of all environment variables

### 4. Initialize the Database

Once PostgreSQL is connected:

1. The database tables are automatically created on first startup
2. Check the deployment logs to confirm: "✅ Database tables created/verified"

If you need to run migrations manually:
```bash
# In Railway's shell or locally with DATABASE_URL set
alembic upgrade head
```

## Deployment Scenarios

### Minimal Deployment (Just to Test)
- Railway automatically provides `PORT`
- App starts and shows configuration guide
- Visit `/api/config/status` to see what's missing

### Basic Deployment (Database Only)
1. Add PostgreSQL service in Railway
2. Set `SECRET_KEY`
3. App works for manual data entry (no Telegram import)

### Full Deployment (All Features)
1. Add PostgreSQL service
2. Set all environment variables (Telegram, Grok, Secret Key)
3. All features enabled: import, AI summaries, sync

## Troubleshooting

### App Crashes on Start

**Issue**: ValidationError for missing fields
**Solution**: This has been fixed! The app now starts even with missing configuration and shows you what needs to be set up.

### Database Connection Failed

**Issue**: Can't connect to database
**Solution**: 
1. Ensure PostgreSQL service is added in Railway
2. Check that services are in the same project
3. Railway auto-configures the connection

### Telegram Import Not Working

**Issue**: Import endpoint returns 503
**Solution**:
1. Check all TELEGRAM_* variables are set
2. Verify phone number format includes country code
3. Check `/api/config/status` for specific missing variables

### Grok AI Not Working

**Issue**: Summaries not being generated
**Solution**:
1. Check GROK_API_KEY is set
2. Verify API key is valid at https://x.ai
3. Check rate limits haven't been exceeded

## Configuration Validation

The app includes built-in configuration validation:

```python
# Check configuration programmatically
GET /api/config/status

# Response shows:
{
  "database": { "configured": true/false, ... },
  "telegram": { "configured": true/false, ... },
  "grok": { "configured": true/false, ... },
  "environment_hints": { ... }
}
```

## Environment Variable Reference

| Variable | Required | Description | Default |
|----------|----------|-------------|---------|
| PORT | Auto | Server port (Railway provides) | 8080 |
| DATABASE_URL | Auto* | PostgreSQL connection string | Auto from Railway |
| SECRET_KEY | Yes | Session security key | Auto-generated |
| TELEGRAM_API_ID | No** | Telegram API ID | None |
| TELEGRAM_API_HASH | No** | Telegram API Hash | None |
| TELEGRAM_PHONE | No** | Your phone number | None |
| TELEGRAM_CHANNEL_USERNAME | No** | Channel to import | None |
| GROK_API_KEY | No*** | Grok AI API key | None |
| APP_ENV | No | Environment mode | production |
| SYNC_INTERVAL_HOURS | No | Hours between syncs | 6 |
| MAX_GROK_REQUESTS_PER_HOUR | No | API rate limit | 100 |

\* Automatically configured when PostgreSQL service is added
\** Required for Telegram import features
\*** Required for AI summary features

## Progressive Deployment Strategy

1. **Phase 1: Basic Deployment**
   - Deploy app to Railway
   - Add PostgreSQL
   - Verify health check works

2. **Phase 2: Add Telegram**
   - Configure Telegram variables
   - Test import functionality
   - Verify message storage

3. **Phase 3: Add AI Features**
   - Add Grok API key
   - Test summary generation
   - Monitor API usage

## Monitoring

Railway provides:
- Deployment logs
- Metrics dashboard
- Custom domain support
- Automatic HTTPS

Check your app logs for:
- Configuration status on startup
- Warning messages for missing configs
- Error details if features fail

## Support

- Railway Documentation: https://docs.railway.app
- Tele-Sum Issues: [GitHub Issues](https://github.com/yourusername/tele-sum/issues)
- Configuration Status: `https://your-app.railway.app/api/config/status`