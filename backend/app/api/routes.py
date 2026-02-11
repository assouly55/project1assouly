"""
Tender AI Platform - API Routes
FastAPI endpoints for frontend integration
"""

import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel
from uuid import UUID

from app.core.database import get_db
from app.models import Tender, TenderDocument, ScraperJob, TenderStatus
from app.services.scraper import TenderScraper, ScraperProgress, WebsiteMetadata, DownloadedTender
from app.services.extractor import (
    DocumentType as ExtractorDocumentType,
    ExtractionResult,
    ExtractionMethod,
)
from app.services.pipeline_processor import (
    process_tender_documents,
    extract_all_nested_zips,
    ProcessedDocument,
)
from app.services.article_indexer import (
    get_verified_articles,
    slice_document_by_articles,
)
from app.services.phase1_merge import merge_phase1_metadata, is_metadata_complete
from app.services.ai_pipeline import ai_service

router = APIRouter()

# Global scraper state
_scraper_instance: Optional[TenderScraper] = None
_current_job_id: Optional[str] = None

# Concurrent processing settings
MAX_CONCURRENT_TENDERS = 1
MAX_CONCURRENT_EXTRACTION = 1


# ============================
# PYDANTIC MODELS
# ============================

class ScraperRunRequest(BaseModel):
    start_date: Optional[str] = None  # YYYY-MM-DD
    end_date: Optional[str] = None    # YYYY-MM-DD (defaults to start_date)


class ScraperStatusResponse(BaseModel):
    is_running: bool
    current_phase: str
    total_tenders: int
    downloaded: int
    failed: int
    elapsed_seconds: float
    last_run: Optional[str] = None


class TenderListParams(BaseModel):
    q: Optional[str] = None
    status: Optional[TenderStatus] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    page: int = 1
    per_page: int = 50


class AskAIRequest(BaseModel):
    question: str


class ClarificationOption(BaseModel):
    label: str
    value: str


class AskAIResponse(BaseModel):
    answer: str
    citations: List[dict]
    follow_up_questions: List[str] = []
    response_time_ms: Optional[int] = None
    needs_clarification: bool = False
    clarification_prompt: Optional[str] = None
    clarification_options: Optional[List[ClarificationOption]] = None


class AnalyzeResponse(BaseModel):
    success: bool
    phase: str
    message: str
    tender: Optional[dict] = None


# ============================
# HEALTH CHECK
# ============================

@router.get("/health")
def health_check():
    return {
        "status": "healthy",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat()
    }


# ============================
# SCRAPER ENDPOINTS
# ============================

@router.post("/api/scraper/run")
async def run_scraper(
    request: ScraperRunRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Trigger a manual scraper run with concurrent processing.
    
    Flow:
    1. Browser opens, collects tender links
    2. Process tenders with 5 concurrent workers
    3. Each tender: download ZIP, extract, index articles, store in DB
    4. Post avis_metadata immediately when website data is complete
    """
    global _scraper_instance, _current_job_id
    
    if _scraper_instance and _scraper_instance.progress.is_running:
        raise HTTPException(400, "Scraper is already running")
    
    # Default dates
    start = request.start_date or datetime.now().strftime("%Y-%m-%d")
    end = request.end_date or start
    
    # Create job record
    job = ScraperJob(
        target_date=f"{start} to {end}",
        status="RUNNING"
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    
    _current_job_id = str(job.id)
    
    # Run scraper in a separate thread (required for Playwright on Windows)
    import threading
    scraper_thread = threading.Thread(
        target=_run_scraper_sync,
        args=(str(job.id), start, end),
        daemon=True
    )
    scraper_thread.start()
    
    return {"job_id": str(job.id), "status": "started", "date_range": f"{start} to {end}"}


def _run_scraper_sync(job_id: str, start_date: str, end_date: str):
    """
    Run scraper in a separate thread with its own event loop.
    """
    global _scraper_instance
    
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(_run_scraper_async(job_id, start_date, end_date))
    finally:
        loop.close()


async def _process_single_tender(
    context,
    scraper: TenderScraper,
    tender_url: str,
    idx: int,
    semaphore: asyncio.Semaphore,
    db_session_factory,
    download_date: str
) -> Optional[str]:
    """
    Process a single tender: Website first → Bordereau → Files only if needed.
    
    Optimized flow:
    1. Scrape website metadata (usually complete)
    2. Extract Phase 1 from website data
    3. Download documents
    4. Run Phase 2 (Bordereau extraction) on CPS
    5. ONLY if Phase 1 incomplete, fallback to document extraction
    6. Mark as ANALYZED
    """
    from app.core.database import SessionLocal
    from loguru import logger
    
    async with semaphore:
        db = db_session_factory()
        try:
            # Step 1: Scrape website metadata
            tender_page = await context.new_page()
            try:
                from app.core.config import settings
                await tender_page.goto(tender_url, timeout=settings.SCRAPER_TIMEOUT_PAGE)
                website_metadata = await scraper.extract_website_metadata(tender_page)
            finally:
                await tender_page.close()
            
            if not website_metadata:
                logger.error(f"Failed to scrape metadata for tender #{idx}")
                return None
            
            tender_ref = website_metadata.reference_tender or f"tender_{idx}"
            logger.info(f"[{idx}] Scraped metadata: {tender_ref}")
            
            # Step 2: Create tender record (PENDING status until fully processed)
            # Use flush() instead of commit() so we can rollback if processing fails
            # The tender won't be visible to other sessions until final commit
            tender = Tender(
                external_reference=tender_ref,
                source_url=tender_url,
                status=TenderStatus.PENDING,
                download_date=download_date,
            )
            db.add(tender)
            db.flush()  # Get ID without committing - allows rollback on failure
            
            # Step 3: Extract Phase 1 metadata from WEBSITE ONLY first
            merged_metadata = None
            if website_metadata.consultation_text:
                logger.info(f"[{idx}] Phase 1: Extracting from WEBSITE...")
                merged_metadata = ai_service.extract_primary_metadata(
                    website_metadata.consultation_text,
                    source_label="WEBSITE",
                )
                
                # Add website contact if available
                if merged_metadata and website_metadata.contact_administratif:
                    merged_metadata.setdefault("website_extended", {})
                    merged_metadata["website_extended"]["contact_administratif"] = {
                        "value": website_metadata.contact_administratif,
                        "source_document": "WEBSITE",
                    }
            
            # Check if website data is complete
            website_complete = is_metadata_complete(merged_metadata)
            logger.info(f"[{idx}] Website metadata complete: {website_complete}")
            
            # Step 4: Download and process documents
            logger.info(f"[{idx}] Downloading tender documents...")
            downloaded = await scraper.download_tender_zip(
                context, tender_url, idx, website_metadata
            )
            
            documents_for_phase2 = []
            documents = []
            bordereau_result = None
            bordereau_docs_for_early_extraction = []
            
            # Callback for early bordereau extraction
            def on_bordereau_ready(bordereau_docs):
                nonlocal bordereau_docs_for_early_extraction
                bordereau_docs_for_early_extraction = bordereau_docs
            
            if downloaded.success and downloaded.zip_bytes:
                logger.info(f"[{idx}] Processing documents (bordereau priority)...")
                
                # Process documents with bordereau priority callback
                documents, article_index = await process_tender_documents(
                    downloaded.zip_bytes,
                    tender_ref,
                    max_workers=MAX_CONCURRENT_EXTRACTION,
                    on_progress=lambda msg: logger.debug(f"[{idx}] {msg}"),
                    on_bordereau_ready=on_bordereau_ready
                )
                
                # Store article index on tender (includes full content for lookups)
                if article_index:
                    tender.article_index = article_index
                
                # Get existing lots from Phase 1 for bordereau extraction
                existing_lots = []
                if merged_metadata and merged_metadata.get("lots"):
                    existing_lots = [
                        str(lot.get("lot_number"))
                        for lot in merged_metadata.get("lots", [])
                        if lot.get("lot_number")
                    ]
                
                # Step 5: Run Phase 2 - Bordereau extraction EARLY on priority files
                if bordereau_docs_for_early_extraction:
                    logger.info(f"[{idx}] Phase 2: Early bordereau extraction from {len(bordereau_docs_for_early_extraction)} priority files...")
                    
                    early_docs = [
                        {
                            "filename": doc.filename,
                            "document_type": _doc_type_str(doc.document_type),
                            "raw_text": doc.raw_text,
                            "article_index": doc.article_index
                        }
                        for doc in bordereau_docs_for_early_extraction
                        if doc.success and doc.raw_text
                    ]
                    
                    if early_docs:
                        bordereau_result = ai_service.extract_bordereau_items_smart(
                            early_docs,
                            existing_lots=existing_lots
                        )
                        
                        items_found = 0
                        if bordereau_result:
                            items_found = bordereau_result.get('_completeness', {}).get('total_articles', 0)
                            logger.info(f"[{idx}] ✓ Early extraction: {items_found} items")
                        
                        if items_found > 0:
                            # Save bordereau immediately so frontend can display
                            tender.bordereau_metadata = bordereau_result
                            tender.universal_metadata = bordereau_result
                            db.flush()  # Make available to frontend without full commit
                            logger.info(f"[{idx}] ✓ Bordereau posted to frontend early")
                
                # Store documents in DB and prepare for Phase 2 fallback
                for doc in documents:
                    if doc.success and doc.raw_text:
                        db_doc = TenderDocument(
                            tender_id=tender.id,
                            document_type=_doc_type_str(doc.document_type),
                            filename=doc.filename,
                            raw_text=doc.raw_text,
                            page_count=doc.page_count,
                            extraction_method=doc.extraction_method.value if doc.extraction_method else None,
                            file_size_bytes=doc.file_size_bytes,
                            mime_type=doc.mime_type,
                            article_index=doc.article_index,
                        )
                        db.add(db_doc)
                        
                        # Prepare document dict for Phase 2 fallback
                        documents_for_phase2.append({
                            "filename": doc.filename,
                            "document_type": _doc_type_str(doc.document_type),
                            "raw_text": doc.raw_text,
                            "article_index": doc.article_index
                        })
                
                # If early extraction failed, try with all documents
                items_found = 0
                if bordereau_result:
                    items_found = bordereau_result.get('_completeness', {}).get('total_articles', 0)
                
                if items_found == 0 and documents_for_phase2:
                    logger.info(f"[{idx}] Phase 2: Fallback extraction from all documents...")
                    
                    bordereau_result = ai_service.extract_bordereau_items_smart(
                        documents_for_phase2,
                        existing_lots=existing_lots
                    )
                    
                    if bordereau_result:
                        items_found = bordereau_result.get('_completeness', {}).get('total_articles', 0)
                        logger.info(f"[{idx}] ✓ Fallback extraction: {items_found} items")
                    
                    # If still nothing, try focused retry
                    if items_found == 0:
                        logger.warning(f"[{idx}] ⚠ No bordereau items found, running focused retry...")
                        retry_result = ai_service.extract_bordereau_focused_retry(
                            documents_for_phase2,
                            existing_lots=existing_lots
                        )
                        if retry_result:
                            bordereau_result = retry_result
                            items_found = retry_result.get('_completeness', {}).get('total_articles', 0)
                            logger.info(f"[{idx}] ✓ Focused retry found {items_found} items")
                    
                    if bordereau_result and items_found > 0:
                        tender.bordereau_metadata = bordereau_result
                        tender.universal_metadata = bordereau_result
                        logger.info(f"[{idx}] ✓ Final bordereau: {items_found} items")
                
                # Step 6: ONLY if Phase 1 incomplete, use document fallbacks
                if not website_complete and documents:
                    logger.info(f"[{idx}] Website incomplete, using document fallbacks...")
                    
                    for doc in documents:
                        if is_metadata_complete(merged_metadata):
                            break
                        
                        if doc.document_type in [ExtractorDocumentType.AVIS, ExtractorDocumentType.RC, ExtractorDocumentType.CPS]:
                            if doc.raw_text:
                                label = _doc_type_str(doc.document_type)
                                logger.info(f"[{idx}] Extracting from {label}...")
                                fb_metadata = ai_service.extract_primary_metadata(
                                    doc.raw_text, 
                                    source_label=label
                                )
                                merged_metadata = merge_phase1_metadata(merged_metadata, fb_metadata)
                
                # Save Phase 1 metadata
                tender.avis_metadata = merged_metadata
                
                # Step 7: Category classification (Phase 4)
                if merged_metadata:
                    logger.info(f"[{idx}] Phase 4: Classifying categories...")
                    
                    # Get bordereau items for more precise classification
                    bordereau_items = []
                    if bordereau_result:
                        for lot in bordereau_result.get("lots_articles", []):
                            bordereau_items.extend(lot.get("articles", []))
                    
                    categories = ai_service.classify_tender_categories(
                        merged_metadata,
                        bordereau_items=bordereau_items[:20]  # Limit to 20 items
                    )
                    
                    if categories:
                        tender.categories = categories
                        logger.info(f"[{idx}] ✓ Assigned {len(categories)} categories")
                
                # Step 8: Mark as ANALYZED (fully processed)
                tender.status = TenderStatus.ANALYZED
                db.commit()
                logger.info(f"[{idx}] ✓ Tender {tender_ref} fully processed (ANALYZED)")
                return str(tender.id)
            
            else:
                # No ZIP available - can only complete Phase 1
                if merged_metadata:
                    if website_metadata.contact_administratif:
                        merged_metadata.setdefault("website_extended", {})
                        merged_metadata["website_extended"]["contact_administratif"] = {
                            "value": website_metadata.contact_administratif,
                            "source_document": "WEBSITE",
                        }
                    tender.avis_metadata = merged_metadata
                    # Stay as LISTED since we couldn't run Phase 2
                    tender.status = TenderStatus.LISTED
                    logger.warning(f"[{idx}] No documents for Phase 2 - status LISTED only")
                else:
                    tender.status = TenderStatus.ERROR
                    tender.error_message = "No documents available and website extraction failed"
                
                db.commit()
                return str(tender.id) if tender.status != TenderStatus.ERROR else None
                
        except Exception as e:
            logger.error(f"[{idx}] Error processing tender: {e}")
            db.rollback()
            return None
        finally:
            db.close()


async def _run_scraper_async(job_id: str, start_date: str, end_date: str):
    """
    Async scraper logic with concurrent tender processing.
    
    Flow:
    1. Collect all tender links
    2. Process tenders with 5 concurrent workers
    3. Each worker fully processes one tender at a time
    """
    global _scraper_instance
    
    from app.core.database import SessionLocal
    from loguru import logger
    from playwright.async_api import async_playwright
    from app.core.config import settings
    
    db = SessionLocal()
    
    try:
        job = db.query(ScraperJob).filter(ScraperJob.id == job_id).first()
        if not job:
            return
        
        def on_progress(progress: ScraperProgress):
            job.current_phase = progress.phase
            job.total_found = progress.total
            job.downloaded = progress.downloaded
            job.failed = progress.failed
            db.commit()
        
        _scraper_instance = TenderScraper(on_progress=on_progress)
        start_time = datetime.now()
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=settings.SCRAPER_HEADLESS)
            context = await browser.new_context(accept_downloads=True)
            
            try:
                # Phase 1: Collect tender links
                _scraper_instance.progress.phase = "Collecting tender links"
                on_progress(_scraper_instance.progress)
                
                page = await context.new_page()
                tender_links = await _scraper_instance.collect_tender_links(page, start_date, end_date)
                await page.close()
                
                _scraper_instance.progress.total = len(tender_links)
                on_progress(_scraper_instance.progress)
                
                if not tender_links:
                    job.status = "COMPLETED"
                    job.total_found = 0
                    job.completed_at = datetime.utcnow()
                    db.commit()
                    return
                
                logger.info(f"Found {len(tender_links)} tenders, processing with {MAX_CONCURRENT_TENDERS} workers")
                
                # Phase 2: Process tenders concurrently
                _scraper_instance.progress.phase = f"Processing {len(tender_links)} tenders (5 concurrent)"
                on_progress(_scraper_instance.progress)
                
                semaphore = asyncio.Semaphore(MAX_CONCURRENT_TENDERS)
                
                # Create tasks for all tenders
                tasks = [
                    _process_single_tender(
                        context,
                        _scraper_instance,
                        url,
                        idx,
                        semaphore,
                        SessionLocal,
                        start_date
                    )
                    for idx, url in enumerate(tender_links, 1)
                ]
                
                # Process all tenders
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # Count successes
                success_count = sum(1 for r in results if r and not isinstance(r, Exception))
                fail_count = len(results) - success_count
                
                _scraper_instance.progress.downloaded = success_count
                _scraper_instance.progress.failed = fail_count
                on_progress(_scraper_instance.progress)
                
            finally:
                await browser.close()
        
        # Finalize job
        elapsed = (datetime.now() - start_time).total_seconds()
        job.status = "COMPLETED"
        job.extracted = _scraper_instance.progress.downloaded
        job.completed_at = datetime.utcnow()
        job.elapsed_seconds = int(elapsed)
        db.commit()
        
        logger.info(f"Scraper completed: {success_count} success, {fail_count} failed in {elapsed:.1f}s")
        
    except Exception as e:
        logger.error(f"Scraper failed: {e}")
        job.status = "FAILED"
        job.error_log = str(e)
        job.completed_at = datetime.utcnow()
        db.commit()
        raise
    finally:
        _scraper_instance = None
        db.close()


@router.get("/api/scraper/status", response_model=ScraperStatusResponse)
def get_scraper_status(db: Session = Depends(get_db)):
    """Get current scraper status"""
    global _scraper_instance
    
    last_job = db.query(ScraperJob).filter(
        ScraperJob.status.in_(["COMPLETED", "FAILED"])
    ).order_by(desc(ScraperJob.completed_at)).first()
    
    if _scraper_instance and _scraper_instance.progress.is_running:
        p = _scraper_instance.progress
        return ScraperStatusResponse(
            is_running=True,
            current_phase=p.phase,
            total_tenders=p.total,
            downloaded=p.downloaded,
            failed=p.failed,
            elapsed_seconds=p.elapsed_seconds,
            last_run=last_job.completed_at.isoformat() if last_job else None
        )
    
    return ScraperStatusResponse(
        is_running=False,
        current_phase="Idle",
        total_tenders=0,
        downloaded=0,
        failed=0,
        elapsed_seconds=0,
        last_run=last_job.completed_at.isoformat() if last_job else None
    )


@router.post("/api/scraper/stop")
def stop_scraper():
    """Stop running scraper"""
    global _scraper_instance
    
    if _scraper_instance and _scraper_instance.progress.is_running:
        _scraper_instance.stop()
        return {"stopped": True}
    
    return {"stopped": False, "message": "No scraper running"}


class ImportSingleRequest(BaseModel):
    url: str


@router.post("/api/scraper/import-single")
async def import_single_tender(
    request: ImportSingleRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Import and process a single tender from a direct URL.
    
    This endpoint is for testing - it takes a full tender URL like:
    https://www.marchespublics.gov.ma/index.php?page=entreprise.EntrepriseDetailsConsultation&refConsultation=970495&orgAcronyme=g3h
    
    And runs the full scrape + analysis pipeline on just that tender.
    """
    from loguru import logger
    import re
    
    url = request.url.strip()
    
    # Validate URL format
    if "marchespublics.gov.ma" not in url:
        raise HTTPException(400, "URL must be from marchespublics.gov.ma")
    
    if "refConsultation" not in url:
        raise HTTPException(400, "URL must contain refConsultation parameter")
    
    # Extract reference for dedup check
    ref_match = re.search(r'refConsultation=(\d+)', url)
    ref_consultation = ref_match.group(1) if ref_match else None
    
    # Check if already exists
    if ref_consultation:
        existing = db.query(Tender).filter(
            Tender.source_url.contains(f"refConsultation={ref_consultation}")
        ).first()
        if existing:
            logger.info(f"Tender {ref_consultation} already exists, returning existing")
            return _tender_to_dict(existing)
    
    logger.info(f"Importing single tender from: {url}")
    
    # Run the import in a thread
    import threading
    result_holder = {"tender_id": None, "error": None}
    
    def run_import():
        import asyncio
        import sys
        
        if sys.platform == "win32":
            loop = asyncio.ProactorEventLoop()
        else:
            loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            tender_id = loop.run_until_complete(_import_single_tender_async(url))
            result_holder["tender_id"] = tender_id
        except Exception as e:
            logger.error(f"Import failed: {e}")
            result_holder["error"] = str(e)
        finally:
            loop.close()
    
    thread = threading.Thread(target=run_import)
    thread.start()
    thread.join(timeout=300)  # 5 minute timeout
    
    if result_holder["error"]:
        raise HTTPException(500, f"Import failed: {result_holder['error']}")
    
    if not result_holder["tender_id"]:
        raise HTTPException(500, "Import failed: no tender created")
    
    # Fetch and return the created tender
    tender = db.query(Tender).filter(Tender.id == result_holder["tender_id"]).first()
    if not tender:
        raise HTTPException(500, "Import completed but tender not found")
    
    return _tender_to_dict(tender)


async def _import_single_tender_async(tender_url: str) -> Optional[str]:
    """
    Import a single tender asynchronously.
    Reuses the _process_single_tender logic.
    """
    from app.core.database import SessionLocal
    from loguru import logger
    from playwright.async_api import async_playwright
    from app.core.config import settings
    from datetime import datetime
    
    logger.info(f"Starting single tender import: {tender_url}")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=settings.SCRAPER_HEADLESS)
        context = await browser.new_context(accept_downloads=True)
        
        try:
            # Create a minimal scraper instance
            scraper = TenderScraper()
            semaphore = asyncio.Semaphore(1)
            
            tender_id = await _process_single_tender(
                context=context,
                scraper=scraper,
                tender_url=tender_url,
                idx=1,
                semaphore=semaphore,
                db_session_factory=SessionLocal,
                download_date=datetime.now().strftime("%Y-%m-%d")
            )
            
            return tender_id
            
        finally:
            await context.close()
            await browser.close()


# ============================
# TENDER ENDPOINTS
# ============================

@router.get("/api/tenders")
def list_tenders(
    q: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    per_page: int = 50,
    include_incomplete: bool = False,
    db: Session = Depends(get_db)
):
    """
    List tenders with optional filters.
    
    By default, only returns fully processed tenders (ANALYZED status).
    Set include_incomplete=true to see all statuses.
    """
    query = db.query(Tender)
    
    # By default, only show fully processed tenders
    if status:
        query = query.filter(Tender.status == status)
    elif not include_incomplete:
        # Only show ANALYZED tenders by default
        query = query.filter(Tender.status == TenderStatus.ANALYZED)
    
    if date_from:
        query = query.filter(Tender.download_date >= date_from)
    
    if date_to:
        query = query.filter(Tender.download_date <= date_to)
    
    if q:
        search_filter = f"%{q}%"
        query = query.filter(
            Tender.external_reference.ilike(search_filter) |
            Tender.avis_metadata['objet_marche'].astext.ilike(search_filter) |
            Tender.avis_metadata['organisme_acheteur'].astext.ilike(search_filter)
        )
    
    total = query.count()
    query = query.order_by(desc(Tender.created_at))
    query = query.offset((page - 1) * per_page).limit(per_page)
    
    items = query.all()
    
    return {
        "items": [_tender_to_dict(t) for t in items],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page
    }


@router.get("/api/tenders/{tender_id}/debug/articles")
def debug_articles(
    tender_id: str, 
    show_raw_sample: bool = False,
    sample_size: int = 2000,
    db: Session = Depends(get_db)
):
    """
    Debug endpoint: view indexed articles for all documents of a tender.
    
    Args:
        tender_id: The tender UUID
        show_raw_sample: If true, includes raw text sample from each document
        sample_size: How many characters to show in raw sample (default 2000)
    """
    from app.services.article_indexer import get_article_map, get_verified_articles
    
    tender = db.query(Tender).filter(Tender.id == tender_id).first()
    if not tender:
        raise HTTPException(status_code=404, detail="Tender not found")
    
    result = {
        "tender_id": tender_id,
        "tender_reference": tender.external_reference,
        "total_documents": len(list(tender.documents)),
        "documents": []
    }
    
    for doc in tender.documents:
        doc_info = {
            "filename": doc.filename,
            "document_type": doc.document_type,
            "text_length": len(doc.raw_text) if doc.raw_text else 0,
            "stored_article_index": doc.article_index,  # From DB
            "live_article_count": 0,
            "articles": []
        }
        
        if doc.raw_text:
            # Re-index live to compare with stored
            live_articles = get_verified_articles(doc.raw_text)
            doc_info["live_article_count"] = len(live_articles)
            
            article_map = get_article_map(doc.raw_text)
            for art_num, art_data in article_map.items():
                doc_info["articles"].append({
                    "number": art_num,
                    "title": art_data.get("title", ""),
                    "char_count": art_data.get("contentLength", 0),
                    "preview": art_data.get("preview", "")[:500]
                })
            
            # Add raw sample if requested
            if show_raw_sample:
                doc_info["raw_text_sample"] = doc.raw_text[:sample_size]
        
        result["documents"].append(doc_info)
    
    return result


@router.get("/api/tenders/{tender_id}")
def get_tender(tender_id: str, db: Session = Depends(get_db)):
    """Get single tender with documents"""
    tender = db.query(Tender).filter(Tender.id == tender_id).first()
    if not tender:
        raise HTTPException(404, "Tender not found")
    
    result = _tender_to_dict(tender)
    result["documents"] = [
        {
            "id": str(doc.id),
            "document_type": doc.document_type if doc.document_type else None,
            "filename": doc.filename,
            "page_count": doc.page_count,
            "extraction_method": doc.extraction_method,
            "file_size_bytes": doc.file_size_bytes,
            "has_article_index": doc.article_index is not None,
            "article_count": len(doc.article_index) if doc.article_index else 0,
        }
        for doc in tender.documents
    ]
    result["article_index"] = tender.article_index
    
    return result


@router.post("/api/tenders/{tender_id}/analyze")
def analyze_tender(tender_id: str, force: bool = False, db: Session = Depends(get_db)):
    """
    Trigger smart Phase 2 analysis using article-based approach.
    
    Flow:
    1. Check if already analyzed (skip unless force=true)
    2. Check if documents and article index exist
    3. Use article index to target relevant sections
    4. Extract universal metadata from targeted articles
    """
    from loguru import logger
    
    tender = db.query(Tender).filter(Tender.id == tender_id).first()
    if not tender:
        raise HTTPException(404, "Tender not found")
    
    # Skip if already analyzed (unless force=true)
    if tender.status == TenderStatus.ANALYZED and tender.bordereau_metadata and not force:
        logger.info(f"⏭️ Tender {tender_id} already ANALYZED, skipping (use force=true to re-analyze)")
        return {
            "success": True,
            "phase": "phase2_skipped",
            "message": "Tender already analyzed",
            "tender": _tender_to_dict(tender)
        }
    
    documents = list(tender.documents)
    
    if not documents:
        logger.warning(f"No documents for tender {tender_id}, attempting re-download...")
        
        if not tender.source_url:
            raise HTTPException(400, "No source URL available for re-download")
        
        try:
            downloaded_docs = _redownload_tender_documents_sync(tender, db)
            if not downloaded_docs:
                raise HTTPException(400, "Failed to download documents")
            documents = downloaded_docs
        except Exception as e:
            logger.error(f"Re-download failed: {e}")
            raise HTTPException(500, f"Document re-download failed: {str(e)}")
    
    # Build extraction results for AI service
    extraction_results = []
    
    for doc in documents:
        extraction_results.append(ExtractionResult(
            filename=doc.filename,
            document_type=_to_extractor_doc_type(doc.document_type),
            text=doc.raw_text or "",
            page_count=doc.page_count,
            extraction_method=ExtractionMethod(doc.extraction_method) if doc.extraction_method else ExtractionMethod.DIGITAL,
            file_size_bytes=doc.file_size_bytes or 0,
            mime_type=doc.mime_type or "",
            success=True
        ))
    
    # Get website contact if available
    website_contact_raw = None
    if tender.avis_metadata:
        website_extended = tender.avis_metadata.get("website_extended", {})
        contact_info = website_extended.get("contact_administratif", {})
        if contact_info and contact_info.get("value"):
            website_contact_raw = contact_info.get("value")
    
    # Get existing lot numbers from Phase 1
    existing_lots = []
    if tender.avis_metadata and tender.avis_metadata.get("lots"):
        existing_lots = [
            str(lot.get("numero_lot"))
            for lot in tender.avis_metadata.get("lots", [])
            if lot.get("numero_lot")
        ]
    
    # Phase 2: Bordereau des Prix Extraction
    logger.info(f"🚀 Starting Phase 2 (Bordereau) for tender {tender_id}")
    logger.info(f"   Documents: {len(extraction_results)}")
    logger.info(f"   Existing lots: {existing_lots}")
    
    bordereau_result = ai_service.extract_bordereau_items(
        extraction_results,
        existing_lots=existing_lots
    )
    
    if bordereau_result:
        # Store in both columns for backward compatibility
        tender.bordereau_metadata = bordereau_result
        tender.universal_metadata = bordereau_result
        
        # Phase 4: Category classification
        if tender.avis_metadata:
            logger.info(f"🏷️ Starting Phase 4 (Categories) for tender {tender_id}")
            
            # Get bordereau items for classification
            bordereau_items = []
            for lot in bordereau_result.get("lots_articles", []):
                bordereau_items.extend(lot.get("articles", []))
            
            categories = ai_service.classify_tender_categories(
                tender.avis_metadata,
                bordereau_items=bordereau_items[:20]
            )
            
            if categories:
                tender.categories = categories
                logger.info(f"✓ Assigned {len(categories)} categories")
        
        tender.status = TenderStatus.ANALYZED
        db.commit()
        db.refresh(tender)
        
        return AnalyzeResponse(
            success=True,
            phase="bordereau",
            message=f"Extracted {bordereau_result.get('_completeness', {}).get('total_articles', 0)} articles, {len(tender.categories or [])} categories",
            tender=_tender_to_dict(tender)
        )
    
    return AnalyzeResponse(
        success=False,
        phase="bordereau",
        message="No bordereau items found",
        tender=_tender_to_dict(tender)
    )


def _redownload_tender_documents_sync(tender: Tender, db: Session) -> List[TenderDocument]:
    """Re-download and process tender documents."""
    import threading
    from loguru import logger
    
    result_holder = {"documents": [], "error": None}
    
    def run_download():
        if sys.platform == "win32":
            loop = asyncio.ProactorEventLoop()
        else:
            loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            docs = loop.run_until_complete(_redownload_tender_documents_async(tender, db))
            result_holder["documents"] = docs
        except Exception as e:
            result_holder["error"] = e
            logger.error(f"Async download failed: {e}")
        finally:
            loop.close()
    
    download_thread = threading.Thread(target=run_download)
    download_thread.start()
    download_thread.join(timeout=120)
    
    if result_holder["error"]:
        raise result_holder["error"]
    
    return result_holder["documents"]


async def _redownload_tender_documents_async(tender: Tender, db: Session) -> List[TenderDocument]:
    """Async re-download with new pipeline processing."""
    from loguru import logger
    from playwright.async_api import async_playwright
    from app.core.config import settings
    
    stored_documents = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=settings.SCRAPER_HEADLESS)
        context = await browser.new_context(accept_downloads=True)
        
        try:
            scraper = TenderScraper()
            
            download_result = await scraper.download_tender_zip(
                context,
                tender.source_url,
                idx=0,
                website_metadata=None
            )
            
            if not download_result.success or not download_result.zip_bytes:
                logger.error(f"Failed to download ZIP: {download_result.error}")
                return []
            
            # Process with new pipeline
            documents, article_index = await process_tender_documents(
                download_result.zip_bytes,
                tender.external_reference,
                max_workers=MAX_CONCURRENT_EXTRACTION
            )
            
            # Update tender article index
            if article_index:
                tender.article_index = article_index
            
            # Store documents
            for doc in documents:
                if doc.success and doc.raw_text:
                    existing = db.query(TenderDocument).filter(
                        TenderDocument.tender_id == tender.id,
                        TenderDocument.filename == doc.filename
                    ).first()
                    
                    if not existing:
                        db_doc = TenderDocument(
                            tender_id=tender.id,
                            document_type=_doc_type_str(doc.document_type),
                            filename=doc.filename,
                            raw_text=doc.raw_text,
                            page_count=doc.page_count,
                            extraction_method=doc.extraction_method.value if doc.extraction_method else None,
                            file_size_bytes=doc.file_size_bytes,
                            mime_type=doc.mime_type,
                            article_index=doc.article_index,
                        )
                        db.add(db_doc)
                        stored_documents.append(db_doc)
            
            db.commit()
            
            for doc in stored_documents:
                db.refresh(doc)
            
            return stored_documents
            
        finally:
            await browser.close()


@router.post("/api/tenders/{tender_id}/ask", response_model=AskAIResponse)
def ask_ai_about_tender(
    tender_id: str,
    request: AskAIRequest,
    db: Session = Depends(get_db)
):
    """Ask AI about a specific tender (Phase 3) with ambiguity detection, metadata pre-check and timing"""
    import time
    start_time = time.time()
    
    tender = db.query(Tender).filter(Tender.id == tender_id).first()
    if not tender:
        raise HTTPException(404, "Tender not found")
    
    question_lower = request.question.lower()
    
    # Step 0: Detect ambiguous questions that need clarification
    clarification = _check_for_ambiguity(question_lower)
    if clarification:
        elapsed_ms = int((time.time() - start_time) * 1000)
        return AskAIResponse(
            answer="",
            citations=[],
            follow_up_questions=[],
            response_time_ms=elapsed_ms,
            needs_clarification=True,
            clarification_prompt=clarification["prompt"],
            clarification_options=[
                ClarificationOption(label=opt["label"], value=opt["value"]) 
                for opt in clarification["options"]
            ]
        )
    
    # Step 1: Quick metadata lookup for common questions
    if tender.avis_metadata or tender.bordereau_metadata:
        quick_answer = _try_metadata_answer(
            question_lower, 
            tender.avis_metadata or {}, 
            tender.bordereau_metadata
        )
        if quick_answer:
            elapsed_ms = int((time.time() - start_time) * 1000)
            return AskAIResponse(
                answer=quick_answer["answer"],
                citations=quick_answer["citations"],
                follow_up_questions=quick_answer.get("follow_up_questions", []),
                response_time_ms=elapsed_ms
            )
    
    # Step 2: Full AI processing for complex questions
    documents = tender.documents
    if not documents:
        raise HTTPException(400, "No documents available")
    
    extraction_results = []
    for doc in documents:
        extraction_results.append(ExtractionResult(
            filename=doc.filename,
            document_type=_to_extractor_doc_type(doc.document_type),
            text=doc.raw_text or "",
            page_count=doc.page_count,
            extraction_method=ExtractionMethod(doc.extraction_method) if doc.extraction_method else ExtractionMethod.DIGITAL,
            file_size_bytes=doc.file_size_bytes or 0,
            mime_type=doc.mime_type or "",
            success=True
        ))
    
    result = ai_service.ask_ai(
        request.question, 
        extraction_results,
        tender_reference=tender.external_reference,
        bordereau_metadata=tender.bordereau_metadata
    )
    
    elapsed_ms = int((time.time() - start_time) * 1000)
    
    if result:
        return AskAIResponse(
            answer=result.get("answer", ""),
            citations=result.get("citations", []),
            follow_up_questions=result.get("follow_up_questions", []),
            response_time_ms=elapsed_ms
        )
    else:
        raise HTTPException(500, "AI query failed")


def _check_for_ambiguity(question: str) -> Optional[dict]:
    """
    Detect ambiguous questions that require clarification.
    Returns clarification options if ambiguity detected, None otherwise.
    """
    # Pattern: "articles" alone is ambiguous (could mean CPS articles OR bordereau items)
    articles_patterns = [
        "quels sont les articles",
        "c'est quoi les articles",
        "les articles",
        "شنو هي المواد",
        "المواد فهاد",
        "articles dans ce",
        "articles du marché",
    ]
    
    # Check if it's an ambiguous "articles" question
    is_ambiguous_articles = any(p in question for p in articles_patterns)
    
    # Exclude if user is clearly specific about what they want
    specific_articles_cps = any(kw in question for kw in ["article du cps", "article cps", "article rc", "clauses", "conditions"])
    specific_articles_bordereau = any(kw in question for kw in ["bordereau", "quantité", "prix", "fourniture", "items", "produits", "livrer"])
    
    if is_ambiguous_articles and not specific_articles_cps and not specific_articles_bordereau:
        return {
            "prompt": "Que voulez-vous consulter?",
            "options": [
                {"label": "📦 Articles à fournir (Bordereau des Prix)", "value": "Quels sont les articles à fournir avec leurs quantités selon le bordereau des prix?"},
                {"label": "📜 Articles du CPS (clauses légales)", "value": "Quels sont les articles importants du Cahier des Prescriptions Spéciales?"},
                {"label": "📋 Articles du RC (règlement)", "value": "Quels sont les articles du Règlement de Consultation?"},
            ]
        }
    
    # Pattern: Generic "documents" question
    docs_patterns = ["quels documents", "les documents", "documents à", "الوثائق"]
    is_ambiguous_docs = any(p in question for p in docs_patterns)
    specific_docs_fournir = any(kw in question for kw in ["fournir", "remettre", "préparer", "dossier", "soumission"])
    specific_docs_contenu = any(kw in question for kw in ["contenu", "contient", "dans le dossier", "dans ce marché"])
    
    if is_ambiguous_docs and not specific_docs_fournir and not specific_docs_contenu:
        return {
            "prompt": "De quels documents parlez-vous?",
            "options": [
                {"label": "📁 Documents à fournir pour soumissionner", "value": "Quels documents dois-je fournir pour soumettre ma candidature?"},
                {"label": "📄 Documents contenus dans le dossier", "value": "Quels documents sont disponibles dans ce dossier d'appel d'offres?"},
            ]
        }
    
    return None


def _try_metadata_answer(question: str, avis_metadata: dict, bordereau_metadata: Optional[dict] = None) -> Optional[dict]:
    """
    Try to answer common questions directly from stored metadata.
    Checks both avis_metadata AND bordereau_metadata for complete answers.
    Returns None if the question requires full document analysis.
    """
    # Articles / Items / Quantities / Prix - Check bordereau_metadata FIRST
    if any(kw in question for kw in ["articles", "quantité", "quantités", "prix", "items", "fournitures", 
                                       "livrer", "produits", "désignation", "bordereau", "المواد", "الكميات"]):
        if bordereau_metadata and bordereau_metadata.get("lots_articles"):
            lots = bordereau_metadata.get("lots_articles", [])
            total_articles = sum(len(lot.get("articles", [])) for lot in lots)
            
            if total_articles > 0:
                # Build COMPLETE list of all articles
                summary_lines = []
                for lot in lots:  # Show ALL lots
                    lot_num = lot.get("lot_numero", "Unique")
                    lot_objet = lot.get("objet_lot", "")[:80]
                    articles = lot.get("articles", [])
                    
                    if len(lots) > 1 or lot_objet:
                        header = f"**Lot {lot_num}**"
                        if lot_objet:
                            header += f": {lot_objet}"
                        summary_lines.append(header)
                    
                    for art in articles:  # Show ALL articles
                        num_prix = art.get("numero_prix", "")
                        designation = art.get("designation", "")[:80]
                        qty = art.get("quantite", "")
                        unite = art.get("unite", "")
                        if designation:
                            line = f"  - "
                            if num_prix:
                                line += f"**{num_prix}** - "
                            line += designation
                            if qty:
                                line += f" | **Qté: {qty}**"
                            if unite:
                                line += f" {unite}"
                            summary_lines.append(line)
                
                answer = f"**{total_articles} article(s)** au total"
                if len(lots) > 1:
                    answer += f" répartis sur **{len(lots)} lots**"
                answer += ":\n\n" + "\n".join(summary_lines)
                answer += "\n\n**[Bordereau des Prix]**"
                
                return {
                    "answer": answer,
                    "citations": [{"document": "BPU/DQE", "section": "Bordereau des Prix"}],
                    "follow_up_questions": [
                        "Quelles sont les spécifications techniques de ces articles?",
                        "Quel est le délai de livraison prévu?"
                    ]
                }
    
    # Date limite
    if any(kw in question for kw in ["date limite", "deadline", "délai", "dernier délai", "متى", "آخر أجل"]):
        deadline = avis_metadata.get("date_limite_remise_plis", {})
        if deadline.get("date"):
            date_str = deadline.get("date", "")
            time_str = deadline.get("heure", "")
            answer = f"Date limite: **{date_str}**"
            if time_str:
                answer += f" à **{time_str}**"
            answer += ". **[AVIS]**"
            return {
                "answer": answer,
                "citations": [{"document": "AVIS", "section": "Date limite"}],
                "follow_up_questions": [
                    "Où doit-on déposer les plis?",
                    "Quels documents sont requis pour la soumission?"
                ]
            }
    
    # Caution provisoire
    if any(kw in question for kw in ["caution", "garantie provisoire", "الضمان"]):
        caution = avis_metadata.get("caution_provisoire")
        if caution:
            val = caution.get("value") or caution.get("montant")
            if val:
                answer = f"Caution provisoire: **{val}**. **[AVIS]**"
                return {
                    "answer": answer,
                    "citations": [{"document": "AVIS", "section": "Caution provisoire"}],
                    "follow_up_questions": [
                        "Comment doit être constituée la caution?",
                        "Quand sera-t-elle restituée?"
                    ]
                }
    
    # Organisme / Acheteur
    if any(kw in question for kw in ["organisme", "acheteur", "maître d'ouvrage", "client", "من هو"]):
        org = avis_metadata.get("organisme_acheteur")
        if org:
            val = org.get("value") if isinstance(org, dict) else org
            if val:
                answer = f"Organisme acheteur: **{val}**. **[AVIS]**"
                return {
                    "answer": answer,
                    "citations": [{"document": "AVIS", "section": "Organisme"}],
                    "follow_up_questions": [
                        "Quelles sont les coordonnées de contact?",
                        "Quel est l'objet du marché?"
                    ]
                }
    
    # Objet du marché
    if any(kw in question for kw in ["objet", "description", "quoi", "ماذا", "موضوع"]):
        objet = avis_metadata.get("objet_marche")
        if objet:
            val = objet.get("value") if isinstance(objet, dict) else objet
            if val:
                answer = f"{val}. **[AVIS]**"
                return {
                    "answer": answer,
                    "citations": [{"document": "AVIS", "section": "Objet"}],
                    "follow_up_questions": [
                        "Combien de lots contient ce marché?",
                        "Quelle est l'estimation du budget?"
                    ]
                }
    
    # Référence
    if any(kw in question for kw in ["référence", "numéro", "رقم", "مرجع"]):
        ref = avis_metadata.get("reference_marche")
        if ref:
            val = ref.get("value") if isinstance(ref, dict) else ref
            if val:
                answer = f"Référence: **{val}**. **[AVIS]**"
                return {
                    "answer": answer,
                    "citations": [{"document": "AVIS", "section": "Référence"}],
                    "follow_up_questions": [
                        "Quel est l'objet de ce marché?",
                        "Quelle est la date limite de soumission?"
                    ]
                }
    
    return None


def _tender_to_dict(tender: Tender) -> dict:
    """Convert Tender model to dict"""
    return {
        "id": str(tender.id),
        "external_reference": tender.external_reference,
        "source_url": tender.source_url,
        "status": tender.status.value if tender.status else None,
        "scraped_at": tender.scraped_at.isoformat() if tender.scraped_at else None,
        "download_date": tender.download_date,
        "avis_metadata": tender.avis_metadata,
        "bordereau_metadata": tender.bordereau_metadata,
        "universal_metadata": tender.universal_metadata,  # Backward compat
        "categories": tender.categories,  # Phase 4: Category classification
        "article_index": tender.article_index,
        "error_message": tender.error_message,
        "created_at": tender.created_at.isoformat() if tender.created_at else None,
        "updated_at": tender.updated_at.isoformat() if tender.updated_at else None
    }


def _doc_type_str(dt) -> str:
    """Normalize document_type that may be an Enum (has .value) or a plain string."""
    if not dt:
        return "UNKNOWN"

    val = getattr(dt, "value", dt)
    try:
        return str(val)
    except Exception:
        return "UNKNOWN"


def _to_extractor_doc_type(dt) -> ExtractorDocumentType:
    """Safely convert stored document_type into ExtractorDocumentType (fallback to UNKNOWN)."""
    label = _doc_type_str(dt)
    try:
        return ExtractorDocumentType(label)
    except Exception:
        return ExtractorDocumentType.UNKNOWN

