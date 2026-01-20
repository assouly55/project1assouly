"""
Article Indexer - Extracts verified article structure from CPS/RC documents.
Filters out Table of Contents entries and validates real article headers.
"""

import re
import logging
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Multiple regex patterns to capture various Article header formats
# Pattern 1: Standard "Article X" format with optional prefixes
ARTICLE_PATTERNS = [
    # Standard: "Article 1", "Article n° 12", "ARTICLE 15 :"
    re.compile(
        r'(?i)(?:^|\n)\s*ARTICLE\s+(?:n°|nº|no|no\.|#|N°)?\s*(\d+)\s*[:\-–—.]?\s*(.{0,150}?)(?=\n|$)',
        re.MULTILINE
    ),
    # With colon before number: "Article : 1 -", "ARTICLE: 5"
    re.compile(
        r'(?i)(?:^|\n)\s*ARTICLE\s*[:\-]\s*(\d+)\s*[:\-–—.]?\s*(.{0,150}?)(?=\n|$)',
        re.MULTILINE
    ),
    # Uppercase strict: "ARTICLE 1" at start of line
    re.compile(
        r'(?:^|\n)\s*(ARTICLE)\s+(\d+)\s*[:\-–—.]?\s*(.{0,150}?)(?=\n|$)',
        re.MULTILINE
    ),
    # French with dash: "Article 1 -", "Article 2-"
    re.compile(
        r'(?i)(?:^|\n)\s*Article\s+(\d+)\s*[-–—]\s*(.{0,150}?)(?=\n|$)',
        re.MULTILINE
    ),
    # Numbered sections that look like articles (fallback for scanned docs)
    re.compile(
        r'(?:^|\n)\s*(\d{1,2})\s*[.)\-–]\s*([A-ZÀ-Ü][A-Za-zÀ-ÿ\s]{5,80})(?=\n|$)',
        re.MULTILINE
    ),
]

# Legacy pattern for backward compatibility
ARTICLE_PATTERN = ARTICLE_PATTERNS[0]

# ToC signature patterns (dots, underscores, page references)
TOC_SIGNATURES = [
    re.compile(r'\.{3,}'),           # Series of dots (.....)
    re.compile(r'_{3,}'),            # Series of underscores (___)
    re.compile(r'\d+\s*$'),          # Ends with page number
    re.compile(r'page\s*\d+', re.I), # "page 12"
    re.compile(r'p\.\s*\d+', re.I),  # "p. 12"
    re.compile(r'^\s*\d+\s*$'),      # Just a number (page marker)
]


def is_toc_entry(line: str) -> bool:
    """Check if a line looks like a Table of Contents entry."""
    for pattern in TOC_SIGNATURES:
        if pattern.search(line):
            return True
    return False


def get_line_context(text: str, match_start: int, match_end: int) -> str:
    """Get the full line containing the match."""
    # Find line start
    line_start = text.rfind('\n', 0, match_start)
    line_start = line_start + 1 if line_start != -1 else 0
    
    # Find line end
    line_end = text.find('\n', match_end)
    line_end = line_end if line_end != -1 else len(text)
    
    return text[line_start:line_end]


def has_content_after(text: str, end_index: int, min_chars: int = 50) -> bool:
    """
    Check if there's real content after the article header.
    A real article must have text that doesn't start with 'Article'.
    """
    # Get next 500 chars to check for content
    following_text = text[end_index:end_index + 500].strip()
    
    if len(following_text) < min_chars:
        return False
    
    # Split into lines and check first non-empty lines
    lines = [l.strip() for l in following_text.split('\n') if l.strip()]
    
    if not lines:
        return False
    
    # Check first few lines - they shouldn't all be Article headers
    non_article_content = 0
    for line in lines[:5]:
        if not re.match(r'(?i)^\s*Article\s+', line):
            non_article_content += 1
    
    return non_article_content >= 1


def get_verified_articles(text: str) -> List[Dict]:
    """
    Extract verified article structure from document text.
    Uses multiple regex patterns to capture various article formats.
    
    Returns:
        List of dicts with keys:
        - articleNumber: str (e.g., "1", "12")
        - title: str (article title if found)
        - startIndex: int (character position where article starts)
        - endIndex: int (character position where article ends, i.e., next article starts)
    """
    if not text:
        return []
    
    # Collect matches from all patterns
    all_matches = []
    seen_positions = set()  # Avoid duplicates from overlapping patterns
    
    for pattern_idx, pattern in enumerate(ARTICLE_PATTERNS):
        for match in pattern.finditer(text):
            match_start = match.start()
            
            # Skip if we already have a match near this position (within 10 chars)
            is_duplicate = False
            for seen_pos in seen_positions:
                if abs(match_start - seen_pos) < 10:
                    is_duplicate = True
                    break
            
            if is_duplicate:
                continue
            
            seen_positions.add(match_start)
            
            # Extract article number and title based on pattern structure
            groups = match.groups()
            if pattern_idx == 2:  # Pattern with "ARTICLE" as group 1
                article_num = groups[1] if len(groups) > 1 else groups[0]
                title_raw = groups[2].strip() if len(groups) > 2 and groups[2] else ""
            else:
                article_num = groups[0]
                title_raw = groups[1].strip() if len(groups) > 1 and groups[1] else ""
            
            all_matches.append({
                "match": match,
                "pattern_idx": pattern_idx,
                "article_num": article_num,
                "title_raw": title_raw,
                "start": match_start,
                "end": match.end(),
            })
    
    if not all_matches:
        logger.info("No article matches found in document")
        return []
    
    # Sort by position in document
    all_matches.sort(key=lambda x: x["start"])
    
    logger.info(f"Found {len(all_matches)} potential article matches from {len(ARTICLE_PATTERNS)} patterns")
    
    # Filter matches
    verified_matches = []
    
    for i, m in enumerate(all_matches):
        match_start = m["start"]
        match_end = m["end"]
        article_num = m["article_num"]
        title_raw = m["title_raw"]
        
        # Get full line context
        line = get_line_context(text, match_start, match_end)
        
        # Filter 1: Skip ToC entries
        if is_toc_entry(line):
            logger.debug(f"Skipping ToC entry: Article {article_num}")
            continue
        
        # Filter 2: Check for clustered matches (ToC list detection)
        # If another article starts within 15 chars after this title ends, it's likely a list/index
        is_clustered = False
        for j, other_m in enumerate(all_matches):
            if i != j:
                gap = other_m["start"] - match_end
                if 0 < gap < 15:
                    is_clustered = True
                    break
        
        if is_clustered:
            logger.debug(f"Skipping clustered match: Article {article_num}")
            continue
        
        # Filter 3: Validate that real content follows
        if not has_content_after(text, match_end, min_chars=30):
            logger.debug(f"Skipping Article {article_num} - no content follows")
            continue
        
        # Clean up title
        title = title_raw.strip()
        # Remove trailing punctuation and Arabic text artifacts
        title = re.sub(r'[:\-–—.]+$', '', title).strip()
        # Remove excessive whitespace
        title = re.sub(r'\s+', ' ', title).strip()
        
        verified_matches.append({
            "articleNumber": str(article_num),
            "title": title[:150],  # Limit title length
            "startIndex": match_start,
            "matchEnd": match_end,
        })
    
    logger.info(f"Verified {len(verified_matches)} real articles")
    
    # Calculate endIndex for each article (start of next article or end of doc)
    articles = []
    for i, article in enumerate(verified_matches):
        end_index = verified_matches[i + 1]["startIndex"] if i + 1 < len(verified_matches) else len(text)
        
        articles.append({
            "articleNumber": article["articleNumber"],
            "title": article["title"],
            "startIndex": article["startIndex"],
            "endIndex": end_index,
        })
    
    return articles


def extract_article_content(text: str, article: Dict) -> str:
    """Extract the full content of a single article."""
    return text[article["startIndex"]:article["endIndex"]].strip()


def get_article_map(text: str) -> Dict[str, Dict]:
    """
    Create a map of article number -> article info with content preview.
    Useful for quick lookups and AI context.
    """
    articles = get_verified_articles(text)
    
    article_map = {}
    for article in articles:
        content = extract_article_content(text, article)
        article_map[article["articleNumber"]] = {
            "title": article["title"],
            "startIndex": article["startIndex"],
            "endIndex": article["endIndex"],
            "contentLength": len(content),
            "preview": content[:300] + "..." if len(content) > 300 else content,
        }
    
    return article_map


def slice_document_by_articles(text: str) -> List[Dict]:
    """
    Slice document into individual article chunks for targeted AI processing.
    
    Returns:
        List of dicts with keys:
        - articleNumber: str
        - title: str
        - content: str (full article text)
        - charCount: int
    """
    articles = get_verified_articles(text)
    
    slices = []
    for article in articles:
        content = extract_article_content(text, article)
        slices.append({
            "articleNumber": article["articleNumber"],
            "title": article["title"],
            "content": content,
            "charCount": len(content),
        })
    
    return slices


def get_articles_for_field(field_name: str) -> List[str]:
    """
    Get list of article numbers/keywords that typically contain a specific field.
    Used for smart AI analysis to target relevant articles.
    
    Args:
        field_name: The metadata field we're looking for
        
    Returns:
        List of article keywords/numbers to search for
    """
    # Mapping of metadata fields to likely article keywords
    field_to_articles = {
        # Execution/delivery
        "execution_delay": ["délai", "exécution", "livraison", "délais", "durée"],
        "delivery_location": ["lieu", "livraison", "exécution", "remise"],
        
        # Financial/guarantees
        "caution_definitive": ["caution", "garantie", "définitive", "retenue"],
        "caution_provisoire": ["caution", "provisoire", "garantie"],
        "payment_terms": ["paiement", "règlement", "facturation", "décompte"],
        
        # Documents/qualification
        "required_documents": ["pièces", "documents", "dossier", "justificatif"],
        "qualification_criteria": ["qualification", "capacité", "référence", "agrément"],
        
        # Warranty/maintenance
        "warranty_period": ["garantie", "maintenance", "entretien"],
        
        # Items/specifications
        "items": ["objet", "désignation", "spécification", "description", "caractéristiques"],
        
        # Contact/address
        "contact": ["contact", "correspondance", "adresse", "maître d'ouvrage"],
        "institution_address": ["adresse", "siège", "coordonnées"],
    }
    
    return field_to_articles.get(field_name, [])


def find_relevant_articles(
    articles: List[Dict], 
    field_name: str,
    max_articles: int = 5
) -> List[Dict]:
    """
    Find articles most relevant to a specific metadata field.
    
    Args:
        articles: List of article dicts from get_verified_articles()
        field_name: The metadata field we're looking for
        max_articles: Maximum number of articles to return
        
    Returns:
        List of relevant article dicts (subset of input)
    """
    keywords = get_articles_for_field(field_name)
    if not keywords:
        return articles[:max_articles]  # Return first N if no keywords
    
    scored_articles = []
    for article in articles:
        title_lower = (article.get("title") or "").lower()
        score = sum(1 for kw in keywords if kw.lower() in title_lower)
        if score > 0:
            scored_articles.append((score, article))
    
    # Sort by score descending
    scored_articles.sort(key=lambda x: x[0], reverse=True)
    
    # Return top articles
    relevant = [art for _, art in scored_articles[:max_articles]]
    
    # If not enough, add some from the beginning
    if len(relevant) < max_articles:
        remaining = max_articles - len(relevant)
        added = set(art["articleNumber"] for art in relevant)
        for art in articles:
            if art["articleNumber"] not in added:
                relevant.append(art)
                if len(relevant) >= max_articles:
                    break
    
    return relevant


def build_article_index_for_db(text: str, doc_type: str) -> Dict:
    """
    Build article index structure suitable for storing in database.
    Includes full document content for lookups outside indexed articles.
    
    Args:
        text: Full document text
        doc_type: Document type (CPS, RC, etc.)
        
    Returns:
        Dict with article index, full content, and metadata
    """
    articles = get_verified_articles(text)
    
    return {
        "doc_type": doc_type,
        "total_articles": len(articles),
        "total_chars": len(text),
        "full_content": text,  # Keep full document content for lookups outside articles
        "articles": [
            {
                "articleNumber": art["articleNumber"],
                "title": art["title"],
                "startIndex": art["startIndex"],
                "endIndex": art["endIndex"],
                "charCount": art["endIndex"] - art["startIndex"],
            }
            for art in articles
        ]
    }


def get_article_content_by_number(
    text: str, 
    article_index: List[Dict], 
    article_number: str
) -> Optional[str]:
    """
    Get article content by article number using pre-computed index.
    
    Args:
        text: Full document text
        article_index: Pre-computed article index from DB
        article_number: Article number to retrieve
        
    Returns:
        Article content or None if not found
    """
    for art in article_index:
        if str(art["articleNumber"]) == str(article_number):
            return text[art["startIndex"]:art["endIndex"]].strip()
    return None


def get_articles_by_keywords(
    text: str,
    article_index: List[Dict],
    keywords: List[str],
    max_articles: int = 5
) -> List[Tuple[Dict, str]]:
    """
    Find articles matching keywords and return with their content.
    
    Args:
        text: Full document text
        article_index: Pre-computed article index
        keywords: Keywords to search in titles
        max_articles: Maximum articles to return
        
    Returns:
        List of (article_info, content) tuples
    """
    results = []
    
    for art in article_index:
        title_lower = (art.get("title") or "").lower()
        for kw in keywords:
            if kw.lower() in title_lower:
                content = text[art["startIndex"]:art["endIndex"]].strip()
                results.append((art, content))
                break
        
        if len(results) >= max_articles:
            break
    
    return results


# Utility for debugging
def print_article_structure(text: str):
    """Print article structure for debugging."""
    articles = get_verified_articles(text)
    
    print(f"\n{'='*60}")
    print(f"ARTICLE STRUCTURE ({len(articles)} articles found)")
    print('='*60)
    
    for art in articles:
        content_len = art["endIndex"] - art["startIndex"]
        title_display = art["title"][:50] + "..." if len(art["title"]) > 50 else art["title"]
        print(f"\nArticle {art['articleNumber']}: {title_display}")
        print(f"  Range: {art['startIndex']} - {art['endIndex']} ({content_len} chars)")
