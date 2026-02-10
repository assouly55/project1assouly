"""
Tender AI Platform — Coordinate-Based Table OCR
================================================
Converts scanned PDF tables into structured LaTeX using a 4-phase pipeline:

  Phase 1 — Shape Detection   : OpenCV finds lines/borders → virtual grid
  Phase 2 — Text Extraction   : Tesseract extracts words with bounding boxes
  Phase 3 — Coordinate Merge  : Assigns each word to the correct grid cell
  Phase 4 — LaTeX Output      : Deterministic, AI-parseable table format

Performance target: < 5 s per page.
Tools: OpenCV (free), Tesseract (free), no paid APIs.
"""

import io
import os
import re
import time
import platform
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, field
from loguru import logger

import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# Platform paths
# ---------------------------------------------------------------------------
IS_WINDOWS = platform.system() == "Windows"
TESSERACT_PATH_WIN = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
POPPLER_PATH_WIN = r"C:\poppler-24.08.0\Library\bin"


def _get_poppler_path() -> Optional[str]:
    if IS_WINDOWS and os.path.exists(POPPLER_PATH_WIN):
        return POPPLER_PATH_WIN
    return None


def _configure_tesseract():
    import pytesseract
    if IS_WINDOWS and os.path.exists(TESSERACT_PATH_WIN):
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH_WIN


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------
@dataclass
class GridLine:
    """A detected horizontal or vertical line segment."""
    x1: int
    y1: int
    x2: int
    y2: int
    orientation: str  # "H" or "V"


@dataclass
class GridCell:
    """A cell in the virtual grid with its bounding box."""
    row: int
    col: int
    x: int
    y: int
    w: int
    h: int
    words: List[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(self.words)


@dataclass
class WordBox:
    """A word extracted by Tesseract with its bounding box."""
    text: str
    x: int
    y: int
    w: int
    h: int
    conf: int


@dataclass
class DetectedTable:
    """A fully reconstructed table."""
    x: int
    y: int
    w: int
    h: int
    rows: int
    cols: int
    cells: List[GridCell]
    latex: str = ""


# ===================================================================
# PHASE 1 — Shape Detection (OpenCV)
# ===================================================================

def _fast_orientation_fix(image: Image.Image) -> Image.Image:
    """Rotate landscape pages 90° (fast heuristic, no OSD)."""
    w, h = image.size
    if w > h * 1.20:
        logger.info("TableOCR: Rotating landscape page by 90°")
        return image.rotate(-90, expand=True)
    return image


def _detect_lines(binary: np.ndarray) -> Tuple[List[int], List[int]]:
    """
    Detect horizontal and vertical line positions using morphology.
    Returns (sorted list of Y positions, sorted list of X positions).
    """
    import cv2

    h, w = binary.shape

    # --- Horizontal lines ---
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 25, 30), 1))
    h_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel)
    h_lines = cv2.dilate(h_lines, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3)), iterations=1)

    # Project horizontally — each row with enough white pixels is a line
    h_proj = np.sum(h_lines, axis=1)
    h_threshold = w * 0.15 * 255  # at least 15 % of width
    h_positions = _cluster_positions(np.where(h_proj > h_threshold)[0], min_gap=8)

    # --- Vertical lines ---
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(h // 25, 30)))
    v_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kernel)
    v_lines = cv2.dilate(v_lines, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1)), iterations=1)

    v_proj = np.sum(v_lines, axis=0)
    v_threshold = h * 0.15 * 255
    v_positions = _cluster_positions(np.where(v_proj > v_threshold)[0], min_gap=8)

    return h_positions, v_positions


def _cluster_positions(positions: np.ndarray, min_gap: int = 8) -> List[int]:
    """Cluster nearby pixel positions into single line coordinates."""
    if len(positions) == 0:
        return []
    clusters: List[List[int]] = [[int(positions[0])]]
    for p in positions[1:]:
        if int(p) - clusters[-1][-1] <= min_gap:
            clusters[-1].append(int(p))
        else:
            clusters.append([int(p)])
    # Use median of each cluster
    return [int(np.median(c)) for c in clusters]


def _build_virtual_grid(
    h_positions: List[int],
    v_positions: List[int],
    img_h: int,
    img_w: int,
) -> List[GridCell]:
    """
    Build a grid of cells from detected line positions.
    Each cell is the rectangle between consecutive H and V lines.
    """
    # We need at least 2 horizontal and 2 vertical lines to form cells
    if len(h_positions) < 2 or len(v_positions) < 2:
        return []

    cells: List[GridCell] = []
    for ri in range(len(h_positions) - 1):
        for ci in range(len(v_positions) - 1):
            y1 = h_positions[ri]
            y2 = h_positions[ri + 1]
            x1 = v_positions[ci]
            x2 = v_positions[ci + 1]
            # Skip tiny slivers
            if (y2 - y1) < 8 or (x2 - x1) < 8:
                continue
            cells.append(GridCell(
                row=ri, col=ci,
                x=x1, y=y1,
                w=x2 - x1, h=y2 - y1,
            ))
    return cells


def detect_grid(image: Image.Image) -> Tuple[List[GridCell], List[int], List[int]]:
    """
    Phase 1: Detect table grid structure from a page image.

    Returns:
        (cells, h_positions, v_positions)
    """
    import cv2

    img_array = np.array(image.convert("L"))
    # Adaptive threshold → binary (white text/lines on black)
    binary = cv2.adaptiveThreshold(
        img_array, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        15, 10,
    )

    h_positions, v_positions = _detect_lines(binary)
    logger.debug(
        f"Phase1: detected {len(h_positions)} H-lines, {len(v_positions)} V-lines"
    )

    cells = _build_virtual_grid(h_positions, v_positions, *binary.shape)
    return cells, h_positions, v_positions


# ===================================================================
# PHASE 2 — Text Extraction (Tesseract word boxes)
# ===================================================================

def extract_word_boxes(image: Image.Image, timeout_s: float = 6.0) -> List[WordBox]:
    """
    Phase 2: Run Tesseract once on the full page image and return
    every detected word with its bounding box and confidence.
    """
    import pytesseract

    _configure_tesseract()

    try:
        data = pytesseract.image_to_data(
            image,
            lang="fra",
            config="--oem 1 --psm 6",
            output_type=pytesseract.Output.DICT,
            timeout=timeout_s,
        )
    except Exception as e:
        logger.error(f"Phase2 Tesseract failed: {e}")
        return []

    words: List[WordBox] = []
    n = len(data["text"])
    for i in range(n):
        txt = (data["text"][i] or "").strip()
        if not txt:
            continue
        conf = int(data["conf"][i]) if data["conf"][i] != "-1" else 0
        if conf < 20:
            continue  # skip very low-confidence junk
        words.append(WordBox(
            text=txt,
            x=int(data["left"][i]),
            y=int(data["top"][i]),
            w=int(data["width"][i]),
            h=int(data["height"][i]),
            conf=conf,
        ))

    logger.debug(f"Phase2: extracted {len(words)} words")
    return words


# ===================================================================
# PHASE 3 — Coordinate Merging
# ===================================================================

def _word_center(wb: WordBox) -> Tuple[int, int]:
    return wb.x + wb.w // 2, wb.y + wb.h // 2


def assign_words_to_cells(cells: List[GridCell], words: List[WordBox]) -> None:
    """
    Phase 3: For each word, find the grid cell whose bounding box contains
    the word's center point. Mutates cells in-place.
    """
    if not cells:
        return

    for wb in words:
        cx, cy = _word_center(wb)
        best_cell: Optional[GridCell] = None
        best_dist = float("inf")

        for cell in cells:
            # Check containment
            if (cell.x <= cx <= cell.x + cell.w) and (cell.y <= cy <= cell.y + cell.h):
                # If inside, distance is 0 — but prefer tightest fit
                best_cell = cell
                best_dist = 0
                break  # exact match, done

        # Fallback: nearest cell if not inside any (handles slight misalignment)
        if best_cell is None:
            for cell in cells:
                cell_cx = cell.x + cell.w // 2
                cell_cy = cell.y + cell.h // 2
                dist = abs(cx - cell_cx) + abs(cy - cell_cy)
                # Only consider if reasonably close (within 50 % of cell size)
                max_dist = (cell.w + cell.h) * 0.5
                if dist < best_dist and dist < max_dist:
                    best_dist = dist
                    best_cell = cell

        if best_cell is not None:
            best_cell.words.append(wb.text)


# ===================================================================
# PHASE 4 — LaTeX Output
# ===================================================================

def _escape_latex(text: str) -> str:
    """Escape characters that have special meaning in LaTeX."""
    replacements = [
        ("\\", "\\textbackslash{}"),
        ("&", "\\&"),
        ("%", "\\%"),
        ("$", "\\$"),
        ("#", "\\#"),
        ("_", "\\_"),
        ("{", "\\{"),
        ("}", "\\}"),
        ("~", "\\textasciitilde{}"),
        ("^", "\\textasciicircum{}"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def cells_to_latex(cells: List[GridCell], table_index: int = 0) -> str:
    """
    Phase 4: Convert grid cells into a LaTeX tabular environment.

    Output format:
        % TABLE <idx> — <rows> rows × <cols> cols
        \\begin{tabular}{|l|l|l|...|}
        \\hline
        cell & cell & cell \\\\
        \\hline
        ...
        \\end{tabular}
    """
    if not cells:
        return ""

    max_row = max(c.row for c in cells)
    max_col = max(c.col for c in cells)
    n_rows = max_row + 1
    n_cols = max_col + 1

    # Build row-major 2D array
    grid: List[List[str]] = [["" for _ in range(n_cols)] for _ in range(n_rows)]
    for cell in cells:
        grid[cell.row][cell.col] = _escape_latex(cell.text)

    # Build LaTeX
    col_spec = "|".join(["l"] * n_cols)
    lines = [
        f"% TABLE {table_index} — {n_rows} rows x {n_cols} cols",
        f"\\begin{{tabular}}{{|{col_spec}|}}",
        "\\hline",
    ]
    for row in grid:
        row_str = " & ".join(row) + " \\\\"
        lines.append(row_str)
        lines.append("\\hline")
    lines.append("\\end{tabular}")

    return "\n".join(lines)


# ===================================================================
# Orchestrator — full page pipeline
# ===================================================================

def process_page_table(
    image: Image.Image,
    table_index: int = 0,
    tesseract_timeout: float = 6.0,
) -> Optional[DetectedTable]:
    """
    Run the full 4-phase pipeline on a single page image.

    Returns a DetectedTable (with .latex) or None if no grid detected.
    """
    t0 = time.monotonic()

    # Phase 1 — grid detection
    cells, h_pos, v_pos = detect_grid(image)
    if not cells:
        return None
    t1 = time.monotonic()
    logger.debug(f"Phase1 grid: {len(cells)} cells in {t1 - t0:.2f}s")

    # Phase 2 — word extraction (single Tesseract call on full page)
    words = extract_word_boxes(image, timeout_s=tesseract_timeout)
    t2 = time.monotonic()
    logger.debug(f"Phase2 words: {len(words)} words in {t2 - t1:.2f}s")

    # Phase 3 — coordinate merging
    assign_words_to_cells(cells, words)
    t3 = time.monotonic()
    logger.debug(f"Phase3 merge: {t3 - t2:.2f}s")

    # Phase 4 — LaTeX
    max_row = max(c.row for c in cells)
    max_col = max(c.col for c in cells)
    latex = cells_to_latex(cells, table_index)
    t4 = time.monotonic()

    logger.info(
        f"TableOCR page done: {max_row + 1}×{max_col + 1} table, "
        f"{len(words)} words, {t4 - t0:.2f}s total"
    )

    # Derive bounding box from line positions
    return DetectedTable(
        x=v_pos[0] if v_pos else 0,
        y=h_pos[0] if h_pos else 0,
        w=(v_pos[-1] - v_pos[0]) if len(v_pos) >= 2 else 0,
        h=(h_pos[-1] - h_pos[0]) if len(h_pos) >= 2 else 0,
        rows=max_row + 1,
        cols=max_col + 1,
        cells=cells,
        latex=latex,
    )


# ===================================================================
# Public API — drop-in replacements for the rest of the pipeline
# ===================================================================

def ocr_page_with_table_detection(
    image: Image.Image,
    fallback_to_full_ocr: bool = True,
) -> str:
    """
    OCR a single page. If a table grid is detected, returns LaTeX.
    Otherwise falls back to standard Tesseract full-page OCR.
    """
    import pytesseract

    _configure_tesseract()
    image = _fast_orientation_fix(image)

    table = process_page_table(image)
    if table and table.latex:
        return table.latex

    if fallback_to_full_ocr:
        logger.debug("No table grid detected, using standard OCR")
        try:
            text = pytesseract.image_to_string(
                image, lang="fra+eng", config="--oem 1 --psm 1", timeout=8.0,
            )
            return text.strip()
        except Exception as e:
            logger.error(f"Fallback OCR failed: {e}")
            return ""

    return ""


def extract_table_with_structure(
    image: Image.Image,
    dpi: int = 200,
    **_kwargs,
) -> Dict[str, Any]:
    """
    Legacy-compatible wrapper. Returns dict with tables/text/metadata.
    """
    image = _fast_orientation_fix(image)
    table = process_page_table(image)

    if not table:
        return {"tables": [], "text": "", "metadata": {"tables_found": 0}}

    return {
        "tables": [{
            "table_index": 0,
            "bounds": {"x": table.x, "y": table.y, "width": table.w, "height": table.h},
            "rows": table.rows,
            "cols": table.cols,
            "cells": [
                {"row": c.row, "col": c.col, "x": c.x, "y": c.y,
                 "width": c.w, "height": c.h, "text": c.text}
                for c in table.cells
            ],
        }],
        "text": table.latex,
        "metadata": {
            "tables_found": 1,
            "total_cells": len(table.cells),
            "output_format": "latex",
        },
    }


def ocr_pages_with_table_extraction(
    file_bytes: io.BytesIO,
    page_numbers: List[int],
    dpi: int = 200,
    timeout_per_page: float = 10.0,
) -> Dict[int, str]:
    """
    OCR specific PDF pages with the 4-phase table pipeline.
    Returns dict mapping page_number → extracted text (LaTeX or plain).
    """
    from pdf2image import convert_from_bytes

    poppler_path = _get_poppler_path()
    results: Dict[int, str] = {}

    try:
        file_bytes.seek(0)
        pdf_bytes = file_bytes.read()

        for page_num in page_numbers:
            t0 = time.monotonic()
            logger.info(f"TableOCR processing page {page_num} at {dpi} DPI…")

            try:
                images = convert_from_bytes(
                    pdf_bytes,
                    dpi=dpi,
                    first_page=page_num,
                    last_page=page_num,
                    poppler_path=poppler_path,
                    fmt="jpeg",
                    thread_count=2,
                )

                if not images:
                    results[page_num] = f"[Page {page_num} conversion failed]"
                    continue

                text = ocr_page_with_table_detection(images[0])
                elapsed = time.monotonic() - t0

                if elapsed > timeout_per_page:
                    logger.warning(
                        f"TableOCR page {page_num} slow: {elapsed:.1f}s "
                        f"(budget {timeout_per_page:.1f}s)"
                    )

                if text:
                    logger.info(
                        f"TableOCR page {page_num}: {len(text)} chars in {elapsed:.1f}s"
                    )
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


# ===================================================================
# Bordereau page detection + re-OCR (unchanged interface)
# ===================================================================

def detect_bordereau_pages(full_text: str) -> List[int]:
    """Detect which pages likely contain Bordereau des Prix tables."""
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
    pages = re.split(r'---\s*Page\s+(\d+)\s*---', full_text)
    detected: List[int] = []

    for i in range(1, len(pages), 2):
        if i + 1 < len(pages):
            page_num = int(pages[i])
            content = pages[i + 1].lower()
            matches = len(re.findall(pattern, content, re.IGNORECASE))
            has_table = content.count('|') > 5 or bool(re.search(r'\d+\s+\d+', content))
            if matches >= 2 or (matches >= 1 and has_table):
                detected.append(page_num)

    return detected


def reocr_bordereau_pages(
    file_bytes: io.BytesIO,
    original_text: str,
    force_pages: Optional[List[int]] = None,
) -> str:
    """
    Re-OCR bordereau pages with the coordinate-based table pipeline
    and merge LaTeX output back into the original text.
    """
    if force_pages:
        bordereau_pages = force_pages
    else:
        bordereau_pages = detect_bordereau_pages(original_text)

    if not bordereau_pages:
        logger.info("No bordereau pages detected, keeping original OCR")
        return original_text

    logger.info(
        f"🔄 Re-OCRing {len(bordereau_pages)} bordereau pages "
        f"with coordinate pipeline: {bordereau_pages}"
    )

    table_results = ocr_pages_with_table_extraction(file_bytes, bordereau_pages)

    result_text = original_text
    for page_num, table_text in table_results.items():
        if "[OCR" in table_text or "[Page" in table_text:
            logger.warning(
                f"TableOCR failed for page {page_num}, keeping Tesseract result"
            )
            continue

        pattern = rf'(---\s*Page\s+{page_num}\s*---\n)(.*?)(?=---\s*Page\s+\d+\s*---|$)'

        def replace_page(match, _txt=table_text):
            return f"{match.group(1)}{_txt}\n\n"

        new_text = re.sub(pattern, replace_page, result_text, flags=re.DOTALL)
        if new_text != result_text:
            logger.info(f"✅ Page {page_num} re-OCR'd with LaTeX table: {len(table_text)} chars")
            result_text = new_text

    return result_text
