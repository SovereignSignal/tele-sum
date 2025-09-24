# Tele-Sum

A system that extracts and organizes historical data from Telegram channels, using Grok AI to analyze and summarize content.

## Features

- **Complete History Import**: Extracts all historical messages from Telegram channels
- **AI-Powered Summaries**: Uses Grok to analyze content and generate 2-3 sentence summaries
- **Smart Categorization**: User-defined categories with AI classification
- **Twitter/X Integration**: Grok can directly analyze X/Twitter posts
- **Search & Filter**: Full-text search across original messages and summaries
- **Export Functionality**: Export filtered data as CSV or JSON
- **Graceful Degradation**: System remains functional even when Grok is unavailable
- **Automatic Sync**: Periodic synchronization for new messages

## Setup

### Prerequisites

- Python 3.9+
- PostgreSQL database
- Telegram API credentials
- Grok (xAI) API key

### Installation

1. Clone the repository
2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and configure:
```bash
cp .env.example .env
```

4. Update `.env` with your credentials:
- `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`: Get from https://my.telegram.org
- `TELEGRAM_PHONE`: Your phone number
- `TELEGRAM_CHANNEL_USERNAME`: Channel to import from
- `GROK_API_KEY`: Your xAI API key
- `DATABASE_URL`: PostgreSQL connection string

5. Initialize the database:
```bash
alembic upgrade head
```

## Usage

### Local Development

```bash
python -m src.main
```

Access the web interface at `http://localhost:8000`

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

## Railway Deployment

1. Create a new Railway project
2. Add PostgreSQL database service
3. Set environment variables in Railway dashboard
4. Deploy using Railway CLI or GitHub integration

```bash
railway up
```

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