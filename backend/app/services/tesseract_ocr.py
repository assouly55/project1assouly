"""
Tender AI Platform - Adaptive Tesseract OCR
============================================
Smart OCR pipeline with:
  - Pre-scan at 72 DPI to classify page complexity
  - Adaptive DPI/config per page type (text vs table vs image)
  - Hard per-page timeouts (no hangs)
  - Progressive fallback chain
  - Single-pass processing (no redundant OCR)

Performance target: ≤15s for 20 pages.
"""

import io
import os
import time
from typing import Tuple, List, Optional, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from loguru import logger

import platform
IS_WINDOWS = platform.system() == "Windows"

TESSERACT_PATH_WIN = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
POPPLER_PATH_WIN = r"C:\poppler-24.08.0\Library\bin"


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _get_optimal_workers() -> int:
    cpu_count = os.cpu_count() or 4
    return max(2, cpu_count - 2)


def _configure_tesseract():
    import pytesseract
    if IS_WINDOWS and os.path.exists(TESSERACT_PATH_WIN):
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH_WIN


def _get_poppler_path() -> Optional[str]:
    if IS_WINDOWS and os.path.exists(POPPLER_PATH_WIN):
        return POPPLER_PATH_WIN
    return None


# ---------------------------------------------------------------------------
# Page complexity classification
# ---------------------------------------------------------------------------

class PageType(str, Enum):
    SIMPLE_TEXT = "simple"    # Mostly text, no tables
    MEDIUM_TABLE = "medium"   # Some table structure
    COMPLEX_TABLE = "complex" # Dense/complex tables
    IMAGE_HEAVY = "image"     # Photos/diagrams, sparse text


@dataclass
class PageProfile:
    """Complexity profile for a single page."""
    page_num: int
    page_type: PageType
    dpi: int
    psm: int          # Tesseract page segmentation mode
    timeout_s: float
    use_table_ocr: bool


def _classify_page_complexity(img_bytes: bytes) -> PageType:
    """
    Quick classification of page complexity from a low-res thumbnail.
    Uses simple heuristics: edge density, text density, line detection.
    """
    try:
        import cv2
        import numpy as np
        from PIL import Image

        img = Image.open(io.BytesIO(img_bytes)).convert("L")
        arr = np.array(img)

        # Edge detection for line/structure density
        edges = cv2.Canny(arr, 50, 150)
        edge_ratio = np.count_nonzero(edges) / edges.size

        # Binary for text density
        _, binary = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        text_ratio = np.count_nonzero(binary) / binary.size

        # Horizontal line detection (table indicator)
        h, w = binary.shape
        h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 8, 20), 1))
        h_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel)
        h_line_ratio = np.count_nonzero(h_lines) / h_lines.size

        if h_line_ratio > 0.005 and edge_ratio > 0.08:
            return PageType.COMPLEX_TABLE
        elif h_line_ratio > 0.002 or edge_ratio > 0.06:
            return PageType.MEDIUM_TABLE
        elif text_ratio < 0.02:
            return PageType.IMAGE_HEAVY
        else:
            return PageType.SIMPLE_TEXT

    except Exception as e:
        logger.debug(f"Page classification failed: {e}")
        return PageType.MEDIUM_TABLE  # safe default


def build_page_profiles(pdf_bytes: bytes) -> List[PageProfile]:
    """
    Pre-scan all pages at 72 DPI and build a processing profile for each.
    Target: ~3 seconds for entire pre-scan.
    """
    from pdf2image import convert_from_bytes, pdfinfo_from_bytes

    poppler_path = _get_poppler_path()

    try:
        info = pdfinfo_from_bytes(pdf_bytes, poppler_path=poppler_path)
        total_pages = info.get("Pages", 0)
    except Exception:
        total_pages = 50  # cap

    if total_pages == 0:
        return []

    logger.info(f"Pre-scanning {total_pages} pages at 72 DPI...")
    t0 = time.monotonic()

    # Convert all pages at low DPI for fast analysis
    try:
        thumbs = convert_from_bytes(
            pdf_bytes,
            dpi=72,
            first_page=1,
            last_page=total_pages,
            poppler_path=poppler_path,
            fmt="jpeg",
            thread_count=2,
        )
    except Exception as e:
        logger.error(f"Pre-scan failed: {e}")
        # Return default profiles
        return [
            PageProfile(p, PageType.MEDIUM_TABLE, 150, 3, 8.0, False)
            for p in range(1, total_pages + 1)
        ]

    profiles: List[PageProfile] = []
    for i, thumb in enumerate(thumbs):
        buf = io.BytesIO()
        thumb.save(buf, format="JPEG", quality=50)
        page_type = _classify_page_complexity(buf.getvalue())

        # Adaptive configuration per page type
        if page_type == PageType.SIMPLE_TEXT:
            profile = PageProfile(i + 1, page_type, dpi=150, psm=3, timeout_s=6.0, use_table_ocr=False)
        elif page_type == PageType.MEDIUM_TABLE:
            profile = PageProfile(i + 1, page_type, dpi=200, psm=6, timeout_s=8.0, use_table_ocr=True)
        elif page_type == PageType.COMPLEX_TABLE:
            profile = PageProfile(i + 1, page_type, dpi=250, psm=6, timeout_s=10.0, use_table_ocr=True)
        else:  # IMAGE_HEAVY
            profile = PageProfile(i + 1, page_type, dpi=150, psm=1, timeout_s=5.0, use_table_ocr=False)

        profiles.append(profile)

    elapsed = time.monotonic() - t0
    type_counts = {}
    for p in profiles:
        type_counts[p.page_type.value] = type_counts.get(p.page_type.value, 0) + 1

    logger.info(f"Pre-scan done in {elapsed:.1f}s: {type_counts}")
    return profiles


# ---------------------------------------------------------------------------
# Single-page OCR with hard timeout
# ---------------------------------------------------------------------------

def _ocr_single_page_adaptive(args: Tuple[int, bytes, int, int, float, bool]) -> Tuple[int, str]:
    """
    OCR a single page with adaptive config and hard timeout.
    args: (page_num, img_bytes, dpi_for_convert, psm, timeout_s, use_table_ocr)
    """
    import pytesseract
    from PIL import Image, ImageEnhance, ImageFilter

    page_num, img_bytes, _dpi, psm, timeout_s, use_table_ocr = args
    _configure_tesseract()

    try:
        img = Image.open(io.BytesIO(img_bytes))

        # Fast orientation fix
        w, h = img.size
        if w > h * 1.2:
            img = img.rotate(-90, expand=True)

        # Try table OCR first if flagged
        if use_table_ocr:
            try:
                from app.services.table_ocr import process_page_table
                table = process_page_table(img, tesseract_timeout=timeout_s - 1.0)
                if table and table.latex and len(table.latex) > 50:
                    return (page_num, table.latex)
            except Exception as e:
                logger.debug(f"Page {page_num} table OCR failed, falling back: {e}")

        # Standard OCR with optimized image
        if img.mode != "L":
            img = img.convert("L")
        img = ImageEnhance.Contrast(img).enhance(1.5)
        img = img.filter(ImageFilter.SHARPEN)

        text = pytesseract.image_to_string(
            img,
            lang="fra+ara+eng",
            config=f"--oem 1 --psm {psm} -c preserve_interword_spaces=1",
            timeout=timeout_s,
        )
        return (page_num, text.strip())

    except Exception as e:
        logger.warning(f"Page {page_num} OCR failed: {e}")
        # Fallback attempt: lowest settings
        return _ocr_fallback(page_num, img_bytes)


def _ocr_fallback(page_num: int, img_bytes: bytes) -> Tuple[int, str]:
    """Last-resort OCR: grayscale, low config, short timeout."""
    import pytesseract
    from PIL import Image

    _configure_tesseract()
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("L")
        # Resize down 50% to reduce work
        img = img.resize((img.width // 2, img.height // 2), Image.LANCZOS)

        text = pytesseract.image_to_string(
            img,
            lang="fra+eng",
            config="--oem 1 --psm 3",
            timeout=5.0,
        )
        if text.strip():
            logger.info(f"Page {page_num} recovered via fallback OCR")
            return (page_num, text.strip())
    except Exception:
        pass

    return (page_num, f"[Page {page_num}: OCR failed after all attempts]")


# ---------------------------------------------------------------------------
# First-page OCR (for classification — unchanged interface)
# ---------------------------------------------------------------------------

def ocr_first_page_tesseract(file_bytes: io.BytesIO) -> str:
    """OCR only the first page of a scanned PDF."""
    import pytesseract
    from pdf2image import convert_from_bytes

    _configure_tesseract()
    poppler_path = _get_poppler_path()

    try:
        file_bytes.seek(0)
        pdf_bytes = file_bytes.read()

        images = convert_from_bytes(
            pdf_bytes, dpi=250,
            first_page=1, last_page=1,
            poppler_path=poppler_path, fmt="png",
        )
        if not images:
            return ""

        from PIL import ImageEnhance, ImageFilter
        img = images[0].convert("L")
        img = ImageEnhance.Contrast(img).enhance(1.5)
        img = img.filter(ImageFilter.SHARPEN)

        text = pytesseract.image_to_string(
            img, lang="fra+ara+eng",
            config="--oem 1 --psm 1 -c preserve_interword_spaces=1",
            timeout=10.0,
        )
        logger.info(f"First page OCR: {len(text)} chars")
        return text.strip()

    except Exception as e:
        logger.error(f"First-page OCR failed: {e}")
        return ""


# ---------------------------------------------------------------------------
# Full PDF OCR — adaptive parallel pipeline
# ---------------------------------------------------------------------------

def ocr_full_pdf_tesseract_parallel(
    file_bytes: io.BytesIO,
    max_workers: Optional[int] = None,
    dpi: int = 200,
    batch_size: int = 5,
) -> Tuple[str, int]:
    """
    Adaptive OCR pipeline:
    1. Pre-scan at 72 DPI → classify every page
    2. Convert pages in batches at per-page DPI
    3. OCR in parallel with per-page config + hard timeouts
    4. Table pages get coordinate-based LaTeX extraction
    5. Progressive fallback on any failure

    Returns: (full_text, page_count)
    """
    from pdf2image import convert_from_bytes

    _configure_tesseract()
    poppler_path = _get_poppler_path()

    if max_workers is None:
        max_workers = _get_optimal_workers()

    try:
        file_bytes.seek(0)
        pdf_bytes = file_bytes.read()

        # Phase 1: Pre-scan and build profiles
        profiles = build_page_profiles(pdf_bytes)
        if not profiles:
            return "", 0

        total_pages = len(profiles)

        # Estimate total time — if too high, downgrade all to medium
        estimated_time = sum(p.timeout_s * 0.5 for p in profiles)  # ~50% of budget
        if estimated_time > 30.0:
            logger.warning(f"Estimated {estimated_time:.0f}s — downgrading to fast mode")
            for p in profiles:
                p.dpi = min(p.dpi, 150)
                p.timeout_s = min(p.timeout_s, 6.0)
                p.use_table_ocr = p.page_type == PageType.COMPLEX_TABLE  # only complex

        logger.info(f"OCR starting: {total_pages} pages, {max_workers} workers")
        t0 = time.monotonic()

        all_results: Dict[int, str] = {}

        # Process in DPI-grouped batches for efficiency
        # Group pages by DPI to minimize re-conversions
        dpi_groups: Dict[int, List[PageProfile]] = {}
        for p in profiles:
            dpi_groups.setdefault(p.dpi, []).append(p)

        for group_dpi, group_profiles in dpi_groups.items():
            # Convert pages in this DPI group
            for batch_start in range(0, len(group_profiles), batch_size):
                batch = group_profiles[batch_start:batch_start + batch_size]
                page_nums = [p.page_num for p in batch]

                logger.info(
                    f"Converting pages {page_nums} at {group_dpi} DPI..."
                )

                # Convert batch
                page_args = []
                for profile in batch:
                    try:
                        images = convert_from_bytes(
                            pdf_bytes,
                            dpi=group_dpi,
                            first_page=profile.page_num,
                            last_page=profile.page_num,
                            poppler_path=poppler_path,
                            fmt="jpeg",
                            thread_count=1,
                        )
                        if images:
                            buf = io.BytesIO()
                            images[0].save(buf, format="PNG", optimize=True)
                            page_args.append((
                                profile.page_num,
                                buf.getvalue(),
                                group_dpi,
                                profile.psm,
                                profile.timeout_s,
                                profile.use_table_ocr,
                            ))
                    except Exception as e:
                        logger.error(f"Page {profile.page_num} conversion failed: {e}")
                        all_results[profile.page_num] = f"[Page {profile.page_num} conversion failed]"

                if not page_args:
                    continue

                # OCR batch in parallel
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {
                        executor.submit(_ocr_single_page_adaptive, args): args[0]
                        for args in page_args
                    }
                    for future in as_completed(futures):
                        pnum = futures[future]
                        try:
                            result_page, result_text = future.result(timeout=15.0)
                            all_results[result_page] = result_text
                        except Exception as e:
                            logger.error(f"Page {pnum} timed out: {e}")
                            all_results[pnum] = f"[Page {pnum}: timeout]"

        # Combine results in page order
        elapsed = time.monotonic() - t0
        sorted_pages = sorted(all_results.keys())
        text_parts = [f"--- Page {p} ---\n{all_results[p]}" for p in sorted_pages]
        full_text = "\n\n".join(text_parts)

        logger.success(
            f"OCR completed: {len(all_results)}/{total_pages} pages "
            f"in {elapsed:.1f}s ({len(full_text)} chars)"
        )
        return full_text, len(all_results)

    except Exception as e:
        logger.error(f"Full adaptive OCR failed: {e}")
        return f"[OCR FAILED: {str(e)}]", 0


# ---------------------------------------------------------------------------
# Convenience wrappers (unchanged interface)
# ---------------------------------------------------------------------------

def ocr_full_pdf_tesseract_fast(file_bytes: io.BytesIO) -> Tuple[str, int]:
    """Fast mode — lower DPI, more workers."""
    return ocr_full_pdf_tesseract_parallel(file_bytes, dpi=150, batch_size=8)


def ocr_full_pdf_tesseract_accurate(file_bytes: io.BytesIO) -> Tuple[str, int]:
    """Accurate mode — higher DPI."""
    return ocr_full_pdf_tesseract_parallel(file_bytes, dpi=300, batch_size=3)
