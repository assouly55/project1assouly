# -*- coding: utf-8 -*-
"""
Document Mapper — AI-powered hierarchical document structure mapping.

During tender processing, this service runs AI on each extracted document to build
a tree map of its structure: articles, sub-sections, summaries, and detection of
multiple documents merged in a single PDF.

The map is stored alongside each document and used by Ask AI for precise targeting.
"""

import json
import re
from pathlib import Path
from typing import Optional, Dict, Any, List
from loguru import logger


def _load_map_prompt() -> str:
    """Load the document mapping prompt."""
    prompt_path = Path(__file__).parent / "prompts" / "document_map_prompt.txt"
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()


_MAP_PROMPT: Optional[str] = None


def get_map_prompt() -> str:
    global _MAP_PROMPT
    if _MAP_PROMPT is None:
        _MAP_PROMPT = _load_map_prompt()
    return _MAP_PROMPT


def build_document_map(
    ai_client,
    model: str,
    text: str,
    filename: str,
    document_type: str,
    max_input_chars: int = 50000,
) -> Optional[Dict[str, Any]]:
    """
    Use AI to build a hierarchical tree map of a document.

    Args:
        ai_client: OpenAI-compatible client
        model: Model name
        text: Full extracted text of the document
        filename: Original filename
        document_type: Detected type (CPS, RC, etc.)
        max_input_chars: Max chars to send to AI

    Returns:
        Document map dict or None on failure
    """
    if not text or len(text.strip()) < 100:
        logger.debug(f"Skipping map for {filename}: too short ({len(text)} chars)")
        return None

    logger.info(f"🗺️ Mapping document structure: {filename} ({len(text)} chars)")

    # For very long documents, we send the full text but cap at max_input_chars
    # The AI is instructed to cover ALL articles even at end
    input_text = text[:max_input_chars]

    user_content = (
        f"FICHIER: {filename}\n"
        f"TYPE DÉTECTÉ: {document_type}\n"
        f"LONGUEUR TOTALE: {len(text)} caractères\n\n"
        f"TEXTE COMPLET DU DOCUMENT:\n\n{input_text}"
    )

    try:
        response = ai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": get_map_prompt()},
                {"role": "user", "content": user_content},
            ],
            max_tokens=4096,
            temperature=0,
        )
        raw = response.choices[0].message.content
    except Exception as e:
        logger.error(f"AI mapping call failed for {filename}: {e}")
        return None

    # Parse JSON
    doc_map = _parse_json(raw)
    if not doc_map:
        logger.warning(f"Failed to parse document map for {filename}")
        return None

    # Enrich with character ranges for each article using text search
    doc_map = _enrich_with_positions(doc_map, text)

    total_articles = doc_map.get("total_articles", 0)
    sub_docs = len(doc_map.get("sub_documents", []))
    logger.info(f"   ✅ Map: {total_articles} articles in {sub_docs} sub-document(s)")

    return doc_map


def _parse_json(raw: str) -> Optional[Dict]:
    """Parse JSON from AI response."""
    try:
        text = raw
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        return json.loads(text.strip())
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error in document map: {e}")
        return None


def _enrich_with_positions(doc_map: Dict, text: str) -> Dict:
    """
    Find character positions for each article in the actual text.
    This enables precise content extraction later.
    """
    text_lower = text.lower()

    for sub_doc in doc_map.get("sub_documents", []):
        articles = sub_doc.get("articles", [])
        for i, article in enumerate(articles):
            art_num = str(article.get("number", ""))
            art_title = article.get("title", "")

            # Search patterns for this article header
            patterns = [
                # "Article 1 : Title" or "Article 1 - Title"
                rf"article\s+(?:n[°o]?\s*)?{re.escape(art_num)}\s*[:\-–—.]\s*",
                # "ARTICLE 1" at start of line
                rf"(?:^|\n)\s*article\s+{re.escape(art_num)}\s",
            ]

            start_pos = None
            for pat in patterns:
                m = re.search(pat, text_lower)
                if m:
                    # Find actual line start
                    line_start = text.rfind("\n", 0, m.start())
                    start_pos = line_start + 1 if line_start != -1 else m.start()
                    break

            if start_pos is not None:
                article["_start"] = start_pos

                # End = start of next article or end of sub-doc section
                if i + 1 < len(articles):
                    next_art = articles[i + 1]
                    next_num = str(next_art.get("number", ""))
                    for pat in [
                        rf"article\s+(?:n[°o]?\s*)?{re.escape(next_num)}\s*[:\-–—.]",
                        rf"(?:^|\n)\s*article\s+{re.escape(next_num)}\s",
                    ]:
                        nm = re.search(pat, text_lower[start_pos + 10:])
                        if nm:
                            article["_end"] = start_pos + 10 + nm.start()
                            break

                if "_end" not in article:
                    # Default: next 20000 chars or end of text
                    article["_end"] = min(start_pos + 20000, len(text))

    return doc_map


def get_article_content_from_map(
    text: str,
    doc_map: Dict,
    article_number: str,
    doc_type_filter: Optional[str] = None,
) -> Optional[str]:
    """
    Extract article content using the document map positions.

    Args:
        text: Full document text
        doc_map: Document map from build_document_map
        article_number: Article number to find (e.g. "22")
        doc_type_filter: Optional sub-document type filter (e.g. "CPS")

    Returns:
        Article text content or None
    """
    for sub_doc in doc_map.get("sub_documents", []):
        if doc_type_filter and sub_doc.get("type", "").upper() != doc_type_filter.upper():
            continue

        for article in sub_doc.get("articles", []):
            if str(article.get("number", "")) == str(article_number):
                start = article.get("_start")
                end = article.get("_end")
                if start is not None and end is not None:
                    return text[start:end].strip()

    return None


def find_relevant_articles_from_map(
    doc_map: Dict,
    keywords: List[str],
    max_articles: int = 10,
) -> List[Dict]:
    """
    Find articles whose title or summary match keywords.

    Returns list of article dicts with sub_doc_type added.
    """
    results = []

    for sub_doc in doc_map.get("sub_documents", []):
        sub_type = sub_doc.get("type", "AUTRE")

        for article in sub_doc.get("articles", []):
            title = (article.get("title") or "").lower()
            summary = (article.get("summary") or "").lower()
            searchable = f"{title} {summary}"

            score = sum(1 for kw in keywords if kw.lower() in searchable)
            if score > 0:
                results.append({
                    **article,
                    "_sub_doc_type": sub_type,
                    "_relevance_score": score,
                })

    # Sort by relevance
    results.sort(key=lambda x: x["_relevance_score"], reverse=True)
    return results[:max_articles]


def format_map_for_ai_selection(doc_map: Dict) -> str:
    """
    Format document map as a compact text for AI to select relevant articles.
    Format: DOC_TYPE | ARTICLE_NUM | TITLE | SUMMARY
    """
    lines = []
    for sub_doc in doc_map.get("sub_documents", []):
        sub_type = sub_doc.get("type", "AUTRE")
        for article in sub_doc.get("articles", []):
            num = article.get("number", "?")
            title = article.get("title", "")
            summary = article.get("summary", "")
            line = f"{sub_type}|{num}|{title}"
            if summary:
                line += f"|{summary}"
            lines.append(line)
    return "\n".join(lines)
