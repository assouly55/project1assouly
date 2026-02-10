"""
Tender AI Platform - Pipeline Processor
Handles concurrent processing of tenders: extraction, indexing, and storage

Updated: AI-based file detection prioritizes Bordereau files before processing.
"""

import asyncio
import io
import time
import zipfile
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple, Any, Callable
from dataclasses import dataclass, field
from datetime import datetime
from loguru import logger

from app.services.extractor import (
    DocumentType,
    ExtractionResult,
    ExtractionMethod,
    extract_full_document,
    extract_first_page,
    classify_document,
    _is_pdf_scanned,
)
from app.services.article_indexer import (
    get_verified_articles,
    build_article_index_for_db,
    slice_document_by_articles,
)
from app.services.file_detector import detect_and_prioritize_files


@dataclass
class ProcessedDocument:
    """Result of document processing"""
    filename: str
    document_type: DocumentType
    raw_text: str
    page_count: Optional[int]
    extraction_method: ExtractionMethod
    file_size_bytes: int
    mime_type: str
    article_index: Optional[List[Dict]] = None  # For CPS/RC
    success: bool = True
    error: Optional[str] = None


@dataclass
class ProcessedTender:
    """Fully processed tender ready for DB storage"""
    url: str
    reference: Optional[str]
    documents: List[ProcessedDocument]
    article_index: Optional[Dict] = None  # Combined index for CPS/RC
    avis_metadata: Optional[Dict] = None
    website_metadata: Any = None
    success: bool = True
    error: Optional[str] = None


def extract_all_nested_zips(zip_bytes: bytes) -> Dict[str, io.BytesIO]:
    """
    Recursively extract all files from ZIP, including nested ZIPs.
    Returns flat dict of filename -> BytesIO
    """
    all_files = {}
    
    def extract_zip(zf: zipfile.ZipFile, prefix: str = ""):
        for name in zf.namelist():
            if name.endswith('/'):
                continue  # Skip directories
            
            full_path = f"{prefix}{name}" if prefix else name
            file_data = zf.read(name)
            
            # Check if this is a nested ZIP
            if name.lower().endswith('.zip'):
                try:
                    nested_zf = zipfile.ZipFile(io.BytesIO(file_data), 'r')
                    # Extract nested ZIP with path prefix
                    nested_prefix = full_path.rsplit('.', 1)[0] + "/"
                    extract_zip(nested_zf, nested_prefix)
                    nested_zf.close()
                except zipfile.BadZipFile:
                    # Not a valid ZIP, store as regular file
                    all_files[full_path] = io.BytesIO(file_data)
            else:
                all_files[full_path] = io.BytesIO(file_data)
    
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zf:
            extract_zip(zf)
    except Exception as e:
        logger.error(f"Failed to extract ZIP: {e}")
    
    return all_files


def detect_merged_files(files: Dict[str, io.BytesIO]) -> Dict[str, List[str]]:
    """
    Detect if multiple tender files are merged into one.
    Returns mapping of merged_file -> list of detected document references.
    
    Looks for patterns like:
    - "Référence de consultation:" followed by reference number
    - Clear page breaks with document headers
    """
    merged_files = {}
    
    for filename, file_bytes in files.items():
        if not filename.lower().endswith('.pdf'):
            continue
            
        file_bytes.seek(0)
        try:
            # Quick scan of first few KB to detect if merged
            import pypdf
            reader = pypdf.PdfReader(file_bytes)
            
            if len(reader.pages) < 5:
                continue  # Unlikely to be merged
            
            # Sample first 3 pages
            sample_text = ""
            for i in range(min(3, len(reader.pages))):
                sample_text += (reader.pages[i].extract_text() or "") + "\n"
            
            # Look for multiple reference patterns
            ref_patterns = [
                r'référence\s*(?:de\s*)?consultation\s*[:\s]+([A-Z0-9\-/]+)',
                r'n°\s*(?:de\s*)?consultation\s*[:\s]+([A-Z0-9\-/]+)',
                r'AO\s*N°\s*([A-Z0-9\-/]+)',
            ]
            
            found_refs = set()
            for pattern in ref_patterns:
                matches = re.findall(pattern, sample_text, re.IGNORECASE)
                found_refs.update(matches)
            
            if len(found_refs) > 1:
                merged_files[filename] = list(found_refs)
                logger.warning(f"Detected merged file: {filename} contains {len(found_refs)} references")
                
        except Exception as e:
            logger.debug(f"Could not analyze {filename} for merge detection: {e}")
        finally:
            file_bytes.seek(0)
    
    return merged_files


def split_merged_file(
    filename: str, 
    file_bytes: io.BytesIO, 
    references: List[str]
) -> Dict[str, io.BytesIO]:
    """
    Attempt to split a merged PDF into separate files.
    Returns mapping of new_filename -> BytesIO
    
    Note: This is a best-effort approach. Perfect splitting requires 
    understanding document structure which varies.
    """
    # For now, we extract text and use markers to identify sections
    # A more robust approach would use page-by-page analysis
    
    logger.info(f"Attempting to split merged file: {filename}")
    
    # For complex splitting, we'll mark sections but keep the original file
    # and let the AI handle sections via article indexing
    
    return {filename: file_bytes}  # Return original for now


MAX_DOCUMENT_PROCESSING_SECONDS = 180  # 3 minutes hard cap per document


def process_single_document(
    filename: str, 
    file_bytes: io.BytesIO,
    tender_ref: Optional[str] = None,
    light_mode: bool = False,
) -> ProcessedDocument:
    """
    Process a single document: extract text, classify, and index articles.
    Runs in thread pool for parallel processing.
    """
    try:
        # First page scan for classification
        first_page_result = extract_first_page(filename, file_bytes, use_ai_classification=True)
        
        if not first_page_result.success:
            return ProcessedDocument(
                filename=filename,
                document_type=DocumentType.UNKNOWN,
                raw_text="",
                page_count=None,
                extraction_method=ExtractionMethod.DIGITAL,
                file_size_bytes=0,
                mime_type="",
                success=False,
                error=first_page_result.error
            )
        
        # In light_mode, skip OCR for scanned PDFs (bordereau already found via Excel)
        effective_scanned = first_page_result.is_scanned
        if light_mode and effective_scanned:
            logger.info(f"⚡ Light mode: skipping OCR for scanned PDF {filename}")
            effective_scanned = False  # Force digital extraction (fast, partial text is fine)
        
        # Full extraction
        file_bytes.seek(0)
        extraction = extract_full_document(
            filename, 
            file_bytes, 
            effective_scanned
        )
        
        if not extraction.success:
            return ProcessedDocument(
                filename=filename,
                document_type=first_page_result.document_type,
                raw_text="",
                page_count=None,
                extraction_method=ExtractionMethod.DIGITAL,
                file_size_bytes=first_page_result.file_size_bytes,
                mime_type=first_page_result.mime_type,
                success=False,
                error=extraction.error
            )
        
        # Build article index for CPS and RC documents
        article_index = None
        if extraction.document_type in [DocumentType.CPS, DocumentType.RC]:
            articles = get_verified_articles(extraction.text)
            if articles:
                article_index = articles
                logger.info(f"Indexed {len(articles)} articles from {filename}")
        
        return ProcessedDocument(
            filename=filename,
            document_type=extraction.document_type,
            raw_text=extraction.text,
            page_count=extraction.page_count,
            extraction_method=extraction.extraction_method,
            file_size_bytes=extraction.file_size_bytes,
            mime_type=extraction.mime_type,
            article_index=article_index,
            success=True
        )
        
    except Exception as e:
        logger.error(f"Failed to process document {filename}: {e}")
        return ProcessedDocument(
            filename=filename,
            document_type=DocumentType.UNKNOWN,
            raw_text="",
            page_count=None,
            extraction_method=ExtractionMethod.DIGITAL,
            file_size_bytes=0,
            mime_type="",
            success=False,
            error=str(e)
        )


async def process_documents_concurrent(
    files: Dict[str, io.BytesIO],
    tender_ref: Optional[str] = None,
    max_workers: int = 1,
    on_progress: Optional[Callable[[str], None]] = None,
    light_mode: bool = False,
) -> List[ProcessedDocument]:
    """
    Process documents one by one sequentially for better tracking and resource usage.
    
    Args:
        files: Dict of filename -> BytesIO
        tender_ref: Tender reference for logging
        max_workers: Ignored (kept for API compat), always processes sequentially
        on_progress: Optional callback for progress updates
        light_mode: If True, skip OCR for scanned PDFs (bordereau already available)
        
    Returns:
        List of ProcessedDocument results
    """
    results = []
    
    # Filter out hidden/temp files
    valid_files = {
        k: v for k, v in files.items() 
        if not k.split('/')[-1].startswith(('~$', '.', '__'))
    }
    
    total = len(valid_files)
    if on_progress:
        on_progress(f"Processing {total} documents sequentially (focused mode)")
    
    for idx, (filename, file_bytes) in enumerate(valid_files.items(), 1):
        if on_progress:
            on_progress(f"[{idx}/{total}] Processing: {filename}")
        
        t0 = time.monotonic()
        try:
            result = process_single_document(filename, file_bytes, tender_ref, light_mode)
            elapsed = time.monotonic() - t0
            results.append(result)
            
            if on_progress:
                status = "✓" if result.success else "✗"
                on_progress(f"[{idx}/{total}] {status} {filename} → {result.document_type.value} ({elapsed:.1f}s)")
            
            if elapsed > MAX_DOCUMENT_PROCESSING_SECONDS:
                logger.warning(f"⚠ Document {filename} took {elapsed:.1f}s (>{MAX_DOCUMENT_PROCESSING_SECONDS}s limit)")
                
        except Exception as e:
            elapsed = time.monotonic() - t0
            logger.error(f"Error processing {filename} after {elapsed:.1f}s: {e}")
            results.append(ProcessedDocument(
                filename=filename,
                document_type=DocumentType.UNKNOWN,
                raw_text="",
                page_count=None,
                extraction_method=ExtractionMethod.DIGITAL,
                file_size_bytes=0,
                mime_type="",
                success=False,
                error=str(e)
            ))
    
    return results


def build_combined_article_index(documents: List[ProcessedDocument]) -> Optional[Dict]:
    """
    Build combined article index from all CPS/RC documents.
    Structure: {"CPS": {...}, "RC": {...}}
    Includes full document content for lookups outside indexed articles.
    """
    index = {}
    
    for doc in documents:
        if doc.document_type in [DocumentType.CPS, DocumentType.RC]:
            doc_type = doc.document_type.value
            
            if doc_type not in index:
                index[doc_type] = {
                    "filename": doc.filename,
                    "total_articles": len(doc.article_index) if doc.article_index else 0,
                    "total_chars": len(doc.raw_text),
                    "full_content": doc.raw_text,  # Keep full content for lookups outside articles
                    "articles": doc.article_index or []
                }
    
    return index if index else None


def select_best_document_per_type(
    documents: List[ProcessedDocument]
) -> Dict[DocumentType, ProcessedDocument]:
    """
    Select the best document for each type (French preferred over Arabic).
    """
    from app.services.extractor import _is_french_document, _is_arabic_document
    
    type_candidates: Dict[DocumentType, List[ProcessedDocument]] = {}
    
    for doc in documents:
        if doc.success and doc.raw_text:
            dt = doc.document_type
            if dt not in type_candidates:
                type_candidates[dt] = []
            type_candidates[dt].append(doc)
    
    best_docs = {}
    
    for doc_type, candidates in type_candidates.items():
        if not candidates:
            continue
            
        french_docs = []
        arabic_docs = []
        neutral_docs = []
        
        for doc in candidates:
            # Quick language check using first 1000 chars
            sample = doc.raw_text[:1000] if doc.raw_text else ""
            is_french = _is_french_document(doc.filename, sample)
            is_arabic = _is_arabic_document(doc.filename, sample)
            
            if is_french and not is_arabic:
                french_docs.append(doc)
            elif is_arabic and not is_french:
                arabic_docs.append(doc)
            else:
                neutral_docs.append(doc)
        
        # Priority: French > Neutral > Arabic
        if french_docs:
            best_docs[doc_type] = french_docs[0]
        elif neutral_docs:
            best_docs[doc_type] = neutral_docs[0]
        elif arabic_docs:
            best_docs[doc_type] = arabic_docs[0]
        else:
            best_docs[doc_type] = candidates[0]
    
    return best_docs


async def process_tender_documents(
    zip_bytes: bytes,
    tender_ref: Optional[str] = None,
    max_workers: int = 5,
    on_progress: Optional[Callable[[str], None]] = None,
    on_bordereau_ready: Optional[Callable[[List[ProcessedDocument]], None]] = None
) -> Tuple[List[ProcessedDocument], Optional[Dict]]:
    """
    Full processing pipeline for a tender's documents.
    
    PRIORITY FLOW:
    1. Extract all files (including nested ZIPs)
    2. AI-based file detection to prioritize Bordereau files
    3. Process bordereau files FIRST with OCR → Return immediately for AI extraction
    4. Then process CPS and other files for indexing
    5. Build article index from CPS/RC (with full content for lookups)
    
    Args:
        zip_bytes: Raw ZIP file bytes
        tender_ref: Tender reference for logging
        max_workers: Concurrent workers for extraction/OCR
        on_progress: Progress callback
        on_bordereau_ready: Callback when bordereau files are processed (for early AI extraction)
        
    Returns:
        Tuple of (all_documents, article_index)
    """
    if on_progress:
        on_progress(f"Extracting files for {tender_ref or 'tender'}...")
    
    # Step 1: Extract all files including nested ZIPs
    files = extract_all_nested_zips(zip_bytes)
    
    if not files:
        logger.error(f"No files extracted from ZIP for {tender_ref}")
        return [], None
    
    logger.info(f"Extracted {len(files)} files from ZIP")
    
    # Step 2: AI-based file detection to prioritize Bordereau files
    if on_progress:
        on_progress("🔍 AI analyzing filenames to detect Bordereau files...")
    
    filenames = list(files.keys())
    bordereau_files, cps_files, other_files = detect_and_prioritize_files(filenames)
    
    if on_progress:
        if bordereau_files:
            on_progress(f"✓ Detected {len(bordereau_files)} potential Bordereau files")
        elif cps_files:
            on_progress(f"✓ No Bordereau files found, will use {len(cps_files)} CPS files")
    
    logger.info(f"📋 Priority: {len(bordereau_files)} bordereau → {len(cps_files)} CPS → {len(other_files)} others")
    
    all_documents = []
    has_excel_bordereau = False  # Track if we got bordereau from Excel (no OCR needed)
    
    # Step 3: Process BORDEREAU files FIRST (priority processing)
    if bordereau_files:
        if on_progress:
            on_progress(f"🚀 Priority processing {len(bordereau_files)} bordereau files...")
        
        bordereau_file_dict = {k: files[k] for k in bordereau_files if k in files}
        bordereau_docs = await process_documents_concurrent(
            bordereau_file_dict,
            tender_ref,
            max_workers,
            on_progress
        )
        all_documents.extend(bordereau_docs)
        
        # Check if any Excel bordereau was successfully extracted
        from app.services.extractor import is_excel_file
        has_excel_bordereau = any(
            d.success and d.raw_text and is_excel_file(d.filename)
            for d in bordereau_docs
        )
        
        if has_excel_bordereau:
            logger.info("⚡ Excel bordereau found — remaining files will skip OCR")
            if on_progress:
                on_progress("⚡ Excel bordereau found — skipping OCR for remaining files")
        
        # Call the callback so AI can start bordereau extraction immediately
        if on_bordereau_ready and bordereau_docs:
            successful_docs = [d for d in bordereau_docs if d.success and d.raw_text]
            if successful_docs:
                logger.info(f"🎯 Bordereau files ready - triggering early AI extraction")
                on_bordereau_ready(successful_docs)
    
    # Step 4: Process CPS files (for metadata and article indexing)
    # Use light_mode if Excel bordereau already covers items
    if cps_files:
        if on_progress:
            on_progress(f"📄 Processing {len(cps_files)} CPS files...")
        
        cps_file_dict = {k: files[k] for k in cps_files if k in files}
        cps_docs = await process_documents_concurrent(
            cps_file_dict,
            tender_ref,
            max_workers,
            on_progress,
            light_mode=has_excel_bordereau,
        )
        all_documents.extend(cps_docs)
        
        # If no bordereau files were found, CPS might contain bordereau
        # Trigger callback so AI can try extracting from CPS
        if not bordereau_files and on_bordereau_ready and cps_docs:
            successful_cps = [d for d in cps_docs if d.success and d.raw_text]
            if successful_cps:
                logger.info(f"🎯 No bordereau files - using CPS for bordereau extraction")
                on_bordereau_ready(successful_cps)
    
    # Step 5: Detect merged files in remaining files
    other_file_dict = {k: files[k] for k in other_files if k in files}
    merged = detect_merged_files(other_file_dict)
    if merged:
        for merged_file, refs in merged.items():
            if on_progress:
                on_progress(f"⚠ Merged file detected: {merged_file} ({len(refs)} refs)")
            if merged_file in other_file_dict:
                split_files = split_merged_file(
                    merged_file, 
                    other_file_dict[merged_file], 
                    refs
                )
                del other_file_dict[merged_file]
                other_file_dict.update(split_files)
    
    # Step 6: Process remaining files (always light_mode if Excel bordereau found)
    if other_file_dict:
        if on_progress:
            on_progress(f"📄 Processing {len(other_file_dict)} remaining files...")
        
        other_docs = await process_documents_concurrent(
            other_file_dict,
            tender_ref,
            max_workers,
            on_progress,
            light_mode=has_excel_bordereau,
        )
        all_documents.extend(other_docs)
    
    # Step 7: Build combined article index (includes full content for lookups)
    article_index = build_combined_article_index(all_documents)
    
    if article_index:
        total_articles = sum(
            idx.get("total_articles", 0) 
            for idx in article_index.values()
        )
        if on_progress:
            on_progress(f"✓ Indexed {total_articles} articles across {len(article_index)} documents")
    
    # Step 8: Return ALL successfully processed documents
    success_count = sum(1 for d in all_documents if d.success)
    logger.info(f"Processed {success_count}/{len(all_documents)} documents successfully")
    
    return all_documents, article_index
