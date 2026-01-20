"""
Tender AI Platform - Optimized Tesseract OCR
High-performance OCR with multiprocessing, page splitting, and image optimization.
"""

import io
import os
from typing import Tuple, List, Optional
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from functools import partial
from loguru import logger

# Detect OS and set paths
import platform
IS_WINDOWS = platform.system() == "Windows"

# Paths for Windows
TESSERACT_PATH_WIN = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
POPPLER_PATH_WIN = r"C:\poppler-24.08.0\Library\bin"


def _get_optimal_workers() -> int:
    """Get optimal number of workers based on CPU cores."""
    cpu_count = os.cpu_count() or 4
    # Leave 1-2 cores free for system
    return max(2, cpu_count - 2)


def _configure_tesseract():
    """Configure Tesseract path based on OS."""
    import pytesseract
    if IS_WINDOWS and os.path.exists(TESSERACT_PATH_WIN):
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH_WIN


def _get_poppler_path() -> Optional[str]:
    """Get Poppler path based on OS."""
    if IS_WINDOWS and os.path.exists(POPPLER_PATH_WIN):
        return POPPLER_PATH_WIN
    return None  # Linux uses system PATH


def _detect_and_fix_orientation(image):
    """
    Detect page orientation using Tesseract OSD and rotate if needed.
    Handles landscape scans that appear sideways.
    
    Returns rotated image if rotation was needed, otherwise original.
    """
    import pytesseract
    from PIL import Image
    
    _configure_tesseract()
    
    try:
        # Use OSD (Orientation and Script Detection) to detect rotation
        # This requires the osd traineddata file
        osd_data = pytesseract.image_to_osd(image, output_type=pytesseract.Output.DICT)
        
        rotation_angle = osd_data.get('rotate', 0)
        orientation_conf = osd_data.get('orientation_conf', 0)
        
        logger.debug(f"OSD detected rotation: {rotation_angle}° (confidence: {orientation_conf})")
        
        # Only rotate if confidence is reasonable and rotation is needed
        if rotation_angle != 0 and orientation_conf > 1.0:
            logger.info(f"Auto-rotating image by {rotation_angle}° (confidence: {orientation_conf:.1f})")
            # PIL rotates counter-clockwise, so we negate for clockwise correction
            image = image.rotate(-rotation_angle, expand=True)
        
        return image
        
    except Exception as e:
        # OSD can fail on some images - just continue without rotation
        logger.debug(f"Orientation detection failed (non-critical): {e}")
        return image


def _optimize_image_for_ocr(image, target_dpi: int = 300, detect_orientation: bool = True):
    """
    Optimize image for OCR:
    - Detect and fix orientation (landscape scans)
    - Convert to grayscale
    - Increase contrast
    - Apply thresholding for better text recognition
    """
    from PIL import Image, ImageEnhance, ImageFilter
    
    # First, detect and fix orientation (important for landscape scans)
    if detect_orientation:
        image = _detect_and_fix_orientation(image)
    
    # Convert to grayscale if not already
    if image.mode != 'L':
        image = image.convert('L')
    
    # Enhance contrast
    enhancer = ImageEnhance.Contrast(image)
    image = enhancer.enhance(1.5)
    
    # Apply slight sharpening
    image = image.filter(ImageFilter.SHARPEN)
    
    return image


def _ocr_single_page(args: Tuple[int, bytes]) -> Tuple[int, str]:
    """
    OCR a single page image (designed for multiprocessing).
    Args is a tuple: (page_number, image_bytes)
    Returns: (page_number, extracted_text)
    """
    import pytesseract
    from PIL import Image
    
    page_num, img_bytes = args
    
    _configure_tesseract()
    
    try:
        # Load image from bytes
        img = Image.open(io.BytesIO(img_bytes))
        
        # Optimize for OCR
        img = _optimize_image_for_ocr(img)
        
        # OCR with optimized config
        # --oem 1: LSTM neural net (faster and often better than oem 3)
        # --psm 1: Automatic page segmentation with OSD (handles multi-column)
        # -c preserve_interword_spaces=1: Better word spacing
        text = pytesseract.image_to_string(
            img,
            lang="fra+ara+eng",
            config="--oem 1 --psm 1 -c preserve_interword_spaces=1"
        )
        
        return (page_num, text.strip())
        
    except Exception as e:
        logger.error(f"OCR failed for page {page_num}: {e}")
        return (page_num, f"[OCR ERROR on page {page_num}]")


def _convert_page_to_bytes(page_image) -> bytes:
    """Convert PIL Image to bytes for multiprocessing."""
    buf = io.BytesIO()
    page_image.save(buf, format='PNG', optimize=True)
    return buf.getvalue()


def ocr_first_page_tesseract(file_bytes: io.BytesIO) -> str:
    """
    OCR only the first page of a scanned PDF using optimized Tesseract.
    """
    import pytesseract
    from pdf2image import convert_from_bytes
    
    _configure_tesseract()
    poppler_path = _get_poppler_path()
    
    try:
        file_bytes.seek(0)
        pdf_bytes = file_bytes.read()
        
        # Convert first page only at higher DPI for accuracy
        logger.info("Converting first page to image for OCR...")
        images = convert_from_bytes(
            pdf_bytes,
            dpi=250,  # Higher DPI for first page (classification needs accuracy)
            first_page=1,
            last_page=1,
            poppler_path=poppler_path,
            fmt='png'
        )
        
        if not images:
            logger.error("Could not convert PDF first page to image")
            return ""
        
        # Optimize and OCR
        img = _optimize_image_for_ocr(images[0])
        
        logger.info("Running Tesseract OCR on first page...")
        text = pytesseract.image_to_string(
            img,
            lang="fra+ara+eng",
            config="--oem 1 --psm 1 -c preserve_interword_spaces=1"
        )
        
        logger.info(f"First page OCR extracted {len(text)} chars")
        return text.strip()
        
    except Exception as e:
        logger.error(f"First-page Tesseract OCR failed: {e}")
        return ""


def ocr_full_pdf_tesseract_parallel(
    file_bytes: io.BytesIO,
    max_workers: Optional[int] = None,
    dpi: int = 200,
    batch_size: int = 5
) -> Tuple[str, int]:
    """
    Full OCR extraction with parallel processing.
    
    Strategy:
    1. Convert PDF pages to images in batches (memory efficient)
    2. Process pages in parallel using multiprocessing
    3. Combine results maintaining page order
    
    Args:
        file_bytes: PDF content
        max_workers: Number of parallel workers (auto-detected if None)
        dpi: Resolution for page conversion (lower = faster, higher = accurate)
        batch_size: Pages to convert at once (memory management)
    
    Returns:
        (full_text, page_count)
    """
    from pdf2image import convert_from_bytes, pdfinfo_from_bytes
    
    _configure_tesseract()
    poppler_path = _get_poppler_path()
    
    if max_workers is None:
        max_workers = _get_optimal_workers()
    
    try:
        file_bytes.seek(0)
        pdf_bytes = file_bytes.read()
        
        # Get page count first
        logger.info("Analyzing PDF structure...")
        try:
            pdf_info = pdfinfo_from_bytes(pdf_bytes, poppler_path=poppler_path)
            total_pages = pdf_info.get("Pages", 0)
        except Exception:
            # Fallback: convert all and count
            total_pages = None
        
        if total_pages:
            logger.info(f"PDF has {total_pages} pages, using {max_workers} workers")
        
        # Convert and OCR in batches
        all_results = {}
        page_num = 1
        
        while True:
            # Convert batch of pages
            end_page = page_num + batch_size - 1
            if total_pages:
                end_page = min(end_page, total_pages)
            
            logger.info(f"Converting pages {page_num}-{end_page}...")
            
            try:
                images = convert_from_bytes(
                    pdf_bytes,
                    dpi=dpi,
                    first_page=page_num,
                    last_page=end_page,
                    poppler_path=poppler_path,
                    fmt='png',
                    thread_count=2  # Light threading for conversion
                )
            except Exception as e:
                if "Invalid" in str(e) or page_num > 1:
                    # Likely past end of document
                    break
                raise
            
            if not images:
                break
            
            # Prepare args for parallel processing
            # Convert images to bytes for pickling in multiprocessing
            page_args = []
            for i, img in enumerate(images):
                current_page = page_num + i
                img_bytes = _convert_page_to_bytes(img)
                page_args.append((current_page, img_bytes))
            
            # Process batch in parallel using ThreadPoolExecutor
            # (ProcessPoolExecutor has overhead, threads work well for I/O-bound pytesseract)
            logger.info(f"OCR processing {len(page_args)} pages in parallel...")
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(_ocr_single_page, args): args[0]
                    for args in page_args
                }
                
                for future in as_completed(futures):
                    pnum = futures[future]
                    try:
                        result_page, result_text = future.result()
                        all_results[result_page] = result_text
                        logger.debug(f"Page {result_page} OCR complete: {len(result_text)} chars")
                    except Exception as e:
                        logger.error(f"Page {pnum} OCR failed: {e}")
                        all_results[pnum] = f"[OCR ERROR on page {pnum}]"
            
            page_num = end_page + 1
            
            if total_pages and page_num > total_pages:
                break
        
        # Combine results in page order
        final_page_count = len(all_results)
        sorted_pages = sorted(all_results.keys())
        
        text_parts = []
        for pnum in sorted_pages:
            text_parts.append(f"--- Page {pnum} ---\n{all_results[pnum]}")
        
        full_text = "\n\n".join(text_parts)
        logger.success(f"OCR completed: {final_page_count} pages, {len(full_text)} chars")
        
        return full_text, final_page_count
        
    except Exception as e:
        logger.error(f"Full parallel OCR failed: {e}")
        return f"[OCR FAILED: {str(e)}]", 0


def ocr_full_pdf_tesseract_fast(file_bytes: io.BytesIO) -> Tuple[str, int]:
    """
    Fast OCR mode - lower DPI, more workers.
    Good for large documents where speed matters more than perfect accuracy.
    """
    return ocr_full_pdf_tesseract_parallel(
        file_bytes,
        dpi=150,  # Lower DPI = faster
        batch_size=8  # Larger batches
    )


def ocr_full_pdf_tesseract_accurate(file_bytes: io.BytesIO) -> Tuple[str, int]:
    """
    Accurate OCR mode - higher DPI, optimized processing.
    Good for important documents or poor quality scans.
    """
    return ocr_full_pdf_tesseract_parallel(
        file_bytes,
        dpi=300,  # Higher DPI = more accurate
        batch_size=3  # Smaller batches to manage memory
    )
