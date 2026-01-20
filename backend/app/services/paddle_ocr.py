"""
Tender AI Platform - PaddleOCR Service
High-accuracy OCR specifically for Bordereau des Prix tables in scanned documents.
Uses PaddleOCR which excels at table structure recognition.
"""

import io
import os
import threading
from typing import Tuple, List, Optional
from loguru import logger
from PIL import Image

# Detect OS and set paths
import platform
IS_WINDOWS = platform.system() == "Windows"

# Paths for Windows Poppler
POPPLER_PATH_WIN = r"C:\poppler-24.08.0\Library\bin"


def _get_poppler_path() -> Optional[str]:
    """Get Poppler path based on OS."""
    if IS_WINDOWS and os.path.exists(POPPLER_PATH_WIN):
        return POPPLER_PATH_WIN
    return None


def _detect_and_fix_orientation(image: Image.Image) -> Image.Image:
    """
    Fast orientation handling.

    We avoid Tesseract OSD here because it's *very* slow and can turn OCR into minutes/page.
    Instead we apply a cheap heuristic: if the page is clearly landscape, rotate 90°.

    You can re-enable the slower-but-smarter OSD by setting:
      ENABLE_OCR_OSD=true
    """
    try:
        w, h = image.size
        if w > h * 1.20:
            logger.info("PaddleOCR: Fast-rotating landscape page by 90°")
            return image.rotate(-90, expand=True)

        if os.getenv("ENABLE_OCR_OSD", "false").lower() in {"1", "true", "yes"}:
            import pytesseract

            # Configure Tesseract path for Windows
            if IS_WINDOWS:
                tesseract_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
                if os.path.exists(tesseract_path):
                    pytesseract.pytesseract.tesseract_cmd = tesseract_path

            osd_data = pytesseract.image_to_osd(image, output_type=pytesseract.Output.DICT)
            rotation_angle = int(osd_data.get("rotate", 0) or 0)
            orientation_conf = float(osd_data.get("orientation_conf", 0) or 0)

            # Use a higher confidence threshold to avoid bad rotations
            if rotation_angle != 0 and orientation_conf >= 10.0:
                logger.info(
                    f"PaddleOCR: OSD rotating image by {rotation_angle}° (confidence: {orientation_conf:.1f})"
                )
                return image.rotate(-rotation_angle, expand=True)

        return image
    except Exception as e:
        logger.debug(f"Orientation detection skipped: {e}")
        return image


_PADDLE_OCR_INSTANCE = None
_PADDLE_OCR_LOCK = threading.Lock()


def _initialize_paddle_ocr():
    """Initialize PaddleOCR once and reuse it (model init is expensive)."""
    global _PADDLE_OCR_INSTANCE

    if _PADDLE_OCR_INSTANCE is not None:
        return _PADDLE_OCR_INSTANCE

    with _PADDLE_OCR_LOCK:
        if _PADDLE_OCR_INSTANCE is not None:
            return _PADDLE_OCR_INSTANCE

        try:
            # Disable model source check to speed up initialization
            os.environ["DISABLE_MODEL_SOURCE_CHECK"] = "True"

            from paddleocr import PaddleOCR

            # NOTE: PaddleOCR versions vary a lot; keep constructor args minimal.
            _PADDLE_OCR_INSTANCE = PaddleOCR(
                use_angle_cls=True,  # angle classification handled at init (more compatible)
                lang="fr",
                det_db_thresh=0.3,
                det_db_box_thresh=0.5,
                rec_batch_num=6,
            )
            return _PADDLE_OCR_INSTANCE
        except ImportError:
            logger.error("PaddleOCR not installed. Install with: pip install paddlepaddle paddleocr")
            return None
        except Exception as e:
            logger.error(f"Failed to initialize PaddleOCR: {e}")
            return None


def _format_ocr_result_as_table(result: list) -> str:
    """
    Format PaddleOCR result to preserve table structure.
    PaddleOCR returns: [[[box_coords], (text, confidence)], ...]
    
    Groups text by approximate Y position to reconstruct table rows.
    """
    if not result or not result[0]:
        return ""
    
    # Extract all text boxes with their positions
    boxes = []
    for line in result[0]:
        if len(line) >= 2:
            box_coords = line[0]  # [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
            text_conf = line[1]   # (text, confidence)
            
            if isinstance(text_conf, tuple) and len(text_conf) >= 1:
                text = str(text_conf[0])
                # Get average Y position for row grouping
                y_positions = [coord[1] for coord in box_coords]
                avg_y = sum(y_positions) / len(y_positions)
                # Get X position for column ordering
                x_positions = [coord[0] for coord in box_coords]
                avg_x = sum(x_positions) / len(x_positions)
                
                boxes.append({
                    'text': text,
                    'y': avg_y,
                    'x': avg_x
                })
    
    if not boxes:
        return ""
    
    # Sort by Y position (top to bottom)
    boxes.sort(key=lambda b: b['y'])
    
    # Group into rows (boxes within ~20px Y are same row)
    rows = []
    current_row = []
    last_y = None
    row_threshold = 20  # pixels
    
    for box in boxes:
        if last_y is None or abs(box['y'] - last_y) < row_threshold:
            current_row.append(box)
        else:
            if current_row:
                # Sort row by X position (left to right)
                current_row.sort(key=lambda b: b['x'])
                rows.append(current_row)
            current_row = [box]
        last_y = box['y']
    
    if current_row:
        current_row.sort(key=lambda b: b['x'])
        rows.append(current_row)
    
    # Format as text with | separators for tables
    lines = []
    for row in rows:
        row_text = " | ".join(b['text'] for b in row)
        lines.append(row_text)
    
    return "\n".join(lines)


def ocr_pages_with_paddle(
    file_bytes: io.BytesIO,
    page_numbers: List[int],
    dpi: int = 200,
) -> dict:
    """
    OCR specific pages of a PDF using PaddleOCR.

    Performance notes:
    - Paddle model init is cached (see _initialize_paddle_ocr).
    - Default DPI is 200 (300 DPI is often 2-5x slower).
    - Orientation OSD is disabled by default (see _detect_and_fix_orientation).
    - A soft timeout is applied to the Paddle call to avoid hanging forever.

    Returns: Dict mapping page_number -> extracted_text
    """
    from pdf2image import convert_from_bytes
    import numpy as np
    from concurrent.futures import ThreadPoolExecutor, TimeoutError

    ocr = _initialize_paddle_ocr()
    if not ocr:
        return {p: "[PADDLEOCR NOT AVAILABLE]" for p in page_numbers}

    poppler_path = _get_poppler_path()
    results = {}

    # Soft timeout: if the OCR call is too slow we return an error for that page.
    # (Python can't reliably kill the underlying native work, but this prevents the whole pipeline from blocking.)
    per_page_timeout_s = float(os.getenv("PADDLE_OCR_PAGE_TIMEOUT_SECONDS", "8"))

    try:
        file_bytes.seek(0)
        pdf_bytes = file_bytes.read()

        for page_num in page_numbers:
            logger.info(f"PaddleOCR processing page {page_num} at {dpi} DPI...")

            try:
                # Convert specific page to image
                images = convert_from_bytes(
                    pdf_bytes,
                    dpi=dpi,
                    first_page=page_num,
                    last_page=page_num,
                    poppler_path=poppler_path,
                    fmt="jpeg",  # faster than png in most cases
                    thread_count=2,
                )

                if not images:
                    results[page_num] = f"[Page {page_num} conversion failed]"
                    continue

                image = _detect_and_fix_orientation(images[0])
                img_array = np.array(image)

                def _run_paddle():
                    # Run OCR (API varies between PaddleOCR versions)
                    try:
                        return ocr.ocr(img_array, cls=True)
                    except TypeError:
                        return ocr.ocr(img_array)

                with ThreadPoolExecutor(max_workers=1) as ex:
                    future = ex.submit(_run_paddle)
                    try:
                        ocr_result = future.result(timeout=per_page_timeout_s)
                    except TimeoutError:
                        results[page_num] = f"[OCR TIMEOUT on page {page_num}: >{per_page_timeout_s:.0f}s]"
                        logger.warning(f"PaddleOCR timeout on page {page_num} (>{per_page_timeout_s:.0f}s)")
                        continue

                text = _format_ocr_result_as_table(ocr_result)

                if text:
                    logger.info(f"PaddleOCR page {page_num}: extracted {len(text)} chars")
                    results[page_num] = text
                else:
                    results[page_num] = f"[Page {page_num}: No text detected]"

            except Exception as e:
                logger.error(f"PaddleOCR failed for page {page_num}: {e}")
                results[page_num] = f"[OCR ERROR on page {page_num}: {str(e)}]"

        return results

    except Exception as e:
        logger.error(f"PaddleOCR processing failed: {e}")
        return {p: f"[OCR FAILED: {str(e)}]" for p in page_numbers}


def detect_bordereau_pages(full_text: str) -> List[int]:
    """
    Detect which pages likely contain Bordereau des Prix tables.
    
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
    
    # pages[0] is before first marker (usually empty)
    # pages[1] = page number, pages[2] = content, etc.
    for i in range(1, len(pages), 2):
        if i + 1 < len(pages):
            page_num = int(pages[i])
            page_content = pages[i + 1].lower()
            
            # Check for bordereau indicators
            matches = len(re.findall(pattern, page_content, re.IGNORECASE))
            
            # Also check for table-like structure (multiple | or columns)
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
    Re-OCR bordereau pages with PaddleOCR and merge back into original text.
    
    Args:
        file_bytes: Original PDF bytes
        original_text: Full text from Tesseract OCR
        force_pages: Optional list of specific pages to re-OCR
    
    Returns:
        Updated text with bordereau pages re-OCR'd using PaddleOCR
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
    
    logger.info(f"🔄 Re-OCRing {len(bordereau_pages)} bordereau pages with PaddleOCR: {bordereau_pages}")
    
    # OCR the bordereau pages with PaddleOCR
    # Use faster defaults (200 DPI) unless explicitly overridden.
    paddle_results = ocr_pages_with_paddle(file_bytes, bordereau_pages)

    
    # Replace those pages in the original text
    result_text = original_text
    
    for page_num, paddle_text in paddle_results.items():
        if "[OCR" in paddle_text or "[Page" in paddle_text:
            # PaddleOCR failed, keep original
            logger.warning(f"PaddleOCR failed for page {page_num}, keeping Tesseract result")
            continue
        
        # Find and replace the page content
        pattern = rf'(---\s*Page\s+{page_num}\s*---\n)(.*?)(?=---\s*Page\s+\d+\s*---|$)'
        
        def replace_page(match):
            marker = match.group(1)
            return f"{marker}{paddle_text}\n\n"
        
        new_text = re.sub(pattern, replace_page, result_text, flags=re.DOTALL)
        
        if new_text != result_text:
            logger.info(f"✅ Page {page_num} re-OCR'd with PaddleOCR: {len(paddle_text)} chars")
            result_text = new_text
    
    return result_text
