# -*- coding: utf-8 -*-
"""
Smart Article Selector - AI-powered article selection for Phase 2 extraction.
Uses AI to identify which articles are relevant for missing metadata fields.
"""

import json
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass
from openai import OpenAI
from loguru import logger

from app.core.config import settings
from app.services.article_indexer import (
    get_verified_articles,
    extract_article_content,
    get_article_map,
)


def _load_prompt(filename: str) -> str:
    """Load a prompt from the prompts directory"""
    prompt_path = Path(__file__).parent / "prompts" / filename
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()


# Lazy-loaded prompt
ARTICLE_SELECTOR_PROMPT = None


def get_article_selector_prompt() -> str:
    """Get the ARTICLE_SELECTOR_PROMPT, loading from file if needed"""
    global ARTICLE_SELECTOR_PROMPT
    if ARTICLE_SELECTOR_PROMPT is None:
        ARTICLE_SELECTOR_PROMPT = _load_prompt("article_selector_prompt.txt")
    return ARTICLE_SELECTOR_PROMPT


@dataclass
class ArticleSelection:
    """Result of AI article selection"""
    article_number: str
    target_fields: List[str]
    relevance_reason: str
    content: str  # Full article content for processing


@dataclass
class SelectionResult:
    """Full selection result with metadata"""
    selected_articles: List[ArticleSelection]
    fields_covered: List[str]
    fields_not_covered: List[str]
    total_chars: int


class SmartArticleSelector:
    """AI-powered article selector for targeted metadata extraction"""
    
    def __init__(self):
        self.client = OpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL
        )
        self.model = settings.DEEPSEEK_MODEL
    
    def _call_ai(
        self, 
        system_prompt: str, 
        user_content: str,
        max_tokens: int = 2048
    ) -> Optional[str]:
        """Make AI API call"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
                max_tokens=max_tokens,
                temperature=0  # Deterministic selection
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"AI API call failed in article selector: {e}")
            return None
    
    def build_article_index_summary(
        self,
        document_text: str,
        doc_type: str
    ) -> Tuple[List[Dict], str]:
        """
        Build article index and create a summary for AI selection.
        
        Returns:
            Tuple of (article_list, summary_text)
        """
        articles = get_verified_articles(document_text)
        
        if not articles:
            logger.warning(f"No articles found in {doc_type} document")
            return [], ""
        
        logger.info(f"📑 Found {len(articles)} articles in {doc_type}")
        
        # Build summary for AI
        summary_lines = [f"=== INDEX DES ARTICLES ({doc_type}) ===\n"]
        
        for art in articles:
            content = extract_article_content(document_text, art)
            preview = content[:400].replace("\n", " ").strip()
            if len(content) > 400:
                preview += "..."
            
            summary_lines.append(
                f"Article {art['articleNumber']}: {art['title']}\n"
                f"  Aperçu: {preview}\n"
                f"  Longueur: {len(content)} caractères\n"
            )
        
        return articles, "\n".join(summary_lines)
    
    def select_relevant_articles(
        self,
        document_text: str,
        doc_type: str,
        missing_fields: List[str],
        existing_lots: List[str] = None
    ) -> SelectionResult:
        """
        Use AI to select articles relevant to missing metadata fields.
        
        Args:
            document_text: Full document text
            doc_type: Document type (CPS, RC, etc.)
            missing_fields: List of missing field names from Phase 2
            existing_lots: Lot numbers to process
            
        Returns:
            SelectionResult with selected articles and their content
        """
        if not missing_fields:
            logger.info("✓ No missing fields - skipping article selection")
            return SelectionResult(
                selected_articles=[],
                fields_covered=[],
                fields_not_covered=[],
                total_chars=0
            )
        
        logger.info(f"🔍 AI Article Selection for {doc_type} - {len(missing_fields)} missing fields")
        logger.info(f"   Missing: {missing_fields[:10]}{'...' if len(missing_fields) > 10 else ''}")
        
        # Build article index
        articles, index_summary = self.build_article_index_summary(document_text, doc_type)
        
        if not articles:
            return SelectionResult(
                selected_articles=[],
                fields_covered=[],
                fields_not_covered=missing_fields,
                total_chars=0
            )
        
        # Format missing fields for AI
        fields_context = "## CHAMPS MANQUANTS À EXTRAIRE\n\n"
        for field in missing_fields:
            fields_context += f"- {field}\n"
        
        if existing_lots:
            fields_context += f"\n## LOTS CONCERNÉS\n"
            for lot in existing_lots[:10]:
                fields_context += f"- Lot {lot}\n"
        
        # Build user prompt
        user_prompt = f"""{fields_context}

## INDEX DES ARTICLES DISPONIBLES

{index_summary}

Sélectionne les articles pertinents pour extraire les champs manquants."""
        
        logger.info(f"🤖 Calling AI selector for {len(articles)} articles...")
        
        response = self._call_ai(
            get_article_selector_prompt(),
            user_prompt,
            max_tokens=2048
        )
        
        if not response:
            logger.warning("AI selector returned no response - falling back to keyword matching")
            return self._fallback_selection(document_text, articles, missing_fields)
        
        # Parse AI response
        try:
            json_str = response
            if "```json" in response:
                json_str = response.split("```json")[1].split("```")[0]
            elif "```" in response:
                json_str = response.split("```")[1].split("```")[0]
            
            selection_data = json.loads(json_str.strip())
            
            # Build ArticleSelection objects with full content
            selected_articles = []
            total_chars = 0
            
            for sel in selection_data.get("selected_articles", []):
                art_num = str(sel.get("article_number", ""))
                
                # Find matching article
                matching_art = None
                for art in articles:
                    if str(art["articleNumber"]) == art_num:
                        matching_art = art
                        break
                
                if matching_art:
                    content = extract_article_content(document_text, matching_art)
                    total_chars += len(content)
                    
                    selected_articles.append(ArticleSelection(
                        article_number=art_num,
                        target_fields=sel.get("target_fields", []),
                        relevance_reason=sel.get("relevance_reason", ""),
                        content=content
                    ))
                    
                    logger.info(
                        f"   ✓ Article {art_num}: {matching_art.get('title', '')[:50]} "
                        f"→ {sel.get('target_fields', [])}"
                    )
            
            summary = selection_data.get("selection_summary", {})
            fields_covered = summary.get("fields_covered", [])
            fields_not_covered = summary.get("fields_not_covered", missing_fields)
            
            logger.info(
                f"📊 Selection complete: {len(selected_articles)} articles, "
                f"{total_chars} chars, {len(fields_covered)} fields covered"
            )
            
            if fields_not_covered:
                logger.warning(f"   ⚠ Fields not covered: {fields_not_covered}")
            
            return SelectionResult(
                selected_articles=selected_articles,
                fields_covered=fields_covered,
                fields_not_covered=fields_not_covered,
                total_chars=total_chars
            )
            
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse AI selection response: {e}")
            return self._fallback_selection(document_text, articles, missing_fields)
    
    def _fallback_selection(
        self,
        document_text: str,
        articles: List[Dict],
        missing_fields: List[str]
    ) -> SelectionResult:
        """
        Fallback to keyword-based selection if AI fails.
        """
        logger.info("📌 Using fallback keyword-based article selection")
        
        # Field to keyword mapping
        field_keywords = {
            "institution_address": ["adresse", "siège", "coordonnées", "maître"],
            "caution_definitive": ["caution", "garantie", "définitive", "retenue"],
            "execution_delay": ["délai", "exécution", "livraison", "durée"],
            "items": ["objet", "désignation", "spécification", "description", "fourniture"],
            "qualification": ["qualification", "capacité", "référence", "agrément"],
            "required_documents": ["pièces", "documents", "dossier", "justificatif"],
            "warranty": ["garantie", "maintenance", "entretien"],
            "payment": ["paiement", "règlement", "facturation", "décompte"],
        }
        
        selected_articles = []
        fields_covered = set()
        total_chars = 0
        
        for art in articles:
            title_lower = (art.get("title") or "").lower()
            matched_fields = []
            
            for field, keywords in field_keywords.items():
                # Check if this field is in missing fields (partial match)
                is_missing = any(field in mf.lower() for mf in missing_fields)
                if not is_missing:
                    continue
                
                # Check if article title matches keywords
                if any(kw.lower() in title_lower for kw in keywords):
                    matched_fields.append(field)
                    fields_covered.add(field)
            
            if matched_fields:
                content = extract_article_content(document_text, art)
                total_chars += len(content)
                
                selected_articles.append(ArticleSelection(
                    article_number=art["articleNumber"],
                    target_fields=matched_fields,
                    relevance_reason=f"Keyword match in title: {art.get('title', '')}",
                    content=content
                ))
                
                logger.info(f"   → Article {art['articleNumber']}: {matched_fields}")
        
        # Limit to 15 articles max
        if len(selected_articles) > 15:
            selected_articles = selected_articles[:15]
            total_chars = sum(len(a.content) for a in selected_articles)
        
        fields_not_covered = [f for f in missing_fields if not any(fc in f.lower() for fc in fields_covered)]
        
        logger.info(f"📊 Fallback selection: {len(selected_articles)} articles, {total_chars} chars")
        
        return SelectionResult(
            selected_articles=selected_articles,
            fields_covered=list(fields_covered),
            fields_not_covered=fields_not_covered,
            total_chars=total_chars
        )


# Singleton instance
smart_selector = SmartArticleSelector()
