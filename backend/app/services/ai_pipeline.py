# -*- coding: utf-8 -*-
"""
Tender AI Platform - AI Pipeline Service (V4 Two-Step Bordereau Extraction)

Two-phase pipeline:
- Phase 1: Primary/Avis metadata extraction
- Phase 2: Bordereau des Prix extraction using two-step AI process:
    Step 1: Identify and extract raw Bordereau content
    Step 2: Parse extracted content into structured items

This ensures accurate extraction by first locating the correct sections.
"""

import json
import re
from pathlib import Path
from typing import Optional, Dict, Any, List
from openai import OpenAI
from loguru import logger

from app.core.config import settings
from app.services.extractor import DocumentType, ExtractionResult


def _load_prompt(filename: str) -> str:
    """Load a prompt from the prompts directory"""
    prompt_path = Path(__file__).parent / "prompts" / filename
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()


# Lazy-loaded prompts
_PROMPTS: Dict[str, Optional[str]] = {
    "primary": None,
    "bordereau": None,
    "ask_ai": None,
    "ask_ai_selector": None,
    "category": None,
}

# Lazy-loaded category tree
_CATEGORY_TREE: Optional[Dict] = None


def _load_categories() -> Dict:
    """Load the category tree from JSON file"""
    global _CATEGORY_TREE
    if _CATEGORY_TREE is None:
        cat_path = Path(__file__).parent / "prompts" / "categories.json"
        with open(cat_path, "r", encoding="utf-8") as f:
            _CATEGORY_TREE = json.load(f)
    return _CATEGORY_TREE


def get_primary_metadata_prompt() -> str:
    if _PROMPTS["primary"] is None:
        _PROMPTS["primary"] = _load_prompt("primary_metadata_extraction_prompt.txt")
    return _PROMPTS["primary"]


def get_bordereau_extraction_prompt() -> str:
    if _PROMPTS["bordereau"] is None:
        _PROMPTS["bordereau"] = _load_prompt("bordereau_extraction_prompt.txt")
    return _PROMPTS["bordereau"]


def get_ask_ai_prompt() -> str:
    if _PROMPTS["ask_ai"] is None:
        _PROMPTS["ask_ai"] = _load_prompt("ask_ai_prompt.txt")
    return _PROMPTS["ask_ai"]


def get_ask_ai_selector_prompt() -> str:
    if _PROMPTS["ask_ai_selector"] is None:
        _PROMPTS["ask_ai_selector"] = _load_prompt("ask_ai_article_selector_prompt.txt")
    return _PROMPTS["ask_ai_selector"]


def get_category_prompt() -> str:
    if _PROMPTS["category"] is None:
        _PROMPTS["category"] = _load_prompt("category_classification_prompt.txt")
    return _PROMPTS["category"]


def get_category_list_formatted() -> str:
    """Format category tree as a readable list for the AI prompt"""
    categories = _load_categories()
    lines = []
    
    for main_cat, subcats in categories.items():
        lines.append(f"\n## {main_cat}")
        for subcat in subcats:
            subcat_name = subcat.get("name", "")
            lines.append(f"\n### {subcat_name}")
            for item_group in subcat.get("subcategories", []):
                item_name = item_group.get("name", "")
                items = item_group.get("items", [])
                lines.append(f"  - **{item_name}**")
                for item in items[:3]:  # Show first 3 examples
                    lines.append(f"    - {item}")
                if len(items) > 3:
                    lines.append(f"    - ... (+{len(items)-3} autres)")
    
    return "\n".join(lines)


class AIService:
    """DeepSeek AI integration for tender analysis - V3 with Smart Article Selection"""
    
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
        max_tokens: int = 8192
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
                temperature=0
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"AI API call failed: {e}")
            return None
    
    def _parse_json_response(self, response: str) -> Optional[Dict[str, Any]]:
        """Parse JSON from AI response, handling markdown code blocks"""
        try:
            json_str = response
            if "```json" in response:
                json_str = response.split("```json")[1].split("```")[0]
            elif "```" in response:
                json_str = response.split("```")[1].split("```")[0]
            return json.loads(json_str.strip())
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI response as JSON: {e}")
            logger.debug(f"Response was: {response[:500]}")
            return None

    # =========================================================================
    # PHASE 1: Primary/Avis Metadata Extraction
    # =========================================================================
    
    def extract_primary_metadata(
        self,
        source_text: str,
        source_label: str,
        source_date: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Extract Phase 1 metadata from any source text.
        
        source_label: WEBSITE, AVIS, RC, CPS
        """
        if not source_text or len(source_text.strip()) < 50:
            logger.warning("Source text too short for primary metadata extraction")
            return None

        logger.info(f"Starting primary metadata extraction (source={source_label})...")

        response = self._call_ai(
            get_primary_metadata_prompt(),
            f"SOURCE_LABEL: {source_label}\n\nTEXTE À ANALYSER:\n\n{source_text[:20000]}",
        )

        if not response:
            return None

        metadata = self._parse_json_response(response)
        if not metadata:
            return None

        logger.info("Primary metadata extraction complete")
        return metadata
    
    def extract_avis_metadata(
        self,
        avis_text: str,
        source_date: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Backward compatible wrapper for AVIS extraction."""
        return self.extract_primary_metadata(avis_text, source_label="AVIS", source_date=source_date)
    
    def is_metadata_complete(self, metadata: Optional[Dict[str, Any]]) -> bool:
        """Check if Phase 1 metadata has all required fields."""
        if not metadata:
            return False
        
        required_fields = [
            "reference_marche",
            "organisme_acheteur", 
            "objet_marche",
        ]
        
        for field in required_fields:
            val = metadata.get(field)
            if not val:
                return False
        
        # Check deadline
        deadline = metadata.get("date_limite_remise_plis", {})
        if not deadline.get("date"):
            return False
        
        return True

    # =========================================================================
    # PHASE 2: Bordereau des Prix Extraction (Direct Full Document Processing)
    # =========================================================================
    
    def extract_bordereau_items_smart(
        self,
        documents: List[Dict],
        existing_lots: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Phase 2: Extract Bordereau des Prix items by feeding full document to AI.
        
        No indexing - just feed the whole CPS (or other doc) directly to AI.
        
        Flow:
        1. Process CPS first (Bordereau is usually in final pages)
        2. Process Excel files (structured data)
        3. Process RC, BPU/DQE files
        4. Stop once items are found
        
        Args:
            documents: List of processed documents with raw_text
            existing_lots: Lot numbers from Phase 1
            
        Returns:
            Dict with lots_articles structure
        """
        logger.info("=" * 60)
        logger.info("🚀 PHASE 2: Direct Bordereau Extraction (No Indexing)")
        logger.info("=" * 60)
        
        all_lots_articles = {}
        processed_count = 0
        primary_source = None
        
        # Define processing order: Excel FIRST → CPS → BPDE → RC → Others
        # Excel files are most likely to contain structured bordereau data
        def get_priority(doc: Dict) -> int:
            fname = doc.get("filename", "").lower()
            doc_type = doc.get("document_type", "").upper()
            
            # Excel files FIRST (highest priority - structured bordereau data)
            if fname.endswith(('.xlsx', '.xls', '.csv')) or doc_type == "BPDE":
                return 0
            # CPS second (Bordereau is usually at the end of CPS)
            if doc_type == "CPS" or "cps" in fname.split('.')[0].lower() or "cahier" in fname:
                return 1
            # BPU/DQE/BQ third
            if doc_type in ["BPU", "DQE", "BQ"]:
                return 2
            # RC fourth
            if doc_type == "RC" or "reglement" in fname or "rc" in fname.split('.')[0].lower():
                return 3
            # Everything else last
            return 10
        
        # Sort documents by priority
        sorted_docs = sorted(documents, key=get_priority)
        
        logger.info(f"📋 Processing order (Excel first): {[d.get('filename', 'unknown') for d in sorted_docs[:5]]}")
        
        for doc in sorted_docs:
            content = doc.get("raw_text", "")
            filename = doc.get("filename", "unknown")
            doc_type = doc.get("document_type", "UNKNOWN")
            
            # Skip empty or too short content
            if not content or len(content.strip()) < 100:
                logger.info(f"⏭ Skipping {filename} (too short)")
                continue
            
            logger.info(f"📄 Processing: {filename} ({doc_type}, {len(content)} chars)")
            processed_count += 1
            
            # DIRECT EXTRACTION - Feed whole document to AI
            result = self._direct_extract(content, filename, doc_type)
            
            if result:
                items_found = sum(len(la.get("articles", [])) for la in result.get("lots_articles", []))
                
                if items_found > 0:
                    logger.info(f"   ✅ Found {items_found} items in {filename}")
                    self._merge_lots_articles(all_lots_articles, result)
                    
                    if primary_source is None:
                        primary_source = filename
                    
                    # If we found substantial items, we can stop
                    total_so_far = sum(len(arts) for arts in all_lots_articles.values())
                    if total_so_far >= 5:
                        logger.info(f"   🎯 Sufficient items found ({total_so_far}), stopping early")
                        break
                else:
                    logger.info(f"   ⚠ No items found in {filename}")
        
        # Build final result
        final_result = {
            "lots_articles": [
                {
                    "numero_lot": lot_num,
                    "articles": articles
                }
                for lot_num, articles in sorted(all_lots_articles.items())
            ]
        }
        
        # Ensure at least empty lots from Phase 1
        if not final_result["lots_articles"] and existing_lots:
            final_result["lots_articles"] = [
                {"numero_lot": lot_num, "articles": []}
                for lot_num in existing_lots
            ]
        
        # Add completeness info
        total_articles = sum(len(la["articles"]) for la in final_result["lots_articles"])
        final_result["_completeness"] = {
            "is_complete": total_articles > 0,
            "total_articles": total_articles,
            "lots_count": len(final_result["lots_articles"]),
            "files_processed": processed_count,
            "primary_source": primary_source
        }
        
        logger.info("=" * 60)
        logger.info(f"✅ Extraction complete: {total_articles} items in {len(final_result['lots_articles'])} lots")
        logger.info("=" * 60)
        
        return final_result
    
    def _has_bordereau_indicators(self, content: str) -> bool:
        """
        Smart pre-check: Detect if document likely contains a Bordereau des Prix.
        Returns True only if strong indicators are present.
        """
        content_lower = content.lower()
        
        # STRONG indicators - must have at least one
        strong_indicators = [
            "bordereau des prix",
            "bordereau des prix - détail estimatif",
            "bordereau des prix detail estimatif",
            "bordereau des prix détail-estimatif",
            "détail estimatif",
            "detail estimatif",
            "b.p.d.e",
            "bpde",
            "prix n°",
            "n° prix",
            "prix unitaire",
            "montant ht",
            "montant ttc",
            "total ht",
            "total ttc",
        ]
        
        has_strong = any(ind in content_lower for ind in strong_indicators)
        if not has_strong:
            return False
        
        # Must also have TABLE structure indicators
        table_indicators = [
            "désignation",
            "designation",
            "unité",
            "unite",
            "quantité",
            "quantite",
            "forfait",
            "ml",
            "m²",
            "m2",
            "m³",
            "m3",
        ]
        
        table_count = sum(1 for ind in table_indicators if ind in content_lower)
        
        # Need at least 2 table structure indicators
        return table_count >= 2
    
    def _direct_extract(
        self,
        content: str,
        source_name: str,
        source_type: str
    ) -> Optional[Dict[str, Any]]:
        """
        Direct extraction: Feed whole document to AI and extract Bordereau items.
        AI will find the Bordereau section and extract items with units and quantities.
        
        Includes smart pre-check to skip documents that don't contain a Bordereau.
        Files already classified as BPDE always go through (they ARE the bordereau).
        """
        # Always process files classified as BPDE (bordereau) — skip indicator check
        is_bpde = source_type.upper() in ("BPDE", "BORDEREAU")
        if not is_bpde and not self._has_bordereau_indicators(content):
            logger.info(f"   ⏭ Skipping {source_name}: No Bordereau indicators found")
            return None
        
        logger.info(f"   🤖 Extracting Bordereau items from {source_name}...")
        
        # Feed the whole document (up to token limit) to extraction AI
        response = self._call_ai(
            get_bordereau_extraction_prompt(),
            f"DOCUMENT: {source_name} ({source_type})\n\nCONTENU COMPLET DU DOCUMENT:\n\n{content[:50000]}",
            max_tokens=8192
        )
        
        if not response:
            logger.warning(f"   ❌ Extraction failed: No response from AI")
            return None
        
        result = self._parse_json_response(response)
        if not result:
            logger.warning(f"   ❌ Extraction failed: Could not parse response")
            return None
        
        # Count items
        total_items = sum(len(la.get("articles", [])) for la in result.get("lots_articles", []))
        logger.info(f"   ✓ Extracted {total_items} items")
        
        if total_items == 0:
            return None
        
        return result
    
    def _merge_lots_articles(
        self,
        target: Dict[str, List],
        source: Dict[str, Any]
    ):
        """Merge lots_articles from source into target, avoiding duplicates."""
        for lot_data in source.get("lots_articles", []):
            lot_num = str(lot_data.get("numero_lot", "1"))
            articles = lot_data.get("articles", [])
            
            if lot_num not in target:
                target[lot_num] = []
            
            # Add articles, avoiding duplicates by numero_prix
            existing_nums = {a.get("numero_prix") for a in target[lot_num]}
            for art in articles:
                if art.get("numero_prix") not in existing_nums:
                    target[lot_num].append(art)
                    existing_nums.add(art.get("numero_prix"))
    
    def extract_bordereau_focused_retry(
        self,
        documents: List[Dict],
        existing_lots: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Focused retry: When initial extraction found no bordereau items,
        do a more thorough search across ALL documents without early stopping.
        
        This method:
        1. Skips the indicator pre-check (force extraction attempt)
        2. Processes ALL documents (no early stopping)
        3. Uses longer context windows
        
        Args:
            documents: List of processed documents with raw_text
            existing_lots: Lot numbers from Phase 1
            
        Returns:
            Dict with lots_articles structure, or None if still nothing found
        """
        logger.info("=" * 60)
        logger.info("🔄 FOCUSED RETRY: Deep Bordereau Search (No Indicators Check)")
        logger.info("=" * 60)
        
        all_lots_articles = {}
        processed_count = 0
        
        # Process ALL documents without skipping based on indicators
        for doc in documents:
            content = doc.get("raw_text", "")
            filename = doc.get("filename", "unknown")
            doc_type = doc.get("document_type", "UNKNOWN")
            
            # Skip empty or too short content
            if not content or len(content.strip()) < 100:
                continue
            
            logger.info(f"📄 [RETRY] Processing: {filename} ({doc_type}, {len(content)} chars)")
            processed_count += 1
            
            # Force extraction WITHOUT indicator check
            logger.info(f"   🤖 Force extracting from {filename}...")
            
            # Feed the whole document (up to token limit) to extraction AI
            response = self._call_ai(
                get_bordereau_extraction_prompt(),
                f"DOCUMENT: {filename} ({doc_type})\n\n"
                f"IMPORTANT: Cherchez TOUT tableau contenant des prix, quantités, unités.\n"
                f"Même si le document ne semble pas être un bordereau des prix standard.\n\n"
                f"CONTENU COMPLET DU DOCUMENT:\n\n{content[:60000]}",
                max_tokens=8192
            )
            
            if response:
                result = self._parse_json_response(response)
                if result:
                    items_found = sum(len(la.get("articles", [])) for la in result.get("lots_articles", []))
                    if items_found > 0:
                        logger.info(f"   ✅ [RETRY] Found {items_found} items in {filename}")
                        self._merge_lots_articles(all_lots_articles, result)
        
        # Build final result
        final_result = {
            "lots_articles": [
                {
                    "numero_lot": lot_num,
                    "articles": articles
                }
                for lot_num, articles in sorted(all_lots_articles.items())
            ]
        }
        
        # Ensure at least empty lots from Phase 1
        if not final_result["lots_articles"] and existing_lots:
            final_result["lots_articles"] = [
                {"numero_lot": lot_num, "articles": []}
                for lot_num in existing_lots
            ]
        
        # Add completeness info
        total_articles = sum(len(la["articles"]) for la in final_result["lots_articles"])
        final_result["_completeness"] = {
            "is_complete": total_articles > 0,
            "total_articles": total_articles,
            "lots_count": len(final_result["lots_articles"]),
            "files_processed": processed_count,
            "is_retry": True
        }
        
        logger.info("=" * 60)
        logger.info(f"🔄 Retry complete: {total_articles} items found")
        logger.info("=" * 60)
        
        return final_result if total_articles > 0 else None

    # Legacy method for backward compatibility
    def extract_bordereau_items(
        self,
        documents: List[ExtractionResult],
        existing_lots: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Legacy Phase 2 extraction - converts ExtractionResult to dict format.
        """
        # Convert ExtractionResult objects to dicts
        doc_dicts = []
        for doc in documents:
            doc_dict = {
                "filename": doc.filename,
                "document_type": doc.document_type.value if hasattr(doc.document_type, 'value') else str(doc.document_type),
                "raw_text": doc.text,
                "article_index": None  # Legacy format doesn't have article index
            }
            doc_dicts.append(doc_dict)
        
        return self.extract_bordereau_items_smart(doc_dicts, existing_lots)

    # =========================================================================
    # PHASE 3: Ask AI (Q&A) — Comprehensive All-Resources Approach
    # =========================================================================
    
    def ask_ai(
        self,
        question: str,
        documents: List[ExtractionResult],
        tender_reference: Optional[str] = None,
        bordereau_metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Phase 3: Answer questions about tender documents.
        
        Strategy: Build the richest possible context from ALL sources, then let 
        the AI cross-reference and find the answer.
        
        Context priority:
        1. Bordereau structured data (items, quantities, units)
        2. Full document texts ordered by relevance to question
        3. All remaining documents
        """
        if not question or not documents:
            return None
        
        logger.info(f"🤖 Ask AI: '{question[:80]}...'")
        logger.info(f"   📚 Available: {len(documents)} documents, bordereau={'yes' if bordereau_metadata else 'no'}")
        
        # === Build comprehensive context ===
        context_parts = []
        total_chars = 0
        MAX_TOTAL_CONTEXT = 60000
        MAX_PER_DOC = 20000
        
        # 1. Bordereau structured data (always first — compact and high-value)
        if bordereau_metadata:
            bdx_text = self._format_bordereau_context(bordereau_metadata)
            if bdx_text:
                context_parts.append(bdx_text)
                total_chars += len(bdx_text)
                logger.info(f"   ✓ Bordereau context: {len(bdx_text)} chars")
        
        # 2. Prioritize documents likely relevant to the question
        prioritized_docs = self._prioritize_docs_for_question(question, documents)
        
        for doc in prioritized_docs:
            if not doc.text or total_chars >= MAX_TOTAL_CONTEXT:
                continue
            
            doc_type = doc.document_type.value if hasattr(doc.document_type, 'value') else str(doc.document_type)
            remaining = MAX_TOTAL_CONTEXT - total_chars
            chars_to_use = min(len(doc.text), MAX_PER_DOC, remaining)
            
            doc_header = f"=== DOCUMENT: {doc_type} — {doc.filename} ==="
            doc_content = doc.text[:chars_to_use]
            
            context_parts.append(f"{doc_header}\n{doc_content}")
            total_chars += chars_to_use + len(doc_header)
            logger.info(f"   ✓ {doc_type}/{doc.filename}: {chars_to_use} chars")
        
        full_context = "\n\n".join(context_parts)
        logger.info(f"   📊 Total context: {len(full_context)} chars from {len(context_parts)} sources")
        
        # === Call AI with comprehensive context ===
        user_prompt = f"""RÉFÉRENCE DU MARCHÉ: {tender_reference or 'N/A'}

QUESTION DE L'UTILISATEUR: {question}

=== DÉBUT DES DOCUMENTS DU DOSSIER ===

{full_context}

=== FIN DES DOCUMENTS ==="""
        
        response = self._call_ai(
            get_ask_ai_prompt(),
            user_prompt,
            max_tokens=4096
        )
        
        if not response:
            return {
                "answer": "Je n'ai pas pu traiter votre demande. Veuillez réessayer.",
                "citations": [],
                "follow_up_questions": [],
                "language": "fr"
            }
        
        result = self._parse_json_response(response)
        if result:
            result.setdefault("citations", [])
            result.setdefault("follow_up_questions", [])
            result.setdefault("language", "fr")
            return result
        
        # Fallback: raw response as answer
        clean = response.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[1:-1]) if len(lines) > 2 else clean
        
        return {
            "answer": clean,
            "citations": [],
            "follow_up_questions": [],
            "language": "fr"
        }
    
    def _format_bordereau_context(self, bordereau_metadata: Dict[str, Any]) -> str:
        """Format bordereau metadata as readable structured text."""
        lots = bordereau_metadata.get("lots_articles", [])
        if not lots:
            return ""
        
        lines = ["=== DOCUMENT: BORDEREAU DES PRIX (données structurées) ==="]
        for lot in lots:
            lot_num = lot.get("numero_lot", lot.get("lot_numero", "Unique"))
            lot_objet = lot.get("objet_lot", "")
            articles = lot.get("articles", [])
            
            header = f"--- Lot {lot_num}"
            if lot_objet:
                header += f": {lot_objet}"
            header += f" ({len(articles)} articles) ---"
            lines.append(header)
            
            for art in articles:
                num = art.get("numero_prix", "")
                desig = art.get("designation", art.get("description", ""))
                qty = art.get("quantite", "")
                unite = art.get("unite", "")
                
                entry = f"  N°{num}: {desig}"
                if qty:
                    entry += f" | Qté: {qty}"
                if unite:
                    entry += f" {unite}"
                lines.append(entry)
            lines.append("")
        
        return "\n".join(lines)
    
    def _prioritize_docs_for_question(
        self, question: str, documents: List[ExtractionResult]
    ) -> List[ExtractionResult]:
        """
        Order documents by likely relevance to the question.
        CPS first for specs/technical, RC for rules, etc.
        All documents are included — just ordered smartly.
        """
        q = question.lower()
        
        def doc_score(doc: ExtractionResult) -> int:
            dt = (doc.document_type.value if hasattr(doc.document_type, 'value') 
                  else str(doc.document_type)).upper()
            score = 0
            
            specs_kw = ["spécification", "technique", "caractéristique", "norme", 
                        "marque", "modèle", "détail", "specification"]
            rules_kw = ["pénalité", "délai", "condition", "clause", "obligation",
                        "résiliation", "garantie", "assurance"]
            items_kw = ["article", "quantité", "prix", "bordereau", "fourniture",
                        "produit", "désignation"]
            submit_kw = ["document", "soumission", "candidature", "pli", "dossier",
                         "pièce", "justificatif"]
            
            if dt == "CPS":
                score += 10
                if any(k in q for k in specs_kw):
                    score += 20
                if any(k in q for k in rules_kw):
                    score += 15
            elif dt == "RC":
                score += 5
                if any(k in q for k in submit_kw):
                    score += 20
                if any(k in q for k in rules_kw):
                    score += 10
            elif dt in ("BPDE", "BPU", "DQE", "BORDEREAU"):
                if any(k in q for k in items_kw):
                    score += 20
            elif dt == "AVIS":
                score += 3
            
            if doc.text:
                score += min(len(doc.text) // 5000, 5)
            
            return score
        
        return sorted(documents, key=doc_score, reverse=True)

    # =========================================================================
    # PHASE 4: Category Classification
    # =========================================================================
    
    def classify_tender_categories(
        self,
        tender_metadata: Dict[str, Any],
        bordereau_items: Optional[List[Dict]] = None
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Phase 4: Classify a tender into categories based on metadata and bordereau items.
        
        Args:
            tender_metadata: Phase 1 metadata (objet_marche, etc.)
            bordereau_items: Optional list of bordereau articles for more precise classification
            
        Returns:
            List of category assignments with confidence scores
        """
        logger.info("=" * 60)
        logger.info("🏷️ PHASE 4: Category Classification")
        logger.info("=" * 60)
        
        # Build context for classification
        context_parts = []
        
        # Add main metadata
        if tender_metadata.get("objet_marche"):
            context_parts.append(f"OBJET DU MARCHÉ: {tender_metadata['objet_marche']}")
        
        if tender_metadata.get("reference_marche"):
            context_parts.append(f"RÉFÉRENCE: {tender_metadata['reference_marche']}")
        
        if tender_metadata.get("organisme_acheteur"):
            buyer = tender_metadata["organisme_acheteur"]
            if isinstance(buyer, dict):
                context_parts.append(f"ACHETEUR: {buyer.get('nom', '')} - {buyer.get('ministere', '')}")
            else:
                context_parts.append(f"ACHETEUR: {buyer}")
        
        # Add lot information
        lots = tender_metadata.get("lots", [])
        if lots:
            context_parts.append(f"\nLOTS ({len(lots)}):")
            for lot in lots[:10]:  # Limit to 10 lots
                context_parts.append(f"  - Lot {lot.get('numero_lot', '?')}: {lot.get('objet_lot', 'N/A')}")
        
        # Add bordereau items if available
        if bordereau_items:
            context_parts.append(f"\nARTICLES DU BORDEREAU ({len(bordereau_items)}):")
            for item in bordereau_items[:20]:  # Limit to 20 items
                designation = item.get("designation", item.get("description", ""))
                context_parts.append(f"  - {designation}")
        
        tender_context = "\n".join(context_parts)
        
        # Get category list for reference
        category_list = get_category_list_formatted()
        
        user_prompt = f"""INFORMATIONS DU MARCHÉ:
{tender_context}

LISTE DES CATÉGORIES DISPONIBLES:
{category_list}

Analyse le marché ci-dessus et attribue les catégories les plus précises.
"""
        
        logger.info(f"Classifying tender: {tender_metadata.get('objet_marche', '')[:50]}...")
        
        response = self._call_ai(
            get_category_prompt(),
            user_prompt,
            max_tokens=2048
        )
        
        if not response:
            logger.warning("Category classification failed: No response")
            return None
        
        result = self._parse_json_response(response)
        if not result:
            logger.warning("Category classification failed: Could not parse response")
            return None
        
        categories = result.get("categories", [])
        
        # Validate categories against the actual category tree
        validated_categories = self._validate_categories(categories)
        
        logger.info(f"✅ Assigned {len(validated_categories)} categories")
        for cat in validated_categories:
            logger.info(f"   - {cat['main_category']} > {cat['subcategory']} > {cat['item']} ({cat['confidence']:.0%})")
        
        return validated_categories
    
    def _validate_categories(self, categories: List[Dict]) -> List[Dict]:
        """Validate and filter categories against the actual category tree with fuzzy matching"""
        category_tree = _load_categories()
        validated = []
        
        for cat in categories:
            main_cat = cat.get("main_category", "")
            subcat = cat.get("subcategory", "")
            item = cat.get("item", "")
            confidence = cat.get("confidence", 0)
            
            # Skip low confidence
            if confidence < 0.5:
                continue
            
            # Fuzzy match main category
            matched_main = self._fuzzy_match_key(main_cat, list(category_tree.keys()))
            if not matched_main:
                logger.warning(f"Invalid main category: {main_cat}")
                continue
            cat["main_category"] = matched_main
            
            # Fuzzy match subcategory
            subcat_names = [sc.get("name", "") for sc in category_tree[matched_main]]
            matched_subcat_name = self._fuzzy_match_key(subcat, subcat_names)
            
            found_subcat = False
            found_item = False
            matched_sc = None
            
            if matched_subcat_name:
                found_subcat = True
                cat["subcategory"] = matched_subcat_name
                # Find the actual subcategory object
                for sc in category_tree[matched_main]:
                    if sc.get("name") == matched_subcat_name:
                        matched_sc = sc
                        break
            else:
                # Try all subcategories for a partial match
                for sc in category_tree[matched_main]:
                    sc_name = sc.get("name", "")
                    if (subcat.lower() in sc_name.lower() or 
                        sc_name.lower() in subcat.lower()):
                        found_subcat = True
                        cat["subcategory"] = sc_name
                        matched_sc = sc
                        break
            
            # Fuzzy match item within subcategory
            if found_subcat and matched_sc and item:
                item_group_names = [ig.get("name", "") for ig in matched_sc.get("subcategories", [])]
                matched_item = self._fuzzy_match_key(item, item_group_names)
                if matched_item:
                    cat["item"] = matched_item
                    found_item = True
                else:
                    # Check in items lists
                    for item_group in matched_sc.get("subcategories", []):
                        all_items = item_group.get("items", [])
                        matched_in_items = self._fuzzy_match_key(item, all_items)
                        if matched_in_items:
                            cat["item"] = item_group.get("name", item)
                            found_item = True
                            break
            
            if found_subcat and found_item:
                validated.append(cat)
            elif found_subcat:
                cat["item"] = None
                validated.append(cat)
            else:
                logger.warning(f"Category not found in tree: {main_cat} > {subcat} > {item}")
        
        # Remove duplicates
        seen = set()
        unique = []
        for cat in validated:
            key = (cat["main_category"], cat["subcategory"], cat.get("item"))
            if key not in seen:
                seen.add(key)
                unique.append(cat)
        
        return unique[:5]  # Max 5 categories
    
    @staticmethod
    def _fuzzy_match_key(needle: str, haystack: List[str], threshold: float = 0.6) -> Optional[str]:
        """Simple fuzzy matching: exact → lowercase → containment → word overlap"""
        if not needle or not haystack:
            return None
        
        needle_lower = needle.lower().strip()
        
        # Exact match
        for h in haystack:
            if h == needle:
                return h
        
        # Case-insensitive match
        for h in haystack:
            if h.lower().strip() == needle_lower:
                return h
        
        # Containment match
        for h in haystack:
            h_lower = h.lower().strip()
            if needle_lower in h_lower or h_lower in needle_lower:
                return h
        
        # Word overlap (Jaccard-like)
        needle_words = set(re.findall(r'\w{3,}', needle_lower))
        if not needle_words:
            return None
        
        best_match = None
        best_score = 0
        for h in haystack:
            h_words = set(re.findall(r'\w{3,}', h.lower()))
            if not h_words:
                continue
            overlap = len(needle_words & h_words) / len(needle_words | h_words)
            if overlap > best_score and overlap >= threshold:
                best_score = overlap
                best_match = h
        
        return best_match


# Singleton instance
ai_service = AIService()
