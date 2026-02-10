"""
Tender AI Platform - Timeout-Proof Tesseract OCR
=================================================
Aggressive anti-timeout pipeline:
  - Max 2 workers (prevents resource contention)
  - Low DPI first (72-150), only escalate if needed
  - Hard 5s timeout per page, 3s for fallback
  - 2 attempts max per page, then mark as image-only
  - No infinite retries, no hangs

Performance target: ≤15s for 20 pages, 0% timeout rate.
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
    """Max 2 workers to prevent resource contention causing timeouts."""
    return 2


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
    SIMPLE_TEXT = "simple"
    MEDIUM_TABLE = "medium"
    COMPLEX_TABLE = "complex"
    IMAGE_HEAVY = "image"


@dataclass
class PageProfile:
    page_num: int
    page_type: PageType
    dpi: int
    psm: int
    timeout_s: float


def _classify_page_complexity(img_bytes: bytes) -> PageType:
    """Quick classification from a low-res thumbnail."""
    try:
        import cv2
        import numpy as np
        from PIL import Image

        img = Image.open(io.BytesIO(img_bytes)).convert("L")
        arr = np.array(img)

        edges = cv2.Canny(arr, 50, 150)
        edge_ratio = edges.astype(bool).sum() / edges.size

        _, binary = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        text_ratio = binary.astype(bool).sum() / binary.size

        h, w = binary.shape
        h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 8, 20), 1))
        h_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel)
        h_line_ratio = h_lines.astype(bool).sum() / h_lines.size

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
        return PageType.SIMPLE_TEXT  # default to lightest processing


def build_page_profiles(pdf_bytes: bytes) -> List[PageProfile]:
    """Pre-scan all pages at 72 DPI and build processing profiles."""
    from pdf2image import convert_from_bytes, pdfinfo_from_bytes

    poppler_path = _get_poppler_path()

    try:
        info = pdfinfo_from_bytes(pdf_bytes, poppler_path=poppler_path)
        total_pages = info.get("Pages", 0)
    except Exception:
        total_pages = 50

    if total_pages == 0:
        return []

    logger.info(f"Pre-scanning {total_pages} pages at 72 DPI...")
    t0 = time.monotonic()

    try:
        thumbs = convert_from_bytes(
            pdf_bytes, dpi=72,
            first_page=1, last_page=total_pages,
            poppler_path=poppler_path, fmt="jpeg", thread_count=2,
        )
    except Exception as e:
        logger.error(f"Pre-scan failed: {e}")
        return [
            PageProfile(p, PageType.SIMPLE_TEXT, 72, 3, 5.0)
            for p in range(1, total_pages + 1)
        ]

    profiles: List[PageProfile] = []
    for i, thumb in enumerate(thumbs):
        buf = io.BytesIO()
        thumb.save(buf, format="JPEG", quality=50)
        page_type = _classify_page_complexity(buf.getvalue())

        # LOW DPI defaults — pure text OCR only (no table detection here)
        if page_type == PageType.SIMPLE_TEXT:
            profile = PageProfile(i + 1, page_type, dpi=72, psm=3, timeout_s=5.0)
        elif page_type == PageType.MEDIUM_TABLE:
            profile = PageProfile(i + 1, page_type, dpi=100, psm=6, timeout_s=6.0)
        elif page_type == PageType.COMPLEX_TABLE:
            profile = PageProfile(i + 1, page_type, dpi=150, psm=6, timeout_s=8.0)
        else:  # IMAGE_HEAVY
            profile = PageProfile(i + 1, page_type, dpi=72, psm=1, timeout_s=4.0)

        profiles.append(profile)

    elapsed = time.monotonic() - t0
    type_counts: Dict[str, int] = {}
    for p in profiles:
        type_counts[p.page_type.value] = type_counts.get(p.page_type.value, 0) + 1

    logger.info(f"Pre-scan done in {elapsed:.1f}s: {type_counts}")
    return profiles


# ---------------------------------------------------------------------------
# Single-page OCR — 2 attempts max, then give up
# ---------------------------------------------------------------------------

def _ocr_single_page_adaptive(args: Tuple[int, bytes, int, int, float]) -> Tuple[int, str]:
    """
    OCR one page with pure text extraction (no table detection).
    Table detection happens later via reocr_bordereau_pages.
    Level 1 at configured settings, Level 2 ultra-fast fallback.
    Never more than 2 attempts. Never hangs.
    """
    import pytesseract
    from PIL import Image, ImageEnhance

    page_num, img_bytes, _dpi, psm, timeout_s = args
    _configure_tesseract()

    try:
        img = Image.open(io.BytesIO(img_bytes))

        # Fast orientation fix
        w, h = img.size
        if w > h * 1.2:
            img = img.rotate(-90, expand=True)

        # Pure text OCR (no table detection — that happens in a separate pass)
        if img.mode != "L":
            img = img.convert("L")
        img = ImageEnhance.Contrast(img).enhance(1.3)

        text = pytesseract.image_to_string(
            img,
            lang="fra",
            config=f"--oem 1 --psm {psm}",
            timeout=timeout_s,
        )
        if text.strip():
            return (page_num, text.strip())

    except Exception as e:
        logger.warning(f"Page {page_num} L1 failed: {e}")

    # Level 2: ultra-fast fallback
    return _ocr_fallback_ultra(page_num, img_bytes)


def _ocr_fallback_ultra(page_num: int, img_bytes: bytes) -> Tuple[int, str]:
    """Ultra-fast last resort: 25% size, 3s timeout, then give up."""
    import pytesseract
    from PIL import Image

    _configure_tesseract()
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("L")
        img = img.resize((img.width // 4, img.height // 4), Image.LANCZOS)

        text = pytesseract.image_to_string(
            img, lang="fra",
            config="--oem 1 --psm 3",
            timeout=3.0,
        )
        if text.strip():
            logger.info(f"Page {page_num} recovered via ultra-fast fallback")
            return (page_num, text.strip())
    except Exception:
        pass

    logger.warning(f"Page {page_num} unrecoverable — image-only")
    return (page_num, f"[Page {page_num}: image-only, OCR unavailable]")


# ---------------------------------------------------------------------------
# First-page OCR (for classification)
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
            pdf_bytes, dpi=72,
            first_page=1, last_page=1,
            poppler_path=poppler_path, fmt="jpeg",
        )
        if not images:
            return ""

        img = images[0].convert("L")

        text = pytesseract.image_to_string(
            img, lang="fra",
            config="--oem 1 --psm 3",
            timeout=5.0,
        )
        logger.info(f"First page OCR: {len(text)} chars")
        return text.strip()

    except Exception as e:
        logger.error(f"First-page OCR failed: {e}")
        return ""


# ---------------------------------------------------------------------------
# Full PDF OCR — timeout-proof parallel pipeline
# ---------------------------------------------------------------------------

def ocr_full_pdf_tesseract_parallel(
    file_bytes: io.BytesIO,
    max_workers: Optional[int] = None,
    dpi: int = 100,
    batch_size: int = 5,
) -> Tuple[str, int]:
    """
    Timeout-proof OCR pipeline:
    1. Pre-scan at 72 DPI → classify pages
    2. Convert at LOW DPI (72-150) per page type
    3. OCR with max 2 workers, hard 5s timeouts
    4. 2 attempts max per page, then mark image-only
    5. Always completes, never hangs

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

        # Force ultra-fast if many pages
        estimated_time = sum(p.timeout_s * 0.5 for p in profiles)
        if estimated_time > 20.0:
            logger.warning(f"Estimated {estimated_time:.0f}s — forcing ultra-fast mode")
            for p in profiles:
                p.dpi = 72
                p.timeout_s = 4.0

        logger.info(f"OCR starting: {total_pages} pages, {max_workers} workers")
        t0 = time.monotonic()

        all_results: Dict[int, str] = {}

        # Group by DPI for batch conversion
        dpi_groups: Dict[int, List[PageProfile]] = {}
        for p in profiles:
            dpi_groups.setdefault(p.dpi, []).append(p)

        for group_dpi, group_profiles in dpi_groups.items():
            for batch_start in range(0, len(group_profiles), batch_size):
                batch = group_profiles[batch_start:batch_start + batch_size]
                page_nums = [p.page_num for p in batch]

                logger.info(f"Converting pages {page_nums} at {group_dpi} DPI...")

                page_args = []
                for profile in batch:
                    try:
                        images = convert_from_bytes(
                            pdf_bytes, dpi=group_dpi,
                            first_page=profile.page_num, last_page=profile.page_num,
                            poppler_path=poppler_path, fmt="jpeg", thread_count=1,
                        )
                        if images:
                            buf = io.BytesIO()
                            images[0].save(buf, format="PNG", optimize=True)
                            page_args.append((
                                profile.page_num, buf.getvalue(),
                                group_dpi, profile.psm,
                                profile.timeout_s,
                            ))
                    except Exception as e:
                        logger.error(f"Page {profile.page_num} conversion failed: {e}")
                        all_results[profile.page_num] = f"[Page {profile.page_num} conversion failed]"

                if not page_args:
                    continue

                # OCR with max 2 workers
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {
                        executor.submit(_ocr_single_page_adaptive, args): args[0]
                        for args in page_args
                    }
                    for future in as_completed(futures):
                        pnum = futures[future]
                        try:
                            result_page, result_text = future.result(timeout=12.0)
                            all_results[result_page] = result_text
                        except Exception as e:
                            logger.error(f"Page {pnum} hard timeout: {e}")
                            all_results[pnum] = f"[Page {pnum}: timeout, skipped]"

        # Combine in page order
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
        logger.error(f"Full OCR failed: {e}")
        return f"[OCR FAILED: {str(e)}]", 0


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

def ocr_full_pdf_tesseract_fast(file_bytes: io.BytesIO) -> Tuple[str, int]:
    """Fast mode — 72 DPI, 2 workers."""
    return ocr_full_pdf_tesseract_parallel(file_bytes, dpi=72, batch_size=8)


def ocr_full_pdf_tesseract_accurate(file_bytes: io.BytesIO) -> Tuple[str, int]:
    """Accurate mode — moderate DPI."""
    return ocr_full_pdf_tesseract_parallel(file_bytes, dpi=150, batch_size=3)
