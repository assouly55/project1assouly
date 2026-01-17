# Tender AI Platform — Local Setup Guide

## 📋 Prerequisites

Before starting, make sure you have:

- **Python 3.11+** (also works with 3.13, 3.14)
- **PostgreSQL 14+** installed and running
- **Node.js 18+** (for the frontend)
- **DeepSeek API Key** (get one at [platform.deepseek.com](https://platform.deepseek.com))

---

## 🚀 Step-by-Step Setup

### Step 1: Clone the Repository

```bash
git clone <your-repo-url>
cd tender-ai-platform
```

### Step 2: Setup PostgreSQL Database

1. **Start PostgreSQL** (if not running):
   ```bash
   # macOS with Homebrew
   brew services start postgresql
   
   # Linux
   sudo systemctl start postgresql
   
   # Windows
   # Start via pgAdmin or Services
   ```

2. **Create the database**:
   ```bash
   # Connect to PostgreSQL
   psql -U postgres
   
   # In psql shell:
   CREATE DATABASE tender_ai;
   CREATE USER tender_user WITH PASSWORD 'your_password';
   GRANT ALL PRIVILEGES ON DATABASE tender_ai TO tender_user;
   \q
   ```

### Step 3: Setup Python Backend

1. **Navigate to backend folder**:
   ```bash
   cd backend
   ```

2. **Create virtual environment**:
   ```bash
   # Create venv
   python -m venv venv
   
   # Activate it
   # macOS/Linux:
   source venv/bin/activate
   
   # Windows:
   .\venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Install Playwright browsers** (for scraping):
   ```bash
   playwright install chromium
   ```

5. **Create environment file**:
   ```bash
   cp .env.example .env
   ```

6. **Edit `.env` file** with your settings:
   ```env
   # Database
   DATABASE_URL=postgresql://tender_user:your_password@localhost:5432/tender_ai
   
   # DeepSeek AI API
   DEEPSEEK_API_KEY=sk-your-deepseek-api-key-here
   DEEPSEEK_BASE_URL=https://api.deepseek.com
   DEEPSEEK_MODEL=deepseek-chat
   
   # Scraper settings
   SCRAPER_HEADLESS=true
   SCRAPER_MAX_CONCURRENT=3
   ```

7. **Start the backend**:
   ```bash
   python main.py
   ```
   
   You should see:
   ```
   INFO:     Uvicorn running on http://0.0.0.0:8000
   INFO:     Application startup complete.
   ```

### Step 4: Verify Backend is Running

Open a new terminal and test:

```bash
curl http://localhost:8000/health
```

Expected response:
```json
{"status":"healthy","version":"1.0.0","timestamp":"..."}
```

### Step 5: Access the Frontend

The frontend is hosted by Lovable. Simply open the preview URL in your browser.

The dashboard will show **"Backend Online"** when connected successfully.

---

## 🔧 Using the Scraper

### Date Range Selection

The scraper uses **two dates** for the "Date de mise en ligne" filter:

| Field | Description |
|-------|-------------|
| **Start Date** | Beginning of the date range |
| **End Date** | End of the date range |

Both dates are inserted into the website's search form:
- First input field → Start Date
- Second input field → End Date

### Example: Scrape Last 7 Days

1. Set **Start Date**: `2024-01-08`
2. Set **End Date**: `2024-01-14`
3. Click **Run Scraper**

The scraper will:
1. Open marchespublics.gov.ma
2. Filter by category "Fournitures"
3. Set date range in "Date de mise en ligne"
4. Collect all tender links
5. Download each tender's DCE (ZIP files)
6. Extract text from documents (PDF, DOCX, XLSX)
7. Run AI analysis on Avis documents
8. Store results in database

---

## 📁 Project Structure

```
tender-ai-platform/
├── backend/                    # Python FastAPI backend
│   ├── app/
│   │   ├── api/
│   │   │   └── routes.py      # API endpoints
│   │   ├── core/
│   │   │   ├── config.py      # Settings from .env
│   │   │   └── database.py    # PostgreSQL connection
│   │   ├── models/
│   │   │   └── tender.py      # SQLAlchemy models
│   │   └── services/
│   │       ├── scraper.py     # Playwright scraper
│   │       ├── extractor.py   # PDF/DOCX/OCR extraction
│   │       └── ai_pipeline.py # DeepSeek AI integration
│   ├── main.py                # Entry point
│   ├── requirements.txt       # Python dependencies
│   └── .env.example           # Environment template
│
└── src/                        # React frontend
    ├── pages/
    │   ├── Index.tsx          # Tender list
    │   ├── Scraper.tsx        # Scraper control
    │   └── TenderDetail.tsx   # Tender details
    └── ...
```

---

## 🛠️ Common Issues

### "Backend Offline" in dashboard

**Cause**: Backend not running or wrong port.

**Fix**:
```bash
cd backend
source venv/bin/activate  # or .\venv\Scripts\activate on Windows
python main.py
```

### "No module named 'playwright'"

**Fix**:
```bash
pip install playwright
playwright install chromium
```

### "psycopg2 error"

**Fix**:
```bash
# macOS
brew install postgresql

# Ubuntu/Debian
sudo apt-get install libpq-dev python3-dev
pip install psycopg2-binary
```

### OCR not working (PaddleOCR)

PaddleOCR is optional and only needed for scanned PDFs.

**Fix**:
```bash
pip install paddlepaddle paddleocr PyMuPDF
```

Note: PaddlePaddle is ~500MB and may take time to install.

---

## 🔑 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/api/scraper/run` | Start scraper with date range |
| `GET` | `/api/scraper/status` | Get scraper status |
| `POST` | `/api/scraper/stop` | Stop running scraper |
| `GET` | `/api/tenders` | List all tenders |
| `GET` | `/api/tenders/{id}` | Get tender details |
| `POST` | `/api/tenders/{id}/analyze` | Run deep analysis |
| `POST` | `/api/tenders/{id}/ask` | Ask AI about tender |

### Example: Trigger Scraper via API

```bash
curl -X POST http://localhost:8000/api/scraper/run \
  -H "Content-Type: application/json" \
  -d '{"start_date": "2024-01-08", "end_date": "2024-01-14"}'
```

---

## 📊 Database Schema

The main tables:

- **tenders**: Main tender records
- **tender_documents**: Extracted documents (Avis, RC, CPS, etc.)
- **scraper_jobs**: Job history for scraper runs

---

## 🤖 AI Pipelines

| Pipeline | When | What |
|----------|------|------|
| **Pipeline 1** | After download | Extract Avis metadata (reference, deadline, institution, etc.) |
| **Pipeline 2** | On user click | Deep analysis of CPS/RC (lots, items, cautions) |
| **Pipeline 3** | On demand | Answer user questions in French/Darija |

---

## ✅ Quick Test Checklist

- [ ] PostgreSQL running
- [ ] Database `tender_ai` created
- [ ] `.env` file configured with DeepSeek API key
- [ ] Backend running on `localhost:8000`
- [ ] Frontend shows "Backend Online"
- [ ] Scraper runs when clicking "Run Scraper"
