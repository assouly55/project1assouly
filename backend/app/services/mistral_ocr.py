"""
Tender AI Platform - Mistral OCR Service
Uses Mistral Vision API for OCR on scanned PDFs
"""

import io
import base64
import httpx
from typing import Tuple, List
from loguru import logger

from app.core.config import settings


def _detect_and_fix_orientation_pil(image):
    """
    Detect page orientation and rotate if needed.
    Uses Tesseract OSD for detection.
    """
    try:
        import pytesseract
        
        # Use OSD to detect rotation
        osd_data = pytesseract.image_to_osd(image, output_type=pytesseract.Output.DICT)
        rotation_angle = osd_data.get('rotate', 0)
        orientation_conf = osd_data.get('orientation_conf', 0)
        
        if rotation_angle != 0 and orientation_conf > 1.0:
            logger.info(f"Auto-rotating page by {rotation_angle}° (confidence: {orientation_conf:.1f})")
            image = image.rotate(-rotation_angle, expand=True)
        
        return image
    except Exception as e:
        logger.debug(f"Orientation detection skipped: {e}")
        return image


def _pdf_page_to_base64(file_bytes: io.BytesIO, page_num: int = 1, fix_orientation: bool = True) -> str:
    """
    Convert a specific PDF page to base64 image for Mistral Vision API.
    Uses pdf2image for conversion with automatic orientation detection.
    """
    from pdf2image import convert_from_bytes
    
    file_bytes.seek(0)
    pdf_bytes = file_bytes.read()
    
    # Convert specific page to image
    images = convert_from_bytes(
        pdf_bytes,
        dpi=200,
        first_page=page_num,
        last_page=page_num
    )
    
    if not images:
        return ""
    
    image = images[0]
    
    # Detect and fix orientation for scanned landscape documents
    if fix_orientation:
        image = _detect_and_fix_orientation_pil(image)
    
    # Convert PIL image to base64
    import io as io_module
    img_buffer = io_module.BytesIO()
    image.save(img_buffer, format='PNG')
    img_buffer.seek(0)
    
    return base64.b64encode(img_buffer.read()).decode('utf-8')


def _call_mistral_ocr(image_base64: str, page_info: str = "") -> str:
    """
    Call Mistral Vision API to extract text from image.
    
    Args:
        image_base64: Base64 encoded image
        page_info: Optional page info for context
    
    Returns:
        Extracted text from the image
    """
    if not settings.MISTRAL_API_KEY:
        logger.error("MISTRAL_API_KEY not configured")
        return "[OCR FAILED: Mistral API key not configured]"
    
    try:
        headers = {
            "Authorization": f"Bearer {settings.MISTRAL_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": settings.MISTRAL_OCR_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"""Extrait TOUT le texte visible dans cette image de document. 
{page_info}

Instructions CRITIQUES pour les tableaux (Bordereau des Prix):
- IDENTIFIE d'abord les EN-TÊTES de colonnes du tableau
- L'ordre typique est: N° | Désignation | Unité | Quantité | Prix Unitaire
- MAIS certains tableaux peuvent avoir un ordre différent - VÉRIFIE les en-têtes!
- Utilise | comme séparateur de colonnes en RESPECTANT L'ORDRE DES EN-TÊTES
- Extrait CHAQUE ligne du tableau - si le tableau va de 1 à 28, extrais les 28 lignes
- NE SAUTE AUCUNE LIGNE même si le texte est difficile à lire

Instructions générales:
- Si l'image semble être en orientation paysage ou tournée, lis le texte dans le bon sens
- Extrait le texte exactement comme il apparaît
- Préserve la structure (paragraphes, listes, tableaux)
- Inclus tout texte en français, arabe ou anglais
- N'ajoute aucune interprétation, seulement le texte brut

Texte extrait:"""
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_base64}"
                            }
                        }
                    ]
                }
            ],
            "max_tokens": 8192,  # Increased for larger documents
            "temperature": 0
        }
        
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                settings.MISTRAL_API_URL,
                headers=headers,
                json=payload
            )
            
            if response.status_code != 200:
                logger.error(f"Mistral API error: {response.status_code} - {response.text}")
                return f"[OCR FAILED: Mistral API error {response.status_code}]"
            
            result = response.json()
            extracted_text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            return extracted_text.strip()
            
    except Exception as e:
        logger.error(f"Mistral OCR call failed: {e}")
        return f"[OCR FAILED: {str(e)}]"


def ocr_first_page_pdf_mistral(file_bytes: io.BytesIO) -> str:
    """
    OCR only the first page of a scanned PDF using Mistral Vision API.
    
    Args:
        file_bytes: PDF file content as BytesIO
    
    Returns:
        Extracted text from first page
    """
    logger.info("Running Mistral OCR on first page...")
    
    try:
        image_base64 = _pdf_page_to_base64(file_bytes, page_num=1)
        
        if not image_base64:
            logger.error("Could not convert PDF first page to image")
            return ""
        
        text = _call_mistral_ocr(image_base64, "Page 1 d'un document d'appel d'offres.")
        
        logger.info(f"Mistral OCR extracted {len(text)} chars from first page")
        return text
        
    except Exception as e:
        logger.error(f"Mistral first-page OCR failed: {e}")
        return ""


def ocr_full_pdf_mistral(file_bytes: io.BytesIO) -> Tuple[str, int]:
    """
    Full OCR extraction from scanned PDF using Mistral Vision API.
    Processes all pages.
    
    Args:
        file_bytes: PDF file content as BytesIO
    
    Returns:
        Tuple of (extracted_text, page_count)
    """
    from pdf2image import convert_from_bytes
    
    logger.info("Full OCR extraction starting (Mistral Vision API)...")
    
    try:
        file_bytes.seek(0)
        pdf_bytes = file_bytes.read()
        
        # Get page count first
        images = convert_from_bytes(pdf_bytes, dpi=150)  # Lower DPI for counting
        page_count = len(images)
        
        if not images:
            logger.error("Could not convert PDF to images")
            return "[OCR FAILED: No images extracted]", 0
        
        logger.info(f"Processing {page_count} pages with Mistral OCR...")
        
        all_text = []
        
        # Process each page
        for i in range(1, page_count + 1):
            logger.info(f"OCR page {i}/{page_count}...")
            
            # Get base64 for this page
            file_bytes.seek(0)
            image_base64 = _pdf_page_to_base64(file_bytes, page_num=i)
            
            if not image_base64:
                all_text.append(f"--- Page {i} ---\n[Page conversion failed]")
                continue
            
            page_text = _call_mistral_ocr(
                image_base64, 
                f"Page {i}/{page_count} d'un document d'appel d'offres marocain."
            )
            
            all_text.append(f"--- Page {i} ---\n{page_text}")
        
        logger.info(f"Mistral OCR completed: {page_count} pages")
        return "\n\n".join(all_text).strip(), page_count
        
    except Exception as e:
        logger.error(f"Full Mistral OCR failed: {e}")
        return f"[OCR FAILED: {str(e)}]", 0
