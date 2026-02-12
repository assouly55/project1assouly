"""
Tender AI Platform - Technical Pages Extractor
Identifies and extracts technical specification pages from tender documents.

Flow:
1. AI analyzes stored document texts (with page markers) to identify technical specs
2. AI identifies exact page ranges with technical attributes
3. Re-downloads the tender ZIP via Playwright
4. Converts DOCX→PDF if needed, then extracts specific pages
5. Returns the extracted pages as a single PDF (base64)
"""

import asyncio
import base64
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from typing import Optional, Dict, List, Tuple, Any
from loguru import logger

from app.services.ai_pipeline import ai_service
from app.services.pipeline_processor import extract_all_nested_zips
from app.services.scraper import TenderScraper


# ---------------------------------------------------------------------------
# Step 1 — AI identification of document + pages
# ---------------------------------------------------------------------------

def _identify_technical_document_and_pages(
    documents: list,
    tender_reference: str,
) -> Optional[Dict[str, Any]]:
    """
    Use AI to identify which document contains technical specs
    and which pages contain them.

    We now send the FULL text (or large chunks) with page markers so the AI
    can pinpoint exact pages rather than guessing from a truncated sample.
    """
    # Build document summaries for AI — prioritise CPS, send full text with page markers
    doc_summaries = []
    for doc in documents:
        text = doc.raw_text or ""
        doc_type = doc.document_type or "UNKNOWN"
        filename = doc.filename or "unknown"
        page_count = doc.page_count or 0

        # For CPS: send up to 60k chars (≈30 pages) so AI sees technical sections
        # For others: 5k chars is enough for elimination
        if doc_type == "CPS":
            max_chars = 60000
        elif doc_type in ("BORDEREAU", "BDP"):
            max_chars = 3000
        else:
            max_chars = 5000

        sample = text[:max_chars] if text else "(empty)"

        doc_summaries.append(
            f"--- DOCUMENT: {filename} ---\n"
            f"Type: {doc_type}\n"
            f"Total pages: {page_count}\n"
            f"Content:\n{sample}\n"
        )

    all_summaries = "\n\n".join(doc_summaries)

    system_prompt = """Tu es un expert en analyse de documents d'appels d'offres marocains.

Ta mission: identifier le document qui contient les SPÉCIFICATIONS TECHNIQUES / CARACTÉRISTIQUES TECHNIQUES des articles/fournitures, et identifier les PAGES EXACTES.

IMPORTANT - Comment identifier les pages:
- Le texte fourni contient des marqueurs de page comme "[PAGE 1]", "--- Page 2 ---", ou des sauts de page
- Cherche les sections intitulées "Spécifications techniques", "Caractéristiques techniques", "Prescriptions techniques", "Description technique"
- Note les numéros de page de DÉBUT et FIN de ces sections
- Si le document est un DOCX, les pages correspondent aux sections logiques

Les spécifications techniques se trouvent généralement dans:
- Le CPS (Cahier des Prescriptions Spéciales)
- Parfois dans des annexes techniques

Les spécifications techniques incluent:
- Dimensions, poids, matériaux
- Normes (NM, ISO, EN, etc.)
- Caractéristiques de performance
- Descriptions détaillées des articles à fournir
- Tableaux de caractéristiques

⚠️ NE PAS confondre avec:
- Le bordereau des prix (qui liste juste les articles/quantités/prix)
- Les clauses administratives (garanties, pénalités, délais)
- Les conditions de soumission

⚠️ IMPORTANT: Ne donne PAS une seule page si les spécifications s'étendent sur plusieurs pages.
Analyse le contenu et donne la plage COMPLÈTE.

Tu dois répondre en JSON:
{
    "document_filename": "nom_du_fichier.pdf_ou_docx",
    "page_start": <numéro de la première page (1-indexed)>,
    "page_end": <numéro de la dernière page (1-indexed)>,
    "reasoning": "explication courte de ce qui a été trouvé",
    "confidence": 0.0-1.0
}

Si les spécifications techniques sont réparties sur plusieurs sections non-contiguës:
{
    "document_filename": "nom_du_fichier",
    "page_ranges": [[start1, end1], [start2, end2]],
    "reasoning": "explication courte",
    "confidence": 0.0-1.0
}

Si tu ne peux pas déterminer les pages exactes mais tu sais quel document c'est, donne TOUTES les pages:
{
    "document_filename": "nom_du_fichier",
    "page_start": 1,
    "page_end": <total_pages>,
    "reasoning": "Pages exactes non déterminables, extraction complète",
    "confidence": 0.5
}

Si aucune spécification technique n'est trouvée:
{"document_filename": null, "reasoning": "Aucune spécification technique trouvée"}
"""

    user_prompt = f"""Appel d'offres: {tender_reference}

Voici les documents disponibles avec leur contenu:

{all_summaries}

Identifie le document et les pages exactes contenant les spécifications/caractéristiques techniques des articles.
Analyse bien le contenu pour trouver les bonnes pages — ne devine pas, base-toi sur le texte fourni."""

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


# ---------------------------------------------------------------------------
# Step 2 — DOCX → PDF conversion
# ---------------------------------------------------------------------------

def _convert_docx_to_pdf(docx_bytes: bytes) -> Optional[bytes]:
    """
    Convert a DOCX file to PDF using LibreOffice (headless).
    Falls back to python-docx + reportlab if LibreOffice is not available.
    """
    # Try LibreOffice first (best quality)
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            docx_path = os.path.join(tmpdir, "input.docx")
            with open(docx_path, "wb") as f:
                f.write(docx_bytes)

            result = subprocess.run(
                [
                    "libreoffice", "--headless", "--convert-to", "pdf",
                    "--outdir", tmpdir, docx_path,
                ],
                capture_output=True, timeout=60,
            )

            pdf_path = os.path.join(tmpdir, "input.pdf")
            if os.path.exists(pdf_path):
                with open(pdf_path, "rb") as f:
                    pdf_bytes = f.read()
                logger.info(f"✅ DOCX→PDF via LibreOffice ({len(pdf_bytes)} bytes)")
                return pdf_bytes
            else:
                logger.warning(f"LibreOffice conversion produced no output: {result.stderr.decode()[:300]}")
    except FileNotFoundError:
        logger.warning("LibreOffice not found, trying fallback conversion")
    except subprocess.TimeoutExpired:
        logger.warning("LibreOffice conversion timed out")
    except Exception as e:
        logger.warning(f"LibreOffice conversion failed: {e}")

    # Fallback: use pymupdf (fitz) to open DOCX if supported
    try:
        import fitz  # pymupdf
        doc = fitz.open(stream=docx_bytes, filetype="docx")
        pdf_bytes = doc.convert_to_pdf()
        doc.close()
        logger.info(f"✅ DOCX→PDF via PyMuPDF ({len(pdf_bytes)} bytes)")
        return pdf_bytes
    except Exception as e:
        logger.warning(f"PyMuPDF DOCX conversion failed: {e}")

    logger.error("All DOCX→PDF conversion methods failed")
    return None


# ---------------------------------------------------------------------------
# Step 3 — PDF page extraction
# ---------------------------------------------------------------------------

def _extract_pages_from_pdf(pdf_bytes: bytes, pages: List[int]) -> Optional[bytes]:
    """
    Extract specific pages from a PDF file.
    pages: List of 1-indexed page numbers
    """
    try:
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        writer = pypdf.PdfWriter()

        total_pages = len(reader.pages)
        extracted = 0

        for page_num in sorted(pages):
            idx = page_num - 1  # 1-indexed → 0-indexed
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

        logger.info(f"✅ Extracted {extracted}/{len(pages)} pages from PDF ({total_pages} total)")
        return output.read()

    except Exception as e:
        logger.error(f"Failed to extract PDF pages: {e}")
        return None


# ---------------------------------------------------------------------------
# Step 4 — File lookup in ZIP
# ---------------------------------------------------------------------------

def _find_file_in_zip(
    files: Dict[str, io.BytesIO],
    target_filename: str,
) -> Optional[Tuple[str, io.BytesIO]]:
    """Find a file in the extracted ZIP by matching filename (fuzzy)."""
    target_lower = target_filename.lower()
    target_base = target_lower.rsplit("/", 1)[-1]

    # Exact match
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

    # Partial match
    for path, data in files.items():
        if target_base in path.lower():
            data.seek(0)
            return (path, data)

    logger.error(f"File not found in ZIP: {target_filename}")
    logger.debug(f"Available files: {list(files.keys())}")
    return None


# ---------------------------------------------------------------------------
# Step 5 — Download ZIP + extract pages pipeline
# ---------------------------------------------------------------------------

async def _download_and_extract_technical_pages(
    source_url: str,
    target_filename: str,
    pages: List[int],
) -> Optional[bytes]:
    """
    Re-download the tender ZIP, find target file, convert if DOCX, extract pages.
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

            file_data.seek(0)
            file_bytes = file_data.read()

            # Determine if we need to convert DOCX → PDF
            is_docx = path.lower().endswith((".docx", ".doc"))

            if is_docx:
                logger.info("📄 Target is DOCX — converting to PDF...")
                pdf_bytes = _convert_docx_to_pdf(file_bytes)
                if not pdf_bytes:
                    logger.error("DOCX→PDF conversion failed")
                    return None
            else:
                pdf_bytes = file_bytes

            # Extract the specific pages
            return _extract_pages_from_pdf(pdf_bytes, pages)

        finally:
            await context.close()
            await browser.close()


# ---------------------------------------------------------------------------
# Public entry point (synchronous)
# ---------------------------------------------------------------------------

def extract_technical_pages_sync(
    tender,
    documents: list,
) -> Optional[Dict[str, Any]]:
    """
    Synchronous wrapper for the full technical page extraction pipeline.
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
    download_thread.join(timeout=180)

    if result_holder["error"]:
        logger.error(f"Technical page extraction failed: {result_holder['error']}")
        return None

    pdf_bytes = result_holder["pdf_bytes"]
    if not pdf_bytes:
        logger.error("No PDF bytes returned")
        return None

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
