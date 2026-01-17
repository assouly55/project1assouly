from __future__ import annotations

from typing import Any, Dict, List, Optional


# Required fields for metadata to be considered "complete"
# Only the essential fields that are always available on the website
REQUIRED_FIELDS = [
    "reference_tender",
    "subject",
    "submission_deadline",
]


def _is_blank_str(v: Any) -> bool:
    return isinstance(v, str) and v.strip() == ""


def _tracked_missing(tv: Any) -> bool:
    """Return True if a TrackedValue-like dict is missing/empty."""
    if not isinstance(tv, dict):
        return True
    v = tv.get("value")
    return v is None or _is_blank_str(v)


def _merge_tracked_value(base: Any, fallback: Any) -> Any:
    if _tracked_missing(base) and isinstance(fallback, dict):
        return fallback
    return base


def _merge_submission_deadline(base: Any, fallback: Any) -> Any:
    if not isinstance(base, dict):
        base = {}
    if not isinstance(fallback, dict):
        return base

    base_date = base.get("date")
    base_time = base.get("time")

    fb_date = fallback.get("date")
    fb_time = fallback.get("time")

    merged = {
        "date": _merge_tracked_value(base_date, fb_date),
        "time": _merge_tracked_value(base_time, fb_time),
    }
    return merged


def _merge_lots(base: Any, fallback: Any) -> Any:
    """Lots are not provenance-tracked; we merge by lot_number when possible."""
    if not isinstance(base, list) or len(base) == 0:
        return fallback if isinstance(fallback, list) else base
    if not isinstance(fallback, list) or len(fallback) == 0:
        return base

    # Build fallback index by lot_number (string)
    fb_by_num: Dict[str, Dict[str, Any]] = {}
    for lot in fallback:
        if isinstance(lot, dict):
            num = lot.get("lot_number")
            if isinstance(num, str) and num.strip():
                fb_by_num[num.strip()] = lot

    merged: List[Dict[str, Any]] = []
    for i, lot in enumerate(base):
        if not isinstance(lot, dict):
            continue

        num = lot.get("lot_number")
        fb = None
        if isinstance(num, str) and num.strip() and num.strip() in fb_by_num:
            fb = fb_by_num[num.strip()]
        elif i < len(fallback) and isinstance(fallback[i], dict):
            fb = fallback[i]

        if not fb:
            merged.append(lot)
            continue

        out = dict(lot)
        for k in ["lot_subject", "lot_estimated_value", "caution_provisoire"]:
            if out.get(k) is None or _is_blank_str(out.get(k)):
                out[k] = fb.get(k)
        # If lot_number missing but fallback has it
        if out.get("lot_number") is None or _is_blank_str(out.get("lot_number")):
            out["lot_number"] = fb.get("lot_number")

        merged.append(out)

    # If base had lots but all were empty and fallback has more, keep base length (no invention)
    return merged


def is_metadata_complete(metadata: Optional[Dict[str, Any]]) -> bool:
    """Check if all required fields are present and non-empty."""
    if not metadata:
        return False
    
    for field in REQUIRED_FIELDS:
        val = metadata.get(field)
        if val is None:
            return False
        
        # For TrackedValue fields
        if isinstance(val, dict):
            if "value" in val:
                if _tracked_missing(val):
                    return False
            # For submission_deadline which has nested date/time
            elif "date" in val:
                if _tracked_missing(val.get("date")):
                    return False
        elif _is_blank_str(val):
            return False
    
    return True


def get_missing_fields(metadata: Optional[Dict[str, Any]]) -> List[str]:
    """Return list of field names that are missing or empty."""
    if not metadata:
        return REQUIRED_FIELDS.copy()
    
    missing = []
    for field in REQUIRED_FIELDS:
        val = metadata.get(field)
        if val is None:
            missing.append(field)
            continue
        
        if isinstance(val, dict):
            if "value" in val:
                if _tracked_missing(val):
                    missing.append(field)
            elif "date" in val:
                if _tracked_missing(val.get("date")):
                    missing.append(field)
        elif _is_blank_str(val):
            missing.append(field)
    
    return missing


def merge_phase1_metadata(
    base: Optional[Dict[str, Any]],
    fallback: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Fill missing values in base using fallback (field-by-field)."""
    if not base:
        return fallback
    if not fallback:
        return base

    out = dict(base)

    # TrackedValue fields (only essential ones)
    for k in [
        "reference_tender",
        "tender_type",
        "issuing_institution",
        "subject",
        "total_estimated_value",
    ]:
        out[k] = _merge_tracked_value(out.get(k), fallback.get(k))

    # submission_deadline
    out["submission_deadline"] = _merge_submission_deadline(
        out.get("submission_deadline"), fallback.get("submission_deadline")
    )

    # lots
    out["lots"] = _merge_lots(out.get("lots"), fallback.get("lots"))

    # Preserve any extra keys already present in base; only add new keys if base doesn't have them
    for k, v in fallback.items():
        if k not in out:
            out[k] = v

    return out
