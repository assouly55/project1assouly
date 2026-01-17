# Tender AI Platform - Backend

Python FastAPI backend for the Moroccan Government Tender Analysis Platform.

## Requirements

- Python 3.11+ (compatible with 3.13, 3.14)
- PostgreSQL 14+
- Playwright browsers

## Quick Start

### 1. Create Virtual Environment

```bash
cd backend
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or
.\venv\Scripts\activate  # Windows
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Install Playwright Browsers

```bash
playwright install chromium
```

### 4. Setup PostgreSQL

```bash
# Create database
createdb tender_ai

# Or using psql
psql -c "CREATE DATABASE tender_ai;"
```

### 5. Configure Environment

```bash
cp .env.example .env
# Edit .env with your settings
```

**Required: Set your DeepSeek API key:**
```
DEEPSEEK_API_KEY=your_key_here
```

### 6. Run Server

```bash
python main.py
# or
uvicorn main:app --reload --port 8000
```

Server will be available at:
- API: http://localhost:8000
- Docs: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## API Endpoints

### Scraper

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/scraper/run` | Start scraper job |
| GET | `/api/scraper/status` | Get scraper status |
| POST | `/api/scraper/stop` | Stop running scraper |

### Tenders

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/tenders` | List tenders (with filters) |
| GET | `/api/tenders/{id}` | Get tender details |
| POST | `/api/tenders/{id}/analyze` | Trigger deep analysis |
| POST | `/api/tenders/{id}/ask` | Ask AI about tender |

## Architecture

```
backend/
├── main.py                 # FastAPI entry point
├── requirements.txt        # Python dependencies
├── .env.example           # Environment template
├── app/
│   ├── core/
│   │   ├── config.py      # Settings from env vars
│   │   └── database.py    # SQLAlchemy setup
│   ├── models/
│   │   ├── __init__.py
│   │   └── tender.py      # Database models
│   ├── services/
│   │   ├── scraper.py     # Headless Playwright scraper
│   │   ├── extractor.py   # PDF/DOCX/XLSX extraction
│   │   └── ai_pipeline.py # DeepSeek integration
│   └── api/
│       └── routes.py      # API endpoints
```

## Key Features

- **Headless scraping**: No GUI, runs on servers
- **Memory-only**: All file processing in `io.BytesIO`, no disk writes
- **PaddleOCR service**: External OCR service for scanned PDFs (see `paddleocr_service/`)
- **AI extraction**: DeepSeek API for metadata extraction
- **Full traceability**: Every field tracks its source document

## Development

### Running Tests

```bash
pytest tests/
```

### Database Migrations

```bash
# Generate migration
alembic revision --autogenerate -m "Description"

# Apply migrations
alembic upgrade head
```

## Production Deployment

1. Set `DEBUG=false` in `.env`
2. Set `SCRAPER_HEADLESS=true`
3. Configure proper PostgreSQL credentials
4. Use gunicorn: `gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker`
