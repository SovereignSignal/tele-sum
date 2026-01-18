# Tele-Sum Technical Architecture Overview

## 🏗️ System Architecture on Railway

Tele-Sum is architected specifically for Railway's platform, leveraging its managed infrastructure for reliable, scalable operation.

```
┌─────────────────────────────────────────────────────────────┐
│                     Railway Platform                         │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────────┐         ┌──────────────────┐         │
│  │                  │         │                  │          │
│  │  FastAPI App     │◄────────┤  PostgreSQL DB   │          │
│  │  (Main Service)  │         │  (Railway Addon)  │          │
│  │                  │         │                  │          │
│  └────────┬─────────┘         └──────────────────┘          │
│           │                                                  │
│           ├──────────► Railway Cron Jobs                     │
│           │            (Sync every 6 hours)                  │
│           │                                                  │
│           └──────────► Health Checks                         │
│                       (/health endpoint)                     │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    External Integrations
           ┌──────────────────┴──────────────────┐
           │                                      │
    ┌──────▼────────┐                  ┌─────────▼────────┐
    │               │                  │                  │
    │  Telegram API │                  │    Grok AI API   │
    │               │                  │  (X/Twitter)     │
    └───────────────┘                  └──────────────────┘
```

## 🔄 Data Flow & Processing Pipeline

### 1. Message Import Flow

```
Telegram Channel → Telethon Client → Message Parser → Database Storage
                                           │
                                           ▼
                                    URL Extraction
                                           │
                                           ▼
                              ┌────────────┴────────────┐
                              │                         │
                    Twitter/X URLs              Other URLs
                              │                         │
                              ▼                         ▼
                        Grok Direct               Web Scraper
                          Access                       │
                              │                         │
                              └────────────┬────────────┘
                                           │
                                           ▼
                                   Grok AI Analysis
                                           │
                                           ▼
                                 Summary + Categorization
                                           │
                                           ▼
                                   Database Update
```

### 2. User-Defined Category System

```
User Interface
      │
      ▼
Category Management
      │
      ├──► Create Category (name, description)
      ├──► Edit Category
      ├──► Delete Category
      │
      ▼
PostgreSQL Categories Table
      │
      ▼
Dynamic Prompt Generation
      │
      ▼
Grok AI Classification
```

## 💪 Graceful Degradation Architecture

The system is designed with multiple fallback layers to ensure continuous operation even when external services fail.

### Degradation Levels

| Level | Status | Features Available | User Experience |
|-------|--------|-------------------|-----------------|
| **Full Operation** | All services active | • AI summaries<br>• Categorization<br>• Import<br>• Search<br>• Export | Complete functionality |
| **Grok Unavailable** | Grok API down/rate limited | • Import continues<br>• Search (original text)<br>• Export<br>• Browse by date | Messages stored without summaries |
| **Telegram Unavailable** | Telegram API issues | • Search existing data<br>• View summaries<br>• Export<br>• Categories | No new imports, existing data accessible |
| **Database Only** | External APIs down | • Browse existing data<br>• Search<br>• Export cached content | Read-only mode with full historical data |

### Implementation Details

```python
# Pseudo-code showing graceful degradation pattern
async def process_message(message):
    # Always store original message
    db.store_message(message)
    
    # Try AI enhancement (non-blocking)
    try:
        summary = await grok.analyze(message)
        db.update_summary(message.id, summary)
    except GrokError as e:
        # Log but don't fail
        logger.warning(f"Grok failed: {e}")
        db.mark_for_retry(message.id)
    
    # Message is searchable regardless
    return message
```

## 🗄️ Database Schema

### Core Tables

```sql
-- Users (for multi-tenant support)
users
├── id (UUID, PK)
├── email
├── created_at
└── settings (JSON)

-- User-defined categories
categories
├── id (UUID, PK)
├── user_id (FK → users)
├── name (VARCHAR)
├── description (TEXT, nullable)
├── color (VARCHAR, nullable)
└── created_at

-- Telegram messages
messages
├── id (UUID, PK)
├── telegram_id (BIGINT, unique)
├── channel_username (VARCHAR)
├── content (TEXT)
├── timestamp (TIMESTAMP)
├── urls (JSON array)
├── created_at
└── updated_at

-- AI analysis results
summaries
├── id (UUID, PK)
├── message_id (FK → messages)
├── summary_text (TEXT)
├── category_id (FK → categories, nullable)
├── confidence (ENUM: confident/uncertain)
├── entities (JSON)
├── processing_status (ENUM)
├── error_details (JSON, nullable)
├── created_at
└── retry_count (INT, default 0)

-- Indices for performance
CREATE INDEX idx_messages_timestamp ON messages(timestamp);
CREATE INDEX idx_messages_channel ON messages(channel_username);
CREATE INDEX idx_summaries_category ON summaries(category_id);
CREATE INDEX idx_summaries_status ON summaries(processing_status);
```

## 🚀 Railway-Specific Features

### Auto-Scaling Configuration

```json
{
  "deploy": {
    "replicas": {
      "min": 1,
      "max": 3
    },
    "resources": {
      "memory": "512Mi",
      "cpu": "0.5"
    }
  }
}
```

### Health Monitoring

- **Endpoint**: `/health`
- **Checks**:
  - Database connectivity
  - Telegram client status
  - Grok API availability
  - Memory usage
  - Queue depth

### Scheduled Jobs

```python
# Railway cron job configuration
SYNC_SCHEDULE = {
    "sync_new_messages": "0 */6 * * *",  # Every 6 hours
    "retry_failed_summaries": "0 2 * * *",  # Daily at 2 AM
    "cleanup_old_sessions": "0 3 * * 0"  # Weekly Sunday 3 AM
}
```

## 🔐 Security Considerations

### API Key Management

- All sensitive keys stored as Railway environment variables
- Keys never exposed in logs or error messages
- Separate keys for development and production

### Data Protection

- PostgreSQL connection uses SSL
- User sessions encrypted with SECRET_KEY
- No storage of Telegram passwords (uses session files)
- Rate limiting on all API endpoints

### Error Handling

```python
# Safe error responses
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    # Log full error internally
    logger.error(f"Unhandled error: {exc}", exc_info=True)
    
    # Return safe error to client
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"}
    )
```

## 📊 Performance Optimization

### Database Optimizations

- Compound indices for common query patterns
- JSON columns for flexible metadata storage
- Pagination for large result sets
- Connection pooling with SQLAlchemy

### Caching Strategy

- In-memory cache for categories (changes rarely)
- PostgreSQL as primary cache for AI summaries
- No external Redis needed (Railway PostgreSQL is sufficient)

### Batch Processing

```python
# Efficient batch processing for initial import
BATCH_SIZE = 50  # Messages per batch
GROK_BATCH_SIZE = 10  # Parallel Grok requests

async def batch_import():
    async for batch in get_message_batches(BATCH_SIZE):
        # Process in parallel
        await asyncio.gather(*[
            process_message(msg) for msg in batch
        ])
```

## 🔍 Monitoring & Observability

### Key Metrics

- **Import Rate**: Messages/hour imported
- **AI Success Rate**: % of messages successfully summarized
- **Category Distribution**: Messages per category
- **Search Performance**: Query response time
- **API Usage**: Grok API calls and costs

### Railway Dashboard Integration

```python
# Expose metrics for Railway monitoring
@app.get("/metrics")
async def get_metrics():
    return {
        "messages_total": await db.count_messages(),
        "summaries_success": await db.count_successful_summaries(),
        "summaries_pending": await db.count_pending_summaries(),
        "categories_count": await db.count_categories(),
        "grok_requests_today": await get_grok_usage_today()
    }
```

## 🚢 Deployment Best Practices

### Railway Configuration

1. **Start Small**: Begin with minimal resources, scale as needed
2. **Monitor Costs**: Set up Grok API spending alerts
3. **Use Railway Volumes**: For persistent Telegram session storage
4. **Enable Backups**: Regular PostgreSQL backups via Railway

### Progressive Feature Rollout

1. **Phase 1**: Deploy with database only
2. **Phase 2**: Enable Telegram import
3. **Phase 3**: Add Grok AI summaries
4. **Phase 4**: Enable automatic synchronization

### Rollback Strategy

```bash
# Railway makes rollbacks simple
railway rollback

# Or deploy specific version
railway deploy --ref v1.2.3
```

## 📝 Development Workflow

### Local Development with Railway

```bash
# Use Railway's local environment
railway run python -m src.main

# Access Railway database locally
railway run alembic upgrade head

# Test with production-like environment
railway run pytest
```

### CI/CD Pipeline

```yaml
# GitHub Actions integration with Railway
name: Deploy to Railway
on:
  push:
    branches: [main]
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: railwayapp/deploy-action@v1
        with:
          service: tele-sum
```

## 🎯 Future Enhancements

### Planned Features

- **Multi-channel support**: Import from multiple channels
- **Real-time updates**: WebSocket for live message streaming
- **Advanced analytics**: Trend analysis and insights
- **Team collaboration**: Shared categories and annotations
- **API access**: REST API for third-party integrations

### Scalability Roadmap

- **Phase 1**: Single user, single channel (current)
- **Phase 2**: Multi-user with isolated data
- **Phase 3**: Team workspaces with shared channels
- **Phase 4**: Enterprise features with SSO

## 📚 Related Documentation

- [Vision & Overview](VISION.md)
- [Railway Deployment Guide](RAILWAY_DEPLOYMENT.md)
- [Database Schema](DATABASE_SCHEMA.md)
- [Processing Pipeline](PROCESSING.md)
- [Hybrid Bot Setup](HYBRID_SETUP.md)
- [Scripts Reference](SCRIPTS.md)
- [AI Assistant Guide](CLAUDE.md)
