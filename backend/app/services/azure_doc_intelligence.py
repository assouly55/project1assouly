# -*- coding: utf-8 -*-
"""
Tender AI Platform — Azure Document Intelligence Service
=========================================================
Replaces coordinate-based table OCR for bordereau pages.

Flow:
  1. Receive specific PDF pages identified as bordereau
  2. Send to Azure DI (prebuilt-layout model)
  3. Return structured table data as formatted text for AI extraction

Azure DI is ONLY used for bordereau pages that need high-fidelity
table extraction from scanned PDFs. Regular OCR stays with Tesseract.
"""

import io
import time
from typing import Dict, List, Optional, Tuple
from loguru import logger


def _get_client():
    """Lazy-initialize Azure Document Intelligence client."""
    from azure.core.credentials import AzureKeyCredential
    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from app.core.config import settings

    if not settings.AZURE_DI_KEY or not settings.AZURE_DI_ENDPOINT:
        raise ValueError(
            "Azure Document Intelligence not configured. "
            "Set AZURE_DI_ENDPOINT and AZURE_DI_KEY in .env"
        )

    return DocumentIntelligenceClient(
        endpoint=settings.AZURE_DI_ENDPOINT,
        credential=AzureKeyCredential(settings.AZURE_DI_KEY),
    )


# ---------------------------------------------------------------------------
# Table formatting — convert Azure DI tables into structured text
# ---------------------------------------------------------------------------

def _format_table_as_text(table) -> str:
    """
    Convert an Azure DI table object into a pipe-delimited text table.
    This format is well-understood by LLMs for item extraction.
    """
    if not table.cells:
        return ""

    # Build row-major grid
    max_row = max(c.row_index for c in table.cells) + 1
    max_col = max(c.column_index for c in table.cells) + 1
    grid = [["" for _ in range(max_col)] for _ in range(max_row)]

    for cell in table.cells:
        content = (cell.content or "").strip().replace("\n", " ")
        grid[cell.row_index][cell.column_index] = content

    # Format as pipe-delimited table
    lines = []
    for row_idx, row in enumerate(grid):
        line = " | ".join(row)
        lines.append(line)
        if row_idx == 0:
            # Add separator after header
            lines.append("-" * len(line))

    return "\n".join(lines)


def _format_table_as_latex(table) -> str:
    """
    Convert an Azure DI table into LaTeX tabular format.
    Provides clear column separation for AI parsing.
    """
    if not table.cells:
        return ""

    max_row = max(c.row_index for c in table.cells) + 1
    max_col = max(c.column_index for c in table.cells) + 1
    grid = [["" for _ in range(max_col)] for _ in range(max_row)]

    for cell in table.cells:
        content = (cell.content or "").strip().replace("\n", " ")
        # Escape LaTeX special chars
        for ch in ["&", "%", "$", "#", "_", "{", "}", "~", "^"]:
            content = content.replace(ch, f"\\{ch}")
        grid[cell.row_index][cell.column_index] = content

    col_spec = "|".join(["l"] * max_col)
    lines = [
        f"% TABLE — {max_row} rows x {max_col} cols",
        f"\\begin{{tabular}}{{|{col_spec}|}}",
        "\\hline",
    ]
    for row in grid:
        row_str = " & ".join(row) + " \\\\"
        lines.append(row_str)
        lines.append("\\hline")
    lines.append("\\end{tabular}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Extract specific pages from a PDF as a new PDF
# ---------------------------------------------------------------------------

def _extract_pdf_pages(pdf_bytes: bytes, page_numbers: List[int]) -> bytes:
    """
    Extract specific pages from a PDF and return as new PDF bytes.
    page_numbers is 1-indexed.
    """
    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    writer = pypdf.PdfWriter()

    for page_num in page_numbers:
        idx = page_num - 1  # Convert to 0-indexed
        if 0 <= idx < len(reader.pages):
            writer.add_page(reader.pages[idx])

    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


# ---------------------------------------------------------------------------
# Main API: Analyze bordereau pages with Azure DI
# ---------------------------------------------------------------------------

def analyze_bordereau_pages(
    file_bytes: io.BytesIO,
    page_numbers: List[int],
    output_format: str = "text",
) -> Dict[int, str]:
    """
    Send specific PDF pages to Azure Document Intelligence for
    high-fidelity table extraction.

    Args:
        file_bytes: Full PDF as BytesIO
        page_numbers: 1-indexed page numbers to analyze
        output_format: "text" (pipe-delimited) or "latex"

    Returns:
        Dict mapping page_number → extracted table content
    """
    if not page_numbers:
        return {}

    t0 = time.monotonic()
    logger.info(
        f"🔵 Azure DI: Analyzing {len(page_numbers)} bordereau pages: {page_numbers}"
    )

    try:
        client = _get_client()
    except ValueError as e:
        logger.error(f"Azure DI not configured: {e}")
        return {}

    file_bytes.seek(0)
    pdf_bytes = file_bytes.read()

    # Extract only the bordereau pages into a smaller PDF
    try:
        subset_pdf = _extract_pdf_pages(pdf_bytes, page_numbers)
        logger.info(
            f"   Extracted {len(page_numbers)} pages → {len(subset_pdf)} bytes"
        )
    except Exception as e:
        logger.error(f"Failed to extract pages: {e}")
        return {}

    # Send to Azure DI
    try:
        from azure.ai.documentintelligence.models import AnalyzeDocumentRequest

        poller = client.begin_analyze_document(
            "prebuilt-layout",
            analyze_request=AnalyzeDocumentRequest(bytes_source=subset_pdf),
            content_type="application/octet-stream",
        )
        result = poller.result()
    except Exception as e:
        logger.error(f"Azure DI analysis failed: {e}")
        return {}

    elapsed = time.monotonic() - t0
    logger.info(f"   Azure DI response in {elapsed:.1f}s")

    # Map results back to original page numbers
    page_results: Dict[int, str] = {}

    # Process tables by page
    if result.tables:
        # Group tables by their page (in the subset PDF, pages are 0-indexed)
        tables_by_subset_page: Dict[int, list] = {}
        for table in result.tables:
            # Each table has bounding_regions with page_number (1-indexed in subset)
            if table.bounding_regions:
                subset_page = table.bounding_regions[0].page_number  # 1-indexed
            else:
                subset_page = 1
            tables_by_subset_page.setdefault(subset_page, []).append(table)

        formatter = _format_table_as_latex if output_format == "latex" else _format_table_as_text

        for subset_idx, original_page in enumerate(page_numbers):
            subset_page = subset_idx + 1  # 1-indexed
            tables = tables_by_subset_page.get(subset_page, [])

            if tables:
                table_texts = []
                for ti, table in enumerate(tables):
                    formatted = formatter(table)
                    if formatted:
                        table_texts.append(
                            f"--- Table {ti + 1} (Page {original_page}) ---\n{formatted}"
                        )
                if table_texts:
                    page_results[original_page] = "\n\n".join(table_texts)
                    logger.info(
                        f"   ✅ Page {original_page}: {len(tables)} tables extracted"
                    )

    # For pages with no tables, extract paragraphs/text
    if result.pages:
        for subset_idx, original_page in enumerate(page_numbers):
            if original_page in page_results:
                continue  # Already have table data

            subset_page_num = subset_idx + 1
            # Find the page in results
            for page in result.pages:
                if page.page_number == subset_page_num:
                    # Extract lines as fallback
                    if page.lines:
                        text = "\n".join(
                            line.content for line in page.lines if line.content
                        )
                        if text.strip():
                            page_results[original_page] = text.strip()
                            logger.info(
                                f"   📝 Page {original_page}: text only ({len(text)} chars)"
                            )
                    break

    elapsed_total = time.monotonic() - t0
    logger.info(
        f"🔵 Azure DI complete: {len(page_results)}/{len(page_numbers)} pages "
        f"in {elapsed_total:.1f}s"
    )

    return page_results


# ---------------------------------------------------------------------------
# Drop-in replacement for table_ocr.reocr_bordereau_pages
# ---------------------------------------------------------------------------

def reocr_bordereau_pages_azure(
    file_bytes: io.BytesIO,
    original_text: str,
    force_pages: Optional[List[int]] = None,
) -> str:
    """
    Re-process bordereau pages using Azure Document Intelligence,
    replacing plain OCR text with structured table output.

    This is a drop-in replacement for table_ocr.reocr_bordereau_pages.
    Falls back to the original text if Azure DI is not configured or fails.
    """
    import re
    from app.services.table_ocr import detect_bordereau_pages

    if force_pages:
        bordereau_pages = force_pages
    else:
        bordereau_pages = detect_bordereau_pages(original_text)

    if not bordereau_pages:
        logger.info("No bordereau pages detected, keeping original OCR")
        return original_text

    # Limit pages
    MAX_PAGES = 20
    if len(bordereau_pages) > MAX_PAGES:
        logger.warning(
            f"Too many bordereau pages ({len(bordereau_pages)}), "
            f"limiting to first {MAX_PAGES}"
        )
        bordereau_pages = bordereau_pages[:MAX_PAGES]

    logger.info(
        f"🔵 Re-processing {len(bordereau_pages)} bordereau pages "
        f"with Azure DI: {bordereau_pages}"
    )

    # Analyze with Azure DI
    azure_results = analyze_bordereau_pages(
        file_bytes, bordereau_pages, output_format="text"
    )

    if not azure_results:
        logger.warning("Azure DI returned no results, keeping original OCR")
        return original_text

    # Merge Azure results into the original text
    result_text = original_text
    for page_num, azure_text in azure_results.items():
        if not azure_text or not azure_text.strip():
            continue

        # Replace the page content in the original text
        pattern = (
            rf'(---\s*Page\s+{page_num}\s*---\n)'
            rf'(.*?)'
            rf'(?=---\s*Page\s+\d+\s*---|$)'
        )

        def replace_page(match, _txt=azure_text):
            return f"{match.group(1)}[AZURE_DI_EXTRACTED]\n{_txt}\n\n"

        new_text = re.sub(pattern, replace_page, result_text, flags=re.DOTALL)
        if new_text != result_text:
            logger.info(
                f"✅ Page {page_num} replaced with Azure DI output: "
                f"{len(azure_text)} chars"
            )
            result_text = new_text

    return result_text
