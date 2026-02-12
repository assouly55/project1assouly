"""
Tender AI Platform - Technical Pages Extractor
Identifies and extracts technical specification pages from tender documents.

Flow:
1. AI analyzes stored document texts to identify which document has technical specs
2. AI identifies exact page ranges with technical attributes
3. Re-downloads the tender ZIP via Playwright
4. Extracts those specific pages from the PDF
5. Returns the extracted pages as a single PDF (base64)
"""

import asyncio
import base64
import io
import json
import re
import sys
import threading
from typing import Optional, Dict, List, Tuple, Any
from loguru import logger

from app.services.ai_pipeline import ai_service
from app.services.pipeline_processor import extract_all_nested_zips
from app.services.scraper import TenderScraper


def _identify_technical_document_and_pages(
    documents: list,
    tender_reference: str,
) -> Optional[Dict[str, Any]]:
    """
    Use AI to identify which document contains technical specs
    and which pages contain them.
    
    Returns: {
        "document_filename": str,
        "pages": [1, 2, 3, ...],  # 1-indexed page numbers
        "reasoning": str
    }
    """
    # Build document summaries for AI
    doc_summaries = []
    for doc in documents:
        text = doc.raw_text or ""
        doc_type = doc.document_type or "UNKNOWN"
        filename = doc.filename or "unknown"
        page_count = doc.page_count or 0
        
        # For CPS documents, provide more text since they're the primary target
        max_chars = 8000 if doc_type == "CPS" else 3000
        sample = text[:max_chars] if text else "(empty)"
        
        doc_summaries.append(
            f"--- DOCUMENT: {filename} ---\n"
            f"Type: {doc_type}\n"
            f"Pages: {page_count}\n"
            f"Content sample:\n{sample}\n"
        )
    
    all_summaries = "\n\n".join(doc_summaries)
    
    system_prompt = """Tu es un expert en analyse de documents d'appels d'offres marocains.

Ta mission: identifier le document qui contient les SPÉCIFICATIONS TECHNIQUES / CARACTÉRISTIQUES TECHNIQUES des articles/fournitures, et identifier les PAGES EXACTES.

Les spécifications techniques se trouvent généralement dans:
- Le CPS (Cahier des Prescriptions Spéciales) — section "Spécifications techniques" ou "Caractéristiques techniques"
- Parfois dans des annexes techniques

Les spécifications techniques incluent:
- Dimensions, poids, matériaux
- Normes (NM, ISO, EN, etc.)
- Caractéristiques de performance
- Descriptions détaillées des articles à fournir

⚠️ NE PAS confondre avec:
- Le bordereau des prix (qui liste juste les articles/quantités/prix)
- Les clauses administratives
- Les conditions de soumission

Tu dois répondre en JSON:
{
    "document_filename": "nom_du_fichier.pdf",
    "page_start": <numéro de la première page (1-indexed)>,
    "page_end": <numéro de la dernière page (1-indexed)>,
    "reasoning": "explication courte",
    "confidence": 0.0-1.0
}

Si les spécifications techniques sont réparties sur plusieurs sections non-contiguës, utilise:
{
    "document_filename": "nom_du_fichier.pdf",
    "page_ranges": [[start1, end1], [start2, end2]],
    "reasoning": "explication courte",
    "confidence": 0.0-1.0
}

Si aucune spécification technique n'est trouvée, réponds:
{"document_filename": null, "reasoning": "Aucune spécification technique trouvée"}
"""
    
    user_prompt = f"""Appel d'offres: {tender_reference}

Voici les documents disponibles:

{all_summaries}

Identifie le document et les pages exactes contenant les spécifications/caractéristiques techniques des articles."""
    
    response = ai_service._call_ai(system_prompt, user_prompt, max_tokens=1024)
    if not response:
        logger.error("AI failed to identify technical pages")
        return None
    
    result = ai_service._parse_json_response(response)
    if not result:
        logger.error(f"Failed to parse AI response for technical pages: {response[:200]}")
        return None
    
    if not result.get("document_filename"):
        logger.warning(f"No technical document found: {result.get('reasoning')}")
        return None
    
    # Normalize pages into a flat list
    pages = []
    if "page_ranges" in result:
        for start, end in result["page_ranges"]:
            pages.extend(range(int(start), int(end) + 1))
    elif "page_start" in result and "page_end" in result:
        pages = list(range(int(result["page_start"]), int(result["page_end"]) + 1))
    
    if not pages:
        logger.warning("AI identified document but no pages")
        return None
    
    logger.info(f"✅ AI identified technical pages: {result['document_filename']} pages {pages}")
    logger.info(f"   Reasoning: {result.get('reasoning', 'N/A')}")
    logger.info(f"   Confidence: {result.get('confidence', 'N/A')}")
    
    return {
        "document_filename": result["document_filename"],
        "pages": pages,
        "reasoning": result.get("reasoning", ""),
        "confidence": result.get("confidence", 0.0),
    }


def _extract_pages_from_pdf(pdf_bytes: bytes, pages: List[int]) -> Optional[bytes]:
    """
    Extract specific pages from a PDF file.
    
    Args:
        pdf_bytes: Raw PDF file bytes
        pages: List of 1-indexed page numbers to extract
        
    Returns:
        New PDF bytes containing only the specified pages
    """
    try:
        import pypdf
        
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        writer = pypdf.PdfWriter()
        
        total_pages = len(reader.pages)
        extracted = 0
        
        for page_num in sorted(pages):
            # Convert 1-indexed to 0-indexed
            idx = page_num - 1
            if 0 <= idx < total_pages:
                writer.add_page(reader.pages[idx])
                extracted += 1
            else:
                logger.warning(f"Page {page_num} out of range (total: {total_pages})")
        
        if extracted == 0:
            logger.error("No valid pages extracted")
            return None
        
        output = io.BytesIO()
        writer.write(output)
        output.seek(0)
        
        logger.info(f"✅ Extracted {extracted} pages from PDF")
        return output.read()
        
    except Exception as e:
        logger.error(f"Failed to extract PDF pages: {e}")
        return None


def _find_file_in_zip(
    files: Dict[str, io.BytesIO], 
    target_filename: str
) -> Optional[Tuple[str, io.BytesIO]]:
    """
    Find a file in the extracted ZIP by matching filename.
    Uses fuzzy matching to handle path differences.
    """
    target_lower = target_filename.lower()
    target_base = target_lower.rsplit("/", 1)[-1]
    
    # Exact match first
    for path, data in files.items():
        if path.lower() == target_lower:
            data.seek(0)
            return (path, data)
    
    # Basename match
    for path, data in files.items():
        base = path.lower().rsplit("/", 1)[-1]
        if base == target_base:
            data.seek(0)
            return (path, data)
    
    # Partial match (filename contained in path)
    for path, data in files.items():
        if target_base in path.lower():
            data.seek(0)
            return (path, data)
    
    logger.error(f"File not found in ZIP: {target_filename}")
    logger.debug(f"Available files: {list(files.keys())}")
    return None


async def _download_and_extract_technical_pages(
    source_url: str,
    target_filename: str,
    pages: List[int],
) -> Optional[bytes]:
    """
    Re-download the tender ZIP and extract specific pages from the target PDF.
    """
    from playwright.async_api import async_playwright
    from app.core.config import settings
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=settings.SCRAPER_HEADLESS)
        context = await browser.new_context(accept_downloads=True)
        
        try:
            scraper = TenderScraper()
            download_result = await scraper.download_tender_zip(
                context, source_url, idx=0, website_metadata=None
            )
            
            if not download_result.success or not download_result.zip_bytes:
                logger.error(f"Failed to download ZIP: {download_result.error}")
                return None
            
            logger.info(f"✅ ZIP downloaded ({len(download_result.zip_bytes)} bytes)")
            
            # Extract all files from ZIP
            files = extract_all_nested_zips(download_result.zip_bytes)
            
            # Find the target file
            match = _find_file_in_zip(files, target_filename)
            if not match:
                return None
            
            path, file_data = match
            logger.info(f"✅ Found target file: {path}")
            
            # Read the PDF bytes
            file_data.seek(0)
            pdf_bytes = file_data.read()
            
            # Extract the specific pages
            return _extract_pages_from_pdf(pdf_bytes, pages)
            
        finally:
            await context.close()
            await browser.close()


def extract_technical_pages_sync(
    tender,
    documents: list,
) -> Optional[Dict[str, Any]]:
    """
    Synchronous wrapper for the full technical page extraction pipeline.
    
    Returns: {
        "pdf_base64": str,
        "filename": str,
        "pages": list,
        "page_count": int,
        "reasoning": str,
    }
    """
    logger.info(f"🔍 Starting technical page extraction for {tender.external_reference}")
    
    # Step 1: AI identifies the document and pages
    identification = _identify_technical_document_and_pages(
        documents, tender.external_reference
    )
    
    if not identification:
        return None
    
    target_filename = identification["document_filename"]
    pages = identification["pages"]
    
    logger.info(f"📄 Target: {target_filename}, pages: {pages}")
    
    # Step 2: Download and extract pages (needs Playwright in a thread)
    result_holder = {"pdf_bytes": None, "error": None}
    
    def run_download():
        if sys.platform == "win32":
            loop = asyncio.ProactorEventLoop()
        else:
            loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            pdf_bytes = loop.run_until_complete(
                _download_and_extract_technical_pages(
                    tender.source_url, target_filename, pages
                )
            )
            result_holder["pdf_bytes"] = pdf_bytes
        except Exception as e:
            result_holder["error"] = str(e)
            logger.error(f"Download/extract failed: {e}")
        finally:
            loop.close()
    
    download_thread = threading.Thread(target=run_download)
    download_thread.start()
    download_thread.join(timeout=180)  # 3 minute timeout
    
    if result_holder["error"]:
        logger.error(f"Technical page extraction failed: {result_holder['error']}")
        return None
    
    pdf_bytes = result_holder["pdf_bytes"]
    if not pdf_bytes:
        logger.error("No PDF bytes returned")
        return None
    
    # Encode as base64
    pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
    
    logger.info(f"✅ Technical pages extracted: {len(pages)} pages, {len(pdf_bytes)} bytes")
    
    return {
        "pdf_base64": pdf_base64,
        "source_document": target_filename,
        "pages": pages,
        "page_count": len(pages),
        "reasoning": identification.get("reasoning", ""),
        "confidence": identification.get("confidence", 0.0),
    }
