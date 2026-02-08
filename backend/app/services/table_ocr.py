"""
Tender AI Platform - OpenCV + Tesseract Table OCR
High-performance table extraction using OpenCV for structure detection
and Tesseract for cell-by-cell OCR. Replaces PaddleOCR.

Performance target: ~5 seconds per page
"""

import io
import os
import threading
from typing import Tuple, List, Optional, Dict, Any
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from loguru import logger

import numpy as np
from PIL import Image

# Detect OS and set paths
import platform
IS_WINDOWS = platform.system() == "Windows"

# Paths for Windows
TESSERACT_PATH_WIN = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
POPPLER_PATH_WIN = r"C:\poppler-24.08.0\Library\bin"


@dataclass
class TableCell:
    """Represents a single cell in a detected table."""
    x: int
    y: int
    width: int
    height: int
    row: int
    col: int
    text: str = ""


@dataclass
class TableRegion:
    """Represents a detected table with its cells."""
    x: int
    y: int
    width: int
    height: int
    cells: List[TableCell]
    rows: int = 0
    cols: int = 0


def _get_poppler_path() -> Optional[str]:
    """Get Poppler path based on OS."""
    if IS_WINDOWS and os.path.exists(POPPLER_PATH_WIN):
        return POPPLER_PATH_WIN
    return None


def _configure_tesseract():
    """Configure Tesseract path based on OS."""
    import pytesseract
    if IS_WINDOWS and os.path.exists(TESSERACT_PATH_WIN):
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH_WIN


def _fast_orientation_fix(image: Image.Image) -> Image.Image:
    """
    Fast orientation correction using aspect ratio heuristic.
    Rotates landscape images 90° (faster than Tesseract OSD).
    """
    w, h = image.size
    if w > h * 1.20:
        logger.info("TableOCR: Rotating landscape page by 90°")
        return image.rotate(-90, expand=True)
    return image


def _preprocess_for_table_detection(img_array: np.ndarray) -> np.ndarray:
    """
    Preprocess image for table/grid detection.
    Converts to binary and enhances lines.
    """
    import cv2
    
    # Convert to grayscale if needed
    if len(img_array.shape) == 3:
        gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
    else:
        gray = img_array.copy()
    
    # Apply adaptive thresholding for better line detection
    binary = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        15, 10
    )
    
    return binary


def detect_table_structure(img_array: np.ndarray) -> List[TableRegion]:
    """
    Detect table grid structure using OpenCV morphological operations.
    Finds horizontal and vertical lines to identify table cells.
    
    Args:
        img_array: NumPy array of the image (RGB or grayscale)
    
    Returns:
        List of TableRegion objects with cell coordinates
    """
    import cv2
    
    binary = _preprocess_for_table_detection(img_array)
    height, width = binary.shape[:2]
    
    # Detect horizontal lines
    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (width // 30, 1))
    horizontal_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)
    horizontal_lines = cv2.dilate(horizontal_lines, horizontal_kernel, iterations=2)
    
    # Detect vertical lines
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, height // 30))
    vertical_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel)
    vertical_lines = cv2.dilate(vertical_lines, vertical_kernel, iterations=2)
    
    # Combine lines to get table grid
    table_mask = cv2.add(horizontal_lines, vertical_lines)
    
    # Find contours of potential table regions
    contours, _ = cv2.findContours(table_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    tables = []
    min_table_area = (width * height) * 0.01  # At least 1% of page
    
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        
        if area < min_table_area:
            continue
            
        # Extract table region for cell detection
        table_region = table_mask[y:y+h, x:x+w]
        cells = _detect_cells_in_table(table_region, x, y)
        
        if cells:
            # Calculate rows and cols
            rows = len(set(c.row for c in cells))
            cols = len(set(c.col for c in cells))
            
            tables.append(TableRegion(
                x=x, y=y, width=w, height=h,
                cells=cells, rows=rows, cols=cols
            ))
    
    return tables


def _detect_cells_in_table(table_mask: np.ndarray, offset_x: int, offset_y: int) -> List[TableCell]:
    """
    Detect individual cells within a table region.
    Uses contour detection on the inverted table grid.
    """
    import cv2
    
    # Invert to find cell interiors
    inverted = cv2.bitwise_not(table_mask)
    
    # Find cell contours
    contours, _ = cv2.findContours(inverted, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    
    cells = []
    min_cell_area = 100  # Minimum cell size in pixels
    
    cell_bounds = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        
        if area < min_cell_area or w < 10 or h < 10:
            continue
            
        cell_bounds.append((x + offset_x, y + offset_y, w, h))
    
    if not cell_bounds:
        return cells
    
    # Sort cells by position to assign row/col indices
    # Sort by Y first (rows), then by X (columns)
    cell_bounds.sort(key=lambda b: (b[1], b[0]))
    
    # Assign row indices based on Y clustering
    row_threshold = 15  # pixels
    current_row = 0
    last_y = cell_bounds[0][1] if cell_bounds else 0
    row_map = {}
    
    for x, y, w, h in cell_bounds:
        if abs(y - last_y) > row_threshold:
            current_row += 1
        row_map[(x, y, w, h)] = current_row
        last_y = y
    
    # For each row, assign column indices
    rows_data = {}
    for bounds, row in row_map.items():
        if row not in rows_data:
            rows_data[row] = []
        rows_data[row].append(bounds)
    
    for row, bounds_list in rows_data.items():
        bounds_list.sort(key=lambda b: b[0])  # Sort by X
        for col, (x, y, w, h) in enumerate(bounds_list):
            cells.append(TableCell(
                x=x, y=y, width=w, height=h,
                row=row, col=col
            ))
    
    return cells


def extract_table_with_structure(
    image: Image.Image,
    dpi: int = 200
) -> Dict[str, Any]:
    """
    Extract table data with full structure preservation.
    Uses OpenCV for grid detection and Tesseract for cell OCR.
    
    Args:
        image: PIL Image of the page
        dpi: Resolution (affects cell extraction accuracy)
    
    Returns:
        Dict with:
        - tables: List of table data with cells
        - text: Pipe-delimited text representation
        - metadata: Table boundaries and structure info
    """
    import pytesseract
    
    _configure_tesseract()
    
    # Fix orientation
    image = _fast_orientation_fix(image)
    
    # Convert to numpy array
    img_array = np.array(image)
    
    # Detect table structures
    tables = detect_table_structure(img_array)
    
    result_tables = []
    all_text_lines = []
    
    for table_idx, table in enumerate(tables):
        table_data = {
            "table_index": table_idx,
            "bounds": {
                "x": table.x, "y": table.y,
                "width": table.width, "height": table.height
            },
            "rows": table.rows,
            "cols": table.cols,
            "cells": []
        }
        
        # OCR each cell
        row_texts = {}
        
        for cell in table.cells:
            # Extract cell region
            cell_img = image.crop((
                max(0, cell.x - 2),
                max(0, cell.y - 2),
                min(image.width, cell.x + cell.width + 2),
                min(image.height, cell.y + cell.height + 2)
            ))
            
            # OCR the cell
            try:
                cell_text = pytesseract.image_to_string(
                    cell_img,
                    lang="fra+eng",
                    config="--psm 6 --oem 1"  # PSM 6: single block of text
                ).strip()
            except Exception:
                cell_text = ""
            
            cell.text = cell_text
            
            table_data["cells"].append({
                "row": cell.row,
                "col": cell.col,
                "x": cell.x, "y": cell.y,
                "width": cell.width, "height": cell.height,
                "text": cell_text
            })
            
            # Group by row for text output
            if cell.row not in row_texts:
                row_texts[cell.row] = []
            row_texts[cell.row].append((cell.col, cell_text))
        
        # Build pipe-delimited text for this table
        for row_idx in sorted(row_texts.keys()):
            row_cells = sorted(row_texts[row_idx], key=lambda x: x[0])
            row_text = " | ".join(text for _, text in row_cells)
            all_text_lines.append(row_text)
        
        result_tables.append(table_data)
        all_text_lines.append("")  # Empty line between tables
    
    return {
        "tables": result_tables,
        "text": "\n".join(all_text_lines),
        "metadata": {
            "tables_found": len(tables),
            "total_cells": sum(len(t.cells) for t in tables)
        }
    }


def ocr_page_with_table_detection(
    image: Image.Image,
    fallback_to_full_ocr: bool = True
) -> str:
    """
    OCR a single page with intelligent table detection.
    If tables are found, extracts them with structure.
    Otherwise, falls back to standard OCR.
    
    Args:
        image: PIL Image of the page
        fallback_to_full_ocr: If no tables found, do full-page OCR
    
    Returns:
        Extracted text (pipe-delimited for tables)
    """
    import pytesseract
    
    _configure_tesseract()
    
    # Fix orientation first
    image = _fast_orientation_fix(image)
    img_array = np.array(image)
    
    # Try to detect tables
    tables = detect_table_structure(img_array)
    
    if tables:
        logger.info(f"TableOCR: Found {len(tables)} table(s), extracting with structure")
        result = extract_table_with_structure(image)
        return result["text"]
    
    if fallback_to_full_ocr:
        # No tables found - do standard OCR
        logger.debug("TableOCR: No tables detected, using standard OCR")
        text = pytesseract.image_to_string(
            image,
            lang="fra+eng",
            config="--oem 1 --psm 1"
        )
        return text.strip()
    
    return ""


def ocr_pages_with_table_extraction(
    file_bytes: io.BytesIO,
    page_numbers: List[int],
    dpi: int = 200,
    timeout_per_page: float = 8.0
) -> Dict[int, str]:
    """
    OCR specific pages of a PDF with table structure detection.
    Replacement for PaddleOCR's ocr_pages_with_paddle function.
    
    Args:
        file_bytes: PDF content as BytesIO
        page_numbers: List of page numbers to OCR (1-indexed)
        dpi: Resolution for conversion
        timeout_per_page: Max seconds per page before timeout
    
    Returns:
        Dict mapping page_number -> extracted_text
    """
    from pdf2image import convert_from_bytes
    
    poppler_path = _get_poppler_path()
    results = {}
    
    try:
        file_bytes.seek(0)
        pdf_bytes = file_bytes.read()
        
        for page_num in page_numbers:
            logger.info(f"TableOCR processing page {page_num} at {dpi} DPI...")
            
            try:
                # Convert page to image
                images = convert_from_bytes(
                    pdf_bytes,
                    dpi=dpi,
                    first_page=page_num,
                    last_page=page_num,
                    poppler_path=poppler_path,
                    fmt="jpeg",
                    thread_count=2
                )
                
                if not images:
                    results[page_num] = f"[Page {page_num} conversion failed]"
                    continue
                
                image = images[0]
                
                # Run OCR with timeout
                def _run_ocr():
                    return ocr_page_with_table_detection(image)
                
                with ThreadPoolExecutor(max_workers=1) as ex:
                    future = ex.submit(_run_ocr)
                    try:
                        text = future.result(timeout=timeout_per_page)
                    except FuturesTimeout:
                        results[page_num] = f"[OCR TIMEOUT on page {page_num}: >{timeout_per_page:.0f}s]"
                        logger.warning(f"TableOCR timeout on page {page_num}")
                        continue
                
                if text:
                    logger.info(f"TableOCR page {page_num}: extracted {len(text)} chars")
                    results[page_num] = text
                else:
                    results[page_num] = f"[Page {page_num}: No text detected]"
                    
            except Exception as e:
                logger.error(f"TableOCR failed for page {page_num}: {e}")
                results[page_num] = f"[OCR ERROR on page {page_num}: {str(e)}]"
        
        return results
        
    except Exception as e:
        logger.error(f"TableOCR processing failed: {e}")
        return {p: f"[OCR FAILED: {str(e)}]" for p in page_numbers}


def detect_bordereau_pages(full_text: str) -> List[int]:
    """
    Detect which pages likely contain Bordereau des Prix tables.
    Same logic as the original paddle_ocr module.
    
    Args:
        full_text: Full OCR text with page markers (--- Page X ---)
    
    Returns:
        List of page numbers containing bordereau indicators
    """
    import re
    
    bordereau_indicators = [
        r"bordereau\s+des\s+prix",
        r"bordereau\s+prix",
        r"detail[- ]estimatif",
        r"détail[- ]estimatif",
        r"b\.?p\.?d\.?e",
        r"n[°o]\s*prix",
        r"numéro\s+prix",
        r"désignation\s+des\s+prestations",
        r"prix\s+unitaire",
        r"montant\s+total",
        r"quantit[ée]",
        r"unit[ée]",
    ]
    
    pattern = "|".join(bordereau_indicators)
    
    # Split by page markers
    pages = re.split(r'---\s*Page\s+(\d+)\s*---', full_text)
    
    detected_pages = []
    
    for i in range(1, len(pages), 2):
        if i + 1 < len(pages):
            page_num = int(pages[i])
            page_content = pages[i + 1].lower()
            
            matches = len(re.findall(pattern, page_content, re.IGNORECASE))
            has_table_structure = page_content.count('|') > 5 or bool(re.search(r'\d+\s+\d+', page_content))
            
            if matches >= 2 or (matches >= 1 and has_table_structure):
                detected_pages.append(page_num)
                logger.debug(f"Bordereau indicators found on page {page_num}: {matches} matches")
    
    return detected_pages


def reocr_bordereau_pages(
    file_bytes: io.BytesIO,
    original_text: str,
    force_pages: Optional[List[int]] = None
) -> str:
    """
    Re-OCR bordereau pages with table extraction and merge back into original text.
    Drop-in replacement for paddle_ocr.reocr_bordereau_pages.
    
    Args:
        file_bytes: Original PDF bytes
        original_text: Full text from initial Tesseract OCR
        force_pages: Optional list of specific pages to re-OCR
    
    Returns:
        Updated text with bordereau pages re-OCR'd using table extraction
    """
    import re
    
    # Detect bordereau pages if not specified
    if force_pages:
        bordereau_pages = force_pages
    else:
        bordereau_pages = detect_bordereau_pages(original_text)
    
    if not bordereau_pages:
        logger.info("No bordereau pages detected, keeping original OCR")
        return original_text
    
    logger.info(f"🔄 Re-OCRing {len(bordereau_pages)} bordereau pages with TableOCR: {bordereau_pages}")
    
    # OCR the bordereau pages with table extraction
    table_results = ocr_pages_with_table_extraction(file_bytes, bordereau_pages)
    
    # Replace those pages in the original text
    result_text = original_text
    
    for page_num, table_text in table_results.items():
        if "[OCR" in table_text or "[Page" in table_text:
            logger.warning(f"TableOCR failed for page {page_num}, keeping Tesseract result")
            continue
        
        # Find and replace the page content
        pattern = rf'(---\s*Page\s+{page_num}\s*---\n)(.*?)(?=---\s*Page\s+\d+\s*---|$)'
        
        def replace_page(match):
            marker = match.group(1)
            return f"{marker}{table_text}\n\n"
        
        new_text = re.sub(pattern, replace_page, result_text, flags=re.DOTALL)
        
        if new_text != result_text:
            logger.info(f"✅ Page {page_num} re-OCR'd with TableOCR: {len(table_text)} chars")
            result_text = new_text
    
    return result_text
