"""
Tender AI Platform - AI-Based File Detection Service
Uses AI to analyze filenames and detect which files likely contain Bordereau des Prix.
"""

import json
from typing import List, Dict, Optional, Tuple
from loguru import logger
from openai import OpenAI

from app.core.config import settings


# Prompt for AI-based filename detection
FILE_DETECTION_PROMPT = """Tu es un expert en marchés publics marocains. Analyse les noms de fichiers suivants pour identifier lesquels contiennent probablement le "Bordereau des Prix" (liste des articles avec prix, quantités, unités).

RÈGLES D'IDENTIFICATION:

1. **Fichiers Bordereau des Prix** (priorité 1):
   - Noms contenant: "bordereau", "BDP", "BPDE", "prix", "detail estimatif", "détail estimatif", "DQE", "BPU", "devis", "quantitatif"
   - Extensions Excel: .xlsx, .xls, .csv (très haute probabilité de contenir des tableaux de prix)

2. **Fichiers CPS** (priorité 2, fallback si pas de bordereau):
   - Noms contenant: "CPS", "cahier des prescriptions", "cahier des charges"
   - Le CPS contient souvent le bordereau dans ses dernières pages

3. **Autres fichiers** (priorité 3):
   - RC (règlement de consultation)
   - Avis, annexes, etc.

RÉPONSE REQUISE:
Retourne un JSON avec la structure suivante:
```json
{
    "bordereau_files": ["fichier1.xlsx", "fichier2.pdf"],
    "cps_files": ["CPS.pdf"],
    "other_files": ["RC.pdf", "avis.pdf"],
    "analysis": "Explication courte de ton analyse"
}
```

IMPORTANT:
- Si un fichier Excel existe, il est TRÈS probablement le bordereau
- Les fichiers avec "prix", "bordereau", "BDP", "BPDE" sont prioritaires
- Si aucun fichier bordereau n'est identifié, le CPS sera utilisé comme source principale
- Classe TOUS les fichiers dans une des trois catégories
"""


class FileDetector:
    """AI-based file detection for identifying Bordereau des Prix files."""
    
    def __init__(self):
        self.client = OpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL
        )
        self.model = settings.DEEPSEEK_MODEL
    
    def detect_bordereau_files(
        self,
        filenames: List[str]
    ) -> Tuple[List[str], List[str], List[str]]:
        """
        Use AI to analyze filenames and categorize them.
        
        Args:
            filenames: List of all filenames in the tender
            
        Returns:
            Tuple of (bordereau_files, cps_files, other_files)
        """
        if not filenames:
            return [], [], []
        
        # Filter out hidden/temp files
        valid_files = [
            f for f in filenames 
            if not f.split('/')[-1].startswith(('~$', '.', '__'))
        ]
        
        if not valid_files:
            return [], [], []
        
        logger.info(f"🔍 AI analyzing {len(valid_files)} filenames for Bordereau detection...")
        
        # Format filenames for AI
        file_list = "\n".join(f"- {f}" for f in valid_files)
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": FILE_DETECTION_PROMPT},
                    {"role": "user", "content": f"FICHIERS À ANALYSER:\n\n{file_list}"}
                ],
                max_tokens=2048,
                temperature=0
            )
            
            result_text = response.choices[0].message.content
            
            # Parse JSON response
            json_str = result_text
            if "```json" in result_text:
                json_str = result_text.split("```json")[1].split("```")[0]
            elif "```" in result_text:
                json_str = result_text.split("```")[1].split("```")[0]
            
            result = json.loads(json_str.strip())
            
            bordereau_files = result.get("bordereau_files", [])
            cps_files = result.get("cps_files", [])
            other_files = result.get("other_files", [])
            analysis = result.get("analysis", "")
            
            logger.info(f"📋 AI Detection Result:")
            logger.info(f"   - Bordereau files: {bordereau_files}")
            logger.info(f"   - CPS files: {cps_files}")
            logger.info(f"   - Other files: {len(other_files)} files")
            if analysis:
                logger.info(f"   - Analysis: {analysis}")
            
            return bordereau_files, cps_files, other_files
            
        except Exception as e:
            logger.error(f"AI file detection failed: {e}")
            # Fallback to rule-based detection
            return self._fallback_detection(valid_files)
    
    def _fallback_detection(
        self,
        filenames: List[str]
    ) -> Tuple[List[str], List[str], List[str]]:
        """
        Rule-based fallback when AI detection fails.
        """
        logger.info("Using fallback rule-based file detection")
        
        bordereau_files = []
        cps_files = []
        other_files = []
        
        bordereau_keywords = [
            'bordereau', 'bdp', 'bpde', 'prix', 'detail estimatif',
            'détail estimatif', 'dqe', 'bpu', 'devis', 'quantitatif'
        ]
        
        cps_keywords = ['cps', 'cahier des prescriptions', 'cahier des charges']
        
        for filename in filenames:
            name_lower = filename.lower()
            base_name = name_lower.split('/')[-1]  # Get just the filename
            
            # Excel files are high priority bordereau candidates
            if base_name.endswith(('.xlsx', '.xls', '.csv')):
                bordereau_files.append(filename)
            # Check for bordereau keywords
            elif any(kw in name_lower for kw in bordereau_keywords):
                bordereau_files.append(filename)
            # Check for CPS keywords
            elif any(kw in name_lower for kw in cps_keywords):
                cps_files.append(filename)
            else:
                other_files.append(filename)
        
        logger.info(f"📋 Fallback Detection Result:")
        logger.info(f"   - Bordereau files: {bordereau_files}")
        logger.info(f"   - CPS files: {cps_files}")
        
        return bordereau_files, cps_files, other_files


def detect_and_prioritize_files(
    filenames: List[str]
) -> Tuple[List[str], List[str], List[str]]:
    """
    Convenience function to detect and categorize files.
    
    Returns:
        Tuple of (bordereau_files, cps_files, other_files)
    """
    detector = FileDetector()
    return detector.detect_bordereau_files(filenames)
