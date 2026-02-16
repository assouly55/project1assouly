# Tender AI Platform

> **Automated procurement intelligence for Moroccan public tenders** — scrapes, downloads, extracts, classifies, and analyzes tender documents from [marchespublics.gov.ma](https://www.marchespublics.gov.ma) using AI.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Features](#features)
  - [Web Scraper](#1-web-scraper)
  - [Document Processing Pipeline](#2-document-processing-pipeline)
  - [AI Analysis Pipelines](#3-ai-analysis-pipelines)
  - [Ask AI (Chat)](#4-ask-ai-chat)
  - [Technical Specs Extraction](#5-technical-specs-extraction)
  - [Contract Details Extraction](#6-contract-details-extraction)
  - [Category Classification](#7-category-classification)
  - [Document Downloads](#8-document-downloads)
  - [Single Tender Import](#9-single-tender-import)
- [Tech Stack](#tech-stack)
- [Frontend Pages](#frontend-pages)
- [API Reference](#api-reference)
- [Database Schema](#database-schema)
- [Document Types](#document-types)
- [Setup Guide](#setup-guide)
- [Configuration](#configuration)

---

## Overview

The Tender AI Platform is a full-stack application that automates the entire lifecycle of Moroccan public tender analysis:

1. **Scrape** — Playwright-based headless browser navigates marchespublics.gov.ma, filters by category ("Fournitures") and date range, collects all tender links
2. **Download** — For each tender, fills the download form and retrieves the DCE (Dossier de Consultation des Entreprises) ZIP archive
3. **Extract** — Processes all documents in the ZIP: PDF (digital + scanned/OCR), DOCX, XLSX, XLS, CSV
4. **Analyze** — Multi-phase AI pipeline (DeepSeek) extracts structured metadata, bordereau items, contract details, and categories
5. **Query** — Interactive Ask AI chat lets users query any tender's documents in French/Arabic/Darija with citations

---

## Architecture

```
┌─────────────────────────────────────────────┐
│              React Frontend                  │
│   Vite · TypeScript · Tailwind · shadcn/ui  │
│                                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────────┐ │
│  │  Index   │ │ Scraper  │ │ TenderDetail │ │
│  │ (list)   │ │ (control)│ │  (detail+AI) │ │
│  └──────────┘ └──────────┘ └──────────────┘ │
└──────────────────┬──────────────────────────┘
                   │ REST API
┌──────────────────▼──────────────────────────┐
│           Python FastAPI Backend             │
│                                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────────┐ │
│  │ Scraper  │ │Extractor │ │ AI Pipeline  │ │
│  │(Playwright)│(PDF/OCR) │ │ (DeepSeek)   │ │
│  └──────────┘ └──────────┘ └──────────────┘ │
│  ┌──────────┐ ┌──────────┐ ┌──────────────┐ │
│  │ Article  │ │  Smart   │ │  Technical   │ │
│  │ Indexer  │ │ Selector │ │  Pages Ext.  │ │
│  └──────────┘ └──────────┘ └──────────────┘ │
└──────────────────┬──────────────────────────┘
                   │
         ┌─────────▼──────────┐
         │    PostgreSQL       │
         │   (JSONB columns)   │
         └─────────────────────┘
```

---

## Features

### 1. Web Scraper

- **Headless Playwright browser** targets marchespublics.gov.ma
- Filters by **category** (default: "Fournitures" / code `2`) and **date range** ("Date de mise en ligne")
- Configurable **start date** and **end date** with form auto-fill
- Collects all tender links matching the filter criteria
- For each tender:
  - Scrapes **website metadata** directly from the consultation page (reference, deadline, subject, contact info)
  - Fills the download form (nom, prénom, email) to retrieve the **DCE ZIP archive**
  - Handles nested ZIPs (extracts ZIPs within ZIPs recursively)
- **Concurrency control**: sequential processing (`MAX_CONCURRENT_TENDERS = 1`) for stability
- **Real-time progress**: logs streamed to the frontend terminal component
- **Stop/cancel** support mid-run
- Runs in a **separate thread** with its own event loop (required for Playwright on Windows)

### 2. Document Processing Pipeline

- **Supported formats**: PDF, DOCX, XLSX, XLS, CSV
- **Document classification** via `FileDetector`:
  - Recognizes: AVIS, RC, CPS, BPDE (Bordereau), AE (Acte d'Engagement), CCAG, CCTP, BQ, DQE, DSH, and more
  - Uses filename patterns + first-page content analysis
  - AI-assisted classification for ambiguous files
- **PDF text extraction**:
  - **Digital PDFs**: direct text extraction via PyPDF
  - **Scanned PDFs**: Tesseract OCR with French language (`fra`)
  - **Table pages**: Azure Document Intelligence (primary) or coordinate-based OpenCV+Tesseract (fallback) for high-fidelity structured table extraction
- **Bordereau detection & prioritization**:
  - Files identified as BPDE (`.xlsx`, `.xls`, `.csv`, or containing "bordereau des prix" keywords) are processed first
  - CPS files processed second (often contain embedded bordereau tables)
  - Once a bordereau is successfully found in any file, **Azure DI fallback is skipped** for remaining files to save cost and time
- **Two-stage execution**:
  - **Stage 1**: Process bordereau-priority files → trigger early AI extraction callback → flush to DB immediately so frontend can display data before full processing completes
  - **Stage 2**: Process remaining files
- **Article indexing**: CPS and RC documents are indexed by article number for fast AI lookups
- **Sequential processing** with max 2 parallel OCR workers per document for resource efficiency

### 3. AI Analysis Pipelines

All AI calls use **DeepSeek** via OpenAI-compatible API.

| Phase | Name | Trigger | Description |
|-------|------|---------|-------------|
| **Phase 1** | Primary Metadata | After scraping | Extracts from website first, then falls back to AVIS/RC/CPS documents if incomplete. Fields: reference, type de procédure, organisme acheteur, lieu d'exécution, date limite, lieu d'ouverture des plis, objet du marché, estimation totale, lots |
| **Phase 2** | Bordereau Extraction | After download | Two-step AI process: (1) identify raw bordereau content, (2) parse into structured items (numéro prix, désignation, unité, quantité). Smart retry with focused extraction if initial pass fails |
| **Phase 2b** | Contract Details | After bordereau | Extracts: délai d'exécution, pénalité de retard (taux + plafond), mode d'attribution, caution définitive (taux + base). Auto-calculates caution montant estimé |
| **Phase 3** | Ask AI | On demand | Answers user questions about specific tender documents with citations. Supports French, Arabic, and Darija |
| **Phase 4** | Category Classification | After Phase 1 | Classifies tender into hierarchical categories (main > sub > item) using bordereau items for precision, with confidence scores |

#### Phase 1 Metadata Merge Strategy

- Website metadata is **authoritative** (highest priority)
- If website data is incomplete, the system falls back to document extraction (AVIS → RC → CPS)
- `merge_phase1_metadata()` combines sources without overwriting existing values
- `is_metadata_complete()` checks if all critical fields are populated

#### Phase 2 Bordereau Smart Extraction

- **Step 1**: AI identifies which pages/sections contain the Bordereau des Prix
- **Step 2**: AI parses identified content into structured `{numero_prix, designation, unite, quantite}` per lot
- **Fallback chain**: Priority files → All documents → Focused retry
- Results include `_completeness` metadata (`is_complete`, `total_articles`, `lots_count`)

### 4. Ask AI (Chat)

- **Interactive Q&A** on any analyzed tender
- **Smart article selection**: AI identifies which CPS/RC articles are relevant to the user's question, then fetches only those articles for context (saves tokens)
- **Clarification flow**: If the question is ambiguous, the AI returns `needs_clarification: true` with suggested `clarification_options` for the user to pick from
- **Citations**: Every answer includes `[DocumentName, Article/Section X]` references
- **Follow-up questions**: AI suggests 2 relevant follow-up questions
- **Completeness indicator**: Responses tagged as `COMPLETE`, `PARTIAL`, or `NOT_FOUND`
- **Multi-language**: Responds in the same language as the question (French, Arabic, Darija)

### 5. Technical Specs Extraction

- **On-demand** extraction of technical specification pages from tender documents
- **Flow**:
  1. AI analyzes stored document texts to identify which document contains technical specs (typically the CPS)
  2. AI identifies exact page ranges with technical attributes
  3. System re-downloads the tender ZIP via Playwright (stateless, no permanent storage)
  4. Converts DOCX → PDF using LibreOffice (with Windows `soffice.exe` path detection) or PyMuPDF fallback
  5. Extracts specific pages into a single PDF
  6. Returns as **base64 string** for frontend PDF viewer
- Supports PDF, DOCX, and legacy DOC formats
- Uses 60,000-character AI context window
- Distinguishes between Bordereau data (prices/quantities) and actual Technical Specifications (norms/dimensions)

### 6. Contract Details Extraction

Extracted from CPS and RC documents during Phase 2b:

| Field | Format | Example |
|-------|--------|---------|
| **Délai d'exécution** | Standardized period | `30 Jours`, `3 Mois`, `1 Ans` |
| **Pénalité de retard** | Percentage/day + plafond | `0.1% /jour` · Plafond: `10%` |
| **Mode d'attribution** | Text | `Par lot - offre la plus avantageuse` |
| **Caution définitive** | Percentage + estimated amount | `3%` · ≈ `30,000.00 DH` |

- **Caution montant estimé** is automatically calculated: `Estimation Totale × (Caution % / 100)`
- Frontend displays **standardized placeholders** when data is missing (e.g., `— Jours`, `— %`)
- Raw AI output is normalized (fractions like `1/1000` → `0.1%`, per-mille `‰` → `%`)

### 7. Category Classification

- Hierarchical classification: **Main Category > Subcategory > Item**
- Uses a predefined category tree (`categories.json`)
- Classification uses both avis metadata AND bordereau items (up to 20) for precision
- Each category includes a **confidence score** (0–1) and optional reasoning
- Displayed as a breadcrumb path with color-coded confidence

### 8. Document Downloads

- **Full ZIP download**: Re-downloads the entire DCE archive from the source website
- **Individual file download**: Downloads a specific file from the tender
- **Stateless**: Uses Playwright to re-fetch on-the-fly, no permanent server-side storage
- Streams content directly to the user

### 9. Single Tender Import

- Paste a direct URL from marchespublics.gov.ma
- System imports the tender, downloads documents, and auto-triggers full analysis
- Navigates to the tender detail page with `?analyze=true` flag

---

## Tech Stack

### Frontend
| Technology | Purpose |
|-----------|---------|
| **React 18** | UI framework |
| **TypeScript** | Type safety |
| **Vite** | Build tool & dev server |
| **Tailwind CSS** | Utility-first styling |
| **shadcn/ui** | Component library (Radix UI primitives) |
| **TanStack React Query** | Data fetching, caching, polling |
| **React Router v6** | Client-side routing |
| **Recharts** | Charts & data visualization |
| **Lucide React** | Icon library |
| **Sonner** | Toast notifications |
| **Framer Motion** | Animations (via shadcn) |

### Backend
| Technology | Purpose |
|-----------|---------|
| **Python 3.11+** | Runtime (compatible with 3.12, 3.13) |
| **FastAPI** | Web framework |
| **Uvicorn** | ASGI server |
| **SQLAlchemy 2.0** | ORM |
| **PostgreSQL** | Database (JSONB columns for flexible metadata) |
| **Playwright** | Headless browser for scraping & downloads |
| **PyPDF** | Digital PDF text extraction |
| **Tesseract (pytesseract)** | OCR for scanned PDFs |
| **OpenCV** | Table detection & coordinate-based OCR |
| **Azure Document Intelligence** | High-fidelity table extraction (bordereau) |
| **PyMuPDF (fitz)** | PDF manipulation, page extraction |
| **python-docx** | DOCX text extraction |
| **openpyxl / pandas / xlrd** | XLSX/XLS/CSV processing |
| **pdf2image + Pillow** | PDF to image conversion for OCR |
| **DeepSeek API** | AI text analysis (OpenAI-compatible client) |
| **Loguru** | Structured logging |
| **Pydantic v2** | Data validation & settings |
| **APScheduler** | Task scheduling |

---

## Frontend Pages

### `/` — Tender List (Index)

- Displays all analyzed tenders in a **table** with key metadata columns
- **Search bar**: filter by reference, subject, organisme
- **Single tender import**: paste a URL to import and auto-analyze
- Shows total count of analyzed tenders
- Click any tender to navigate to detail page
- Contract details shown inline: délai, pénalité, caution, mode d'attribution

### `/scraper` — Scraper Control

- **Backend status indicator**: Online (green) / Offline (red)
- **Date range picker**: Start Date and End Date with auto-validation
- **Date preview**: Shows formatted range with day count
- **Run/Stop controls**: Start or cancel a scraper run
- **Real-time stats**: Total Found, Downloaded, Failed, Elapsed time
- **Terminal component**: Live-scrolling log output from the scraper

### `/tender/:id` — Tender Detail

- **Tabs**: Résumé · Lots & Articles · Documents · Ask AI · Specs Techniques
- **Résumé tab**:
  - All Phase 1 metadata fields (reference, organisme, procedure, deadline, lieu, etc.)
  - Contact administratif (parsed into structured name/email/phone)
  - Category breadcrumb with confidence
  - Contract details section (délai, pénalité, mode attribution, caution with calculated montant)
  - Estimation totale
- **Lots & Articles tab**:
  - Merged view combining Phase 1 lots with Phase 2 bordereau articles
  - Each lot card shows: numéro, objet, estimation, caution provisoire
  - Expandable article table: N° Prix, Désignation, Unité, Quantité
  - Completeness indicator
- **Documents tab**:
  - List of all extracted documents with type badges
  - Download individual files or full ZIP
  - File metadata (size, extraction method, page count)
- **Ask AI tab**:
  - Chat interface for querying tender documents
  - Clarification flow with clickable option pills
  - Follow-up question suggestions
  - Citation display
- **Specs Techniques tab**:
  - Triggers on-demand technical page extraction
  - Inline PDF viewer for extracted pages
  - Shows source document, page numbers, reasoning, and confidence
- **Auto-analysis**: Triggers Phase 2 automatically for LISTED tenders or via `?analyze=true` URL param
- **Progress overlay**: Animated progress bar during analysis with phase messages

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check — returns `{status, version, timestamp}` |
| `POST` | `/api/scraper/run` | Start scraper with `{start_date, end_date}` |
| `GET` | `/api/scraper/status` | Get scraper status, stats, and logs |
| `POST` | `/api/scraper/stop` | Stop running scraper |
| `POST` | `/api/scraper/import-single` | Import single tender from `{url}` |
| `GET` | `/api/tenders` | List tenders with filters: `q`, `status`, `date_from`, `date_to`, `page`, `per_page` |
| `GET` | `/api/tenders/{id}` | Get full tender details with documents |
| `POST` | `/api/tenders/{id}/analyze` | Trigger Phase 2 deep analysis |
| `POST` | `/api/tenders/{id}/ask` | Ask AI about tender with `{question}` |
| `POST` | `/api/tenders/{id}/technical-pages` | Extract technical specification pages |
| `GET` | `/api/tenders/{id}/download-zip` | Download full DCE ZIP archive |
| `GET` | `/api/tenders/{id}/download-file/{filename}` | Download individual file |

---

## Database Schema

### `tenders` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID (PK) | Auto-generated |
| `external_reference` | VARCHAR(255) | Tender reference from source |
| `source_url` | TEXT | Original URL on marchespublics.gov.ma |
| `status` | ENUM | `PENDING` → `LISTED` → `ANALYZED` → `ERROR` |
| `download_date` | VARCHAR(10) | Scrape date (YYYY-MM-DD) |
| `avis_metadata` | JSONB | Phase 1 extracted metadata |
| `bordereau_metadata` | JSONB | Phase 2 bordereau items |
| `universal_metadata` | JSONB | Legacy alias for bordereau_metadata |
| `categories` | JSONB | Phase 4 classification results |
| `contract_details` | JSONB | Phase 2b contract details |
| `article_index` | JSONB | CPS/RC article index for smart lookups |
| `error_message` | TEXT | Error details if status = ERROR |
| `scraped_at` | TIMESTAMPTZ | When scraped |
| `created_at` | TIMESTAMPTZ | Record creation |
| `updated_at` | TIMESTAMPTZ | Last update |

### `tender_documents` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID (PK) | Auto-generated |
| `tender_id` | UUID (FK) | References `tenders.id` |
| `document_type` | VARCHAR(50) | AVIS, RC, CPS, BPDE, AE, etc. |
| `filename` | VARCHAR(500) | Original filename |
| `raw_text` | TEXT | Extracted text content |
| `page_count` | INTEGER | Number of pages |
| `article_index` | JSONB | Per-document article index |
| `extraction_method` | ENUM | `DIGITAL` or `OCR` |
| `file_size_bytes` | INTEGER | File size |
| `mime_type` | VARCHAR(100) | MIME type |
| `extracted_at` | TIMESTAMPTZ | Extraction timestamp |

### `scraper_jobs` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID (PK) | Auto-generated |
| `target_date` | VARCHAR(50) | Date range string |
| `status` | VARCHAR(50) | RUNNING, COMPLETED, FAILED, STOPPED |
| `total_found` | INTEGER | Tenders found |
| `downloaded` | INTEGER | Successfully downloaded |
| `failed` | INTEGER | Failed downloads |
| `extracted` | INTEGER | Successfully extracted |
| `started_at` | TIMESTAMPTZ | Job start time |
| `completed_at` | TIMESTAMPTZ | Job end time |
| `elapsed_seconds` | INTEGER | Total elapsed time |
| `current_phase` | VARCHAR(100) | Current processing phase |
| `error_log` | TEXT | Error details |

---

## Document Types

The system recognizes and classifies the following document types:

| Code | Full Name | Description |
|------|-----------|-------------|
| `AVIS` | Avis d'Appel d'Offres | Tender notice / announcement |
| `RC` | Règlement de Consultation | Consultation rules / evaluation criteria |
| `CPS` | Cahier des Prescriptions Spéciales | Special specifications (main technical document) |
| `BPDE` | Bordereau des Prix - Détail Estimatif | Price schedule with quantities |
| `AE` | Acte d'Engagement | Commitment form |
| `CCAG` | Cahier des Clauses Administratives Générales | General admin conditions |
| `CCTP` | Cahier des Clauses Techniques Particulières | Technical conditions |
| `BQ` | Bordereau des Quantités | Quantity schedule |
| `DQE` | Devis Quantitatif Estimatif | Estimated quantities |
| `DSH` | Décomposition du Sous-détail | Price breakdown |
| `ANNEXE` | Annexe | Appendix documents |
| `OTHER` | Other | Identified but uncategorized |
| `UNKNOWN` | Unknown | Could not be classified |

---

## Setup Guide

See [SETUP_GUIDE.md](./SETUP_GUIDE.md) for detailed installation instructions including:

- PostgreSQL database setup
- Python virtual environment & dependencies
- Playwright browser installation
- Environment variable configuration
- Frontend connection

### Quick Start

```bash
# Backend
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env  # Edit with your DB URL and DeepSeek API key
python main.py

# Frontend (served by Lovable or locally)
npm install && npm run dev
```

---

## Configuration

Environment variables (in `backend/.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/tender_ai` | PostgreSQL connection string |
| `DEEPSEEK_API_KEY` | (required) | DeepSeek API key for AI analysis |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/v1` | DeepSeek API endpoint |
| `DEEPSEEK_MODEL` | `deepseek-chat` | Model name |
| `AZURE_DI_ENDPOINT` | (optional) | Azure Document Intelligence endpoint for table OCR |
| `AZURE_DI_KEY` | (optional) | Azure Document Intelligence API key |
| `SCRAPER_HEADLESS` | `false` | Run browser in headless mode |
| `SCRAPER_MAX_CONCURRENT` | `5` | Max concurrent scraper workers |
| `SCRAPER_RETRY_ATTEMPTS` | `3` | Retry attempts per tender |
| `SCRAPER_TIMEOUT_PAGE` | `30000` | Page load timeout (ms) |
| `SCRAPER_TIMEOUT_DOWNLOAD` | `60000` | Download timeout (ms) |
| `TARGET_HOMEPAGE` | `https://www.marchespublics.gov.ma/pmmp/` | Target scraping URL |
| `FORM_NOM` / `FORM_PRENOM` / `FORM_EMAIL` | Pre-filled | Download form credentials |
| `CATEGORY_FILTER` | `2` | Category code (2 = Fournitures) |
| `TEST_MODE` | `true` | Run immediately vs. scheduled |

---

## License

Private project — all rights reserved.
