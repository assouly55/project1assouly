"""
Tender AI Platform - Headless Scraper Service
Refactored from tkinter GUI version to headless, memory-only operation
"""

import asyncio
import io
import zipfile
from datetime import datetime, timedelta
from typing import List, Tuple, Dict, Optional, Callable
from dataclasses import dataclass, field
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from loguru import logger

from app.core.config import settings


@dataclass
class ScraperProgress:
    """Progress tracking for scraper operations"""
    phase: str = "Initializing"
    total: int = 0
    downloaded: int = 0
    failed: int = 0
    elapsed_seconds: float = 0
    logs: List[Dict] = field(default_factory=list)
    is_running: bool = False
    
    def log(self, level: str, message: str):
        """Add log entry"""
        self.logs.append({
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": message
        })
        # Also log to console
        getattr(logger, level)(message)


@dataclass
class WebsiteMetadata:
    """Metadata extracted directly from tender webpage (authoritative source)"""
    reference_tender: Optional[str] = None
    submission_deadline_date: Optional[str] = None  # DD/MM/YYYY
    submission_deadline_time: Optional[str] = None  # HH:MM
    subject: Optional[str] = None

    # Full visible text extracted from "page de consultation" (used for AI parsing)
    consultation_text: Optional[str] = None
    
    # Lot details popup text (for multi-lot tenders)
    lots_popup_text: Optional[str] = None

    # Extended metadata (from expanded panel)
    acheteur_public: Optional[str] = None  # Buyer/purchasing entity
    lieu_execution: Optional[str] = None  # Execution location
    estimation_ttc: Optional[str] = None  # Estimated value in DHS TTC
    lieu_ouverture_plis: Optional[str] = None  # Bid opening location
    caution_provisoire: Optional[str] = None  # Provisional guarantee
    contact_administratif: Optional[str] = None  # Administrative contact (raw text)
    
    def is_complete(self) -> bool:
        """Check if all critical fields are present (no fallback needed)"""
        critical_fields = [
            self.reference_tender,
            self.subject,
            self.submission_deadline_date,
            self.acheteur_public or self.lieu_execution,
            self.estimation_ttc,
        ]
        return all(f is not None and str(f).strip() for f in critical_fields)


@dataclass
class ScrapedTender:
    """Tender result from website scraping (no ZIP download yet)"""
    index: int
    url: str
    success: bool
    error: str = ""
    website_metadata: Optional[WebsiteMetadata] = None
    
    def needs_document_download(self) -> bool:
        """Check if we need to download documents for fallback"""
        if not self.website_metadata:
            return True
        return not self.website_metadata.is_complete()


@dataclass
class DownloadedTender:
    """In-memory tender download result"""
    index: int
    url: str
    success: bool
    error: str = ""
    # In-memory ZIP content
    zip_bytes: Optional[bytes] = None
    suggested_filename: str = ""
    # Website metadata (authoritative for reference, deadline, subject)
    website_metadata: Optional[WebsiteMetadata] = None
    
    def get_files(self) -> Dict[str, io.BytesIO]:
        """Extract files from ZIP to memory"""
        if not self.zip_bytes:
            return {}
        
        files = {}
        try:
            with zipfile.ZipFile(io.BytesIO(self.zip_bytes), 'r') as zf:
                for name in zf.namelist():
                    # Skip directories
                    if name.endswith('/'):
                        continue
                    files[name] = io.BytesIO(zf.read(name))
        except Exception as e:
            logger.error(f"Failed to extract ZIP: {e}")
        
        return files


class TenderScraper:
    """
    Headless tender scraper for marchespublics.gov.ma
    All downloads are kept in memory (io.BytesIO)
    """
    
    def __init__(
        self,
        on_progress: Optional[Callable[[ScraperProgress], None]] = None
    ):
        self.progress = ScraperProgress()
        self.on_progress = on_progress
        self._stop_requested = False
        
    def _update_progress(self):
        """Notify progress listeners"""
        if self.on_progress:
            self.on_progress(self.progress)
    
    def stop(self):
        """Request graceful stop"""
        self._stop_requested = True
        self.progress.log("warning", "Stop requested...")
        
    async def collect_tender_links(
        self, 
        page, 
        start_date: str, 
        end_date: Optional[str] = None
    ) -> List[str]:
        """
        Navigate to search page and collect tender URLs
        
        Args:
            page: Playwright page instance
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format (defaults to start_date)
        
        Returns:
            List of tender URLs
        """
        # Use same date if end_date not provided
        if not end_date:
            end_date = start_date
            
        # Convert date format (YYYY-MM-DD to DD/MM/YYYY)
        def format_date(date_str: str) -> str:
            parts = date_str.split('-')
            return f"{parts[2]}/{parts[1]}/{parts[0]}"
        
        formatted_start = format_date(start_date)
        formatted_end = format_date(end_date)
        
        self.progress.log("info", f"Date de mise en ligne: {formatted_start} → {formatted_end}")
        self.progress.log("info", "Category: Fournitures (2)")
        
        # Navigate to homepage
        await page.goto(settings.TARGET_HOMEPAGE)
        
        # Click search tab
        await page.click('text=Consultations en cours')
        
        # Set category filter
        await page.select_option(
            '#ctl0_CONTENU_PAGE_AdvancedSearch_categorie', 
            value=settings.CATEGORY_FILTER
        )
        
        # Set date range (start and end dates)
        section_locator = page.locator('text="Date de mise en ligne :"').locator('..')
        await section_locator.locator('input').nth(0).fill(formatted_start)
        await section_locator.locator('input').nth(1).fill(formatted_end)
        
        # Clear deadline date fields
        section_limite = page.locator('text="Date limite de remise des plis :"').locator('..')
        for i in range(2):
            input_field = section_limite.locator('input').nth(i)
            await input_field.click()
            await page.keyboard.press('Control+A')
            await page.keyboard.press('Delete')
        
        # Execute search
        await page.locator('input[title="Lancer la recherche"]').nth(0).click()
        await page.wait_for_load_state("networkidle")
        
        # Try to set page size to 500 (optional - may not exist on all pages)
        try:
            page_size_selector = '#ctl0_CONTENU_PAGE_resultSearch_listePageSizeTop'
            await page.wait_for_selector(page_size_selector, timeout=5000)
            await page.select_option(page_size_selector, value="500")
            self.progress.log("info", "Set page size to 500")
        except PlaywrightTimeout:
            self.progress.log("warning", "Page size selector not found, continuing with default")
        
        # Wait for results
        try:
            await page.wait_for_selector(
                "a[href*='EntrepriseDetailConsultation']", 
                timeout=20000
            )
        except PlaywrightTimeout:
            self.progress.log("info", "No tenders posted for this date range")
            self.progress.log("success", "Search completed - 0 tenders found")
            return []
        
        # Extract all tender links
        all_links = await page.eval_on_selector_all(
            "a", 
            "els => els.map(el => el.href)"
        )
        
        # Filter and deduplicate
        tender_links = list(set(
            link for link in all_links 
            if link and link.startswith(settings.TARGET_LINK_PREFIX)
        ))
        
        self.progress.log("success", f"Found {len(tender_links)} tender links")
        return tender_links
    
    async def extract_website_metadata(self, page) -> WebsiteMetadata:
        """
        Extract metadata directly from tender HTML page (page de consultation).
        This data is AUTHORITATIVE and overrides document values.
        
        Clicks on expansion button to reveal additional data before extraction.
        Also clicks on "Détail des lots" button to get lot details popup.
        
        HTML selectors:
        - Reference: span#ctl0_CONTENU_PAGE_idEntrepriseConsultationSummary_reference
        - Deadline: span#ctl0_CONTENU_PAGE_idEntrepriseConsultationSummary_dateHeureLimiteRemisePlis
        - Subject: span#ctl0_CONTENU_PAGE_idEntrepriseConsultationSummary_objet
        - Acheteur public: span#ctl0_CONTENU_PAGE_idEntrepriseConsultationSummary_entiteAchat
        - Lieu d'exécution: span#ctl0_CONTENU_PAGE_idEntrepriseConsultationSummary_lieuxExecutions
        - Estimation: span containing class 'content-bloc' in estimation section
        - Lieu ouverture plis: span#ctl0_CONTENU_PAGE_idEntrepriseConsultationSummary_lieuOuverturePlis
        - Caution provisoire: span#ctl0_CONTENU_PAGE_idEntrepriseConsultationSummary_cautionProvisoire
        - Contact administratif: extracted from contact section
        """
        metadata = WebsiteMetadata()
        
        try:
            # First, click the expansion button to reveal hidden data
            try:
                toggle = page.locator('a.title-toggle[onclick*="infosPrincipales"]').first
                if await toggle.count() > 0:
                    await toggle.click(force=True)
                    # Wait a bit for JS toggle + layout
                    await page.wait_for_timeout(800)
                    logger.debug("Clicked infosPrincipales toggle")
            except Exception as e:
                logger.debug(f"Could not click infosPrincipales toggle: {e}")

            # Click on "Détail des lots" (multi-lot tenders)
            # On this website the button usually triggers a JS popUp(...) that opens a small popup tab/window.
            # We handle BOTH cases:
            # 1) click and capture the popup via expect_popup()
            # 2) derive the PopUpDetailLots URL from href/onclick and navigate directly
            try:
                popup_clickable = None

                # 1) Most reliable: find any <a> that references PopUpDetailLots
                popup_link = page.locator(
                    'a[onclick*="commun.PopUpDetailLots"], a[href*="commun.PopUpDetailLots"], '
                    'a[onclick*="PopUpDetailLots"], a[href*="PopUpDetailLots"]'
                ).first
                if await popup_link.count() > 0:
                    popup_clickable = popup_link

                # 2) Fallback: the picto-details image ("Détail des lots")
                if popup_clickable is None:
                    img = page.locator('img[alt*="Détail des lots"], img[src*="picto-details"]').first
                    if await img.count() > 0:
                        a_parent = img.locator('xpath=ancestor::a[1]')
                        popup_clickable = a_parent if await a_parent.count() > 0 else img

                if popup_clickable is None:
                    logger.debug("No lots popup trigger found on page")
                else:
                    popup_page = None

                    # Try: click and capture the popup tab/window
                    try:
                        async with page.expect_popup(timeout=6000) as popup_info:
                            await popup_clickable.click(force=True)
                        popup_page = await popup_info.value
                        logger.info("Lots popup opened via click (expect_popup)")
                    except PlaywrightTimeout:
                        logger.info("No popup event after click; will try direct navigation to PopUpDetailLots URL")
                    except Exception as e:
                        logger.debug(f"Popup click/expect_popup failed: {e}")

                    # Fallback: derive popup URL from href/onclick (works even if click doesn't open a popup)
                    if popup_page is None:
                        href = await popup_clickable.get_attribute("href")
                        onclick = await popup_clickable.get_attribute("onclick")
                        attr = " ".join([s for s in [href, onclick] if s])

                        popup_url = None
                        try:
                            import re
                            # popUp('index.php?page=commun.PopUpDetailLots...','yes')
                            m = re.search(r"popUp\s*\(\s*['\"]([^'\"]+)['\"]", attr or "", re.IGNORECASE)
                            if m:
                                popup_url = m.group(1)
                            else:
                                # Sometimes the URL exists directly in href/onclick
                                m2 = re.search(
                                    r"(index\.php\?page=commun\.PopUpDetailLots[^'\"\s\)]*)",
                                    attr or "",
                                    re.IGNORECASE,
                                )
                                if m2:
                                    popup_url = m2.group(1)
                        except Exception as parse_err:
                            logger.debug(f"Could not parse PopUpDetailLots URL from attributes: {parse_err}")

                        if popup_url:
                            from urllib.parse import urljoin
                            popup_url = urljoin(page.url, popup_url)
                            logger.info(f"Derived lots popup URL: {popup_url}")

                            popup_page = await page.context.new_page()
                            await popup_page.goto(popup_url, wait_until="domcontentloaded", timeout=30000)

                    # Scrape popup text (if we got a page)
                    if popup_page is not None:
                        try:
                            await popup_page.wait_for_load_state("domcontentloaded")
                            await popup_page.wait_for_timeout(800)

                            # Debug screenshot to verify the popup content is present
                            try:
                                await popup_page.screenshot(path="/tmp/lots_popup_debug.png", full_page=True)
                                logger.info("Saved lots popup screenshot to /tmp/lots_popup_debug.png")
                            except Exception as ss_err:
                                logger.debug(f"Could not save popup screenshot: {ss_err}")

                            popup_text = ""
                            try:
                                popup_text = (await popup_page.inner_text("body")).strip()
                            except Exception:
                                popup_text = (await popup_page.evaluate("() => document.body?.innerText || ''")).strip()

                            if popup_text and len(popup_text) > 50:
                                metadata.lots_popup_text = popup_text
                                logger.info(f"Successfully scraped {len(popup_text)} chars from lots popup")
                            else:
                                logger.warning("Lots popup had no meaningful text")
                        finally:
                            # Always close the popup page to avoid leaking tabs
                            try:
                                await popup_page.close()
                            except Exception:
                                pass

            except Exception as lots_err:
                logger.debug(f"Error processing Détail des lots: {lots_err}")

            # Capture consultation text (after expansion) for AI parsing
            try:
                root = await page.query_selector('#ctl0_CONTENU_PAGE_idEntrepriseConsultationSummary')
                if root:
                    metadata.consultation_text = (await root.inner_text()).strip()
                else:
                    # Fallback: capture the whole body text (can be noisier but more robust)
                    metadata.consultation_text = (await page.inner_text('body')).strip()
            except Exception as e:
                logger.debug(f"Could not capture consultation_text: {e}")

            # IMPORTANT: Put lots text near the TOP so it doesn't get truncated by the AI 20k char limit.
            if metadata.lots_popup_text:
                metadata.consultation_text = (
                    "=== DÉTAIL DES LOTS ===\n"
                    + metadata.lots_popup_text
                    + "\n\n=== PAGE DE CONSULTATION ===\n"
                    + (metadata.consultation_text or "")
                )

            # Extract reference
            ref_selector = '#ctl0_CONTENU_PAGE_idEntrepriseConsultationSummary_reference'
            ref_element = await page.query_selector(ref_selector)
            if ref_element:
                metadata.reference_tender = (await ref_element.inner_text()).strip()
                logger.debug(f"Extracted reference: {metadata.reference_tender}")
            
            # Extract deadline (format: DD/MM/YYYY HH:MM)
            deadline_selector = '#ctl0_CONTENU_PAGE_idEntrepriseConsultationSummary_dateHeureLimiteRemisePlis'
            deadline_element = await page.query_selector(deadline_selector)
            if deadline_element:
                deadline_text = await deadline_element.inner_text()
                if deadline_text:
                    # Parse "DD/MM/YYYY HH:MM" format
                    parts = deadline_text.strip().split(' ')
                    if len(parts) >= 1:
                        metadata.submission_deadline_date = parts[0]  # DD/MM/YYYY
                    if len(parts) >= 2:
                        metadata.submission_deadline_time = parts[1]  # HH:MM
                    logger.debug(f"Extracted deadline: {deadline_text}")
            
            # Extract subject
            subject_selector = '#ctl0_CONTENU_PAGE_idEntrepriseConsultationSummary_objet'
            subject_element = await page.query_selector(subject_selector)
            if subject_element:
                metadata.subject = (await subject_element.inner_text()).strip()
                logger.debug(f"Extracted subject: {metadata.subject[:100] if metadata.subject else 'None'}...")
            
            # === EXTENDED METADATA (from expanded panel) ===
            
            # Acheteur public (buyer/purchasing entity)
            acheteur_selector = '#ctl0_CONTENU_PAGE_idEntrepriseConsultationSummary_entiteAchat'
            acheteur_element = await page.query_selector(acheteur_selector)
            if acheteur_element:
                metadata.acheteur_public = (await acheteur_element.inner_text()).strip()
                logger.debug(f"Extracted acheteur_public: {metadata.acheteur_public}")
            
            # Lieu d'exécution (execution location)
            lieu_exec_selector = '#ctl0_CONTENU_PAGE_idEntrepriseConsultationSummary_lieuxExecutions'
            lieu_exec_element = await page.query_selector(lieu_exec_selector)
            if lieu_exec_element:
                metadata.lieu_execution = (await lieu_exec_element.inner_text()).strip()
                logger.debug(f"Extracted lieu_execution: {metadata.lieu_execution}")
            
            # Estimation (en Dhs TTC) - look for the content-bloc span
            estimation_selector = '#ctl0_CONTENU_PAGE_idEntrepriseConsultationSummary_idReferentielZoneText_RepeaterReferentielZoneText_ctl0_labelReferentielZoneText'
            estimation_element = await page.query_selector(estimation_selector)
            if estimation_element:
                metadata.estimation_ttc = (await estimation_element.inner_text()).strip()
                logger.debug(f"Extracted estimation_ttc: {metadata.estimation_ttc}")
            
            # Lieu d'ouverture des plis (bid opening location)
            lieu_ouv_selector = '#ctl0_CONTENU_PAGE_idEntrepriseConsultationSummary_lieuOuverturePlis'
            lieu_ouv_element = await page.query_selector(lieu_ouv_selector)
            if lieu_ouv_element:
                metadata.lieu_ouverture_plis = (await lieu_ouv_element.inner_text()).strip()
                logger.debug(f"Extracted lieu_ouverture_plis: {metadata.lieu_ouverture_plis}")
            
            # Caution provisoire (provisional guarantee)
            caution_selector = '#ctl0_CONTENU_PAGE_idEntrepriseConsultationSummary_cautionProvisoire'
            caution_element = await page.query_selector(caution_selector)
            if caution_element:
                metadata.caution_provisoire = (await caution_element.inner_text()).strip()
                logger.debug(f"Extracted caution_provisoire: {metadata.caution_provisoire}")
            
            # Contact Administratif
            # Prefer the user-provided XPath (if it exists), otherwise fallback to text parsing.
            try:
                xpath = '/html/body/form/div[3]/div[2]/div[4]/div[2]/div[1]/div[4]/div[16]/div[2]'
                loc = page.locator(f'xpath={xpath}')
                if await loc.count() > 0:
                    txt = (await loc.first.inner_text()).strip()
                    if txt:
                        metadata.contact_administratif = txt
                        logger.debug(f"Extracted contact_administratif (xpath): {txt[:100]}...")
            except Exception as e:
                logger.debug(f"XPath contact extraction failed: {e}")

            # Fallback: heuristic extraction from consultation_text
            if (not metadata.contact_administratif) and metadata.consultation_text:
                try:
                    t = metadata.consultation_text
                    # Normalize spacing
                    t_norm = "\n".join([ln.strip() for ln in t.splitlines() if ln.strip()])
                    # Try to capture the block after 'Contact administratif'
                    import re
                    m = re.search(r"contact\s+administratif\s*[:\-]?\s*(.+)$", t_norm, re.IGNORECASE)
                    if m:
                        guess = m.group(1).strip()
                        if len(guess) > 10:
                            metadata.contact_administratif = guess
                            logger.debug(f"Extracted contact_administratif (text): {guess[:100]}...")
                except Exception as e:
                    logger.debug(f"Heuristic contact extraction failed: {e}")
        except Exception as e:
            logger.warning(f"Failed to extract website metadata: {e}")
        
        return metadata
    
    async def scrape_single_tender(
        self,
        context,
        tender_url: str,
        idx: int,
        semaphore: asyncio.Semaphore
    ) -> ScrapedTender:
        """
        Scrape a single tender page WITHOUT downloading the ZIP.
        Only extracts website metadata.
        
        Returns:
            ScrapedTender with website metadata (no ZIP bytes)
        """
        async with semaphore:
            if self._stop_requested:
                return ScrapedTender(idx, tender_url, False, "Stopped by user")
            
            tender_page = None
            website_metadata = None
            try:
                tender_page = await context.new_page()
                
                # Navigate to tender page
                await tender_page.goto(
                    tender_url, 
                    timeout=settings.SCRAPER_TIMEOUT_PAGE
                )
                
                # Extract website metadata (no download)
                website_metadata = await self.extract_website_metadata(tender_page)
                
                ref_display = website_metadata.reference_tender if website_metadata and website_metadata.reference_tender else f"tender_{idx}"
                self.progress.log(
                    "success", 
                    f"Scraped: {ref_display}"
                )
                self._update_progress()
                
                return ScrapedTender(
                    index=idx,
                    url=tender_url,
                    success=True,
                    website_metadata=website_metadata
                )
                
            except PlaywrightTimeout as e:
                self.progress.failed += 1
                self.progress.log("error", f"Timeout on tender #{idx}")
                self._update_progress()
                return ScrapedTender(idx, tender_url, False, f"Timeout: {str(e)[:100]}")
                
            except Exception as e:
                self.progress.failed += 1
                self.progress.log("error", f"Failed tender #{idx}: {type(e).__name__}")
                self._update_progress()
                return ScrapedTender(idx, tender_url, False, f"{type(e).__name__}: {str(e)[:100]}")
                
            finally:
                if tender_page:
                    await tender_page.close()
    
    async def download_tender_zip(
        self,
        context,
        tender_url: str,
        idx: int,
        website_metadata: Optional[WebsiteMetadata] = None
    ) -> DownloadedTender:
        """
        Download the ZIP file for a specific tender.
        Called only when website data is insufficient.
        
        Returns:
            DownloadedTender with ZIP bytes in memory
        """
        tender_page = None
        try:
            tender_page = await context.new_page()
            
            # Navigate to tender page
            await tender_page.goto(
                tender_url, 
                timeout=settings.SCRAPER_TIMEOUT_PAGE
            )
            
            # Click download button
            await tender_page.click(
                'a[id="ctl0_CONTENU_PAGE_linkDownloadDce"]',
                timeout=settings.SCRAPER_TIMEOUT_PAGE // 2
            )
            
            # Wait for form
            await tender_page.wait_for_selector(
                '#ctl0_CONTENU_PAGE_EntrepriseFormulaireDemande_nom',
                timeout=settings.SCRAPER_TIMEOUT_PAGE // 2
            )
            
            # Fill form
            await tender_page.check(
                '#ctl0_CONTENU_PAGE_EntrepriseFormulaireDemande_accepterConditions'
            )
            await tender_page.fill(
                '#ctl0_CONTENU_PAGE_EntrepriseFormulaireDemande_nom',
                settings.FORM_NOM
            )
            await tender_page.fill(
                '#ctl0_CONTENU_PAGE_EntrepriseFormulaireDemande_prenom',
                settings.FORM_PRENOM
            )
            await tender_page.fill(
                '#ctl0_CONTENU_PAGE_EntrepriseFormulaireDemande_email',
                settings.FORM_EMAIL
            )
            
            # Submit form
            await tender_page.click('#ctl0_CONTENU_PAGE_validateButton')
            
            # Wait for download button
            await tender_page.wait_for_selector(
                '#ctl0_CONTENU_PAGE_EntrepriseDownloadDce_completeDownload',
                timeout=settings.SCRAPER_TIMEOUT_PAGE // 2
            )
            
            # Trigger download and capture to memory
            async with tender_page.expect_download(
                timeout=settings.SCRAPER_TIMEOUT_DOWNLOAD
            ) as download_info:
                await tender_page.click(
                    '#ctl0_CONTENU_PAGE_EntrepriseDownloadDce_completeDownload'
                )
            
            download = await download_info.value
            
            # Read download to memory (NO DISK WRITE)
            path = await download.path()
            if path:
                with open(path, 'rb') as f:
                    zip_bytes = f.read()
            else:
                zip_bytes = await download.read_bytes() if hasattr(download, 'read_bytes') else None
            
            self.progress.downloaded += 1
            ref_display = website_metadata.reference_tender if website_metadata and website_metadata.reference_tender else f"tender_{idx}"
            self.progress.log(
                "success", 
                f"Downloaded ZIP: {ref_display} ({download.suggested_filename[:30]})"
            )
            self._update_progress()
            
            return DownloadedTender(
                index=idx,
                url=tender_url,
                success=True,
                zip_bytes=zip_bytes,
                suggested_filename=download.suggested_filename,
                website_metadata=website_metadata
            )
            
        except PlaywrightTimeout as e:
            self.progress.failed += 1
            self.progress.log("error", f"Timeout downloading tender #{idx}")
            self._update_progress()
            return DownloadedTender(idx, tender_url, False, f"Timeout: {str(e)[:100]}", website_metadata=website_metadata)
            
        except Exception as e:
            self.progress.failed += 1
            self.progress.log("error", f"Failed to download tender #{idx}: {type(e).__name__}")
            self._update_progress()
            return DownloadedTender(idx, tender_url, False, f"{type(e).__name__}: {str(e)[:100]}", website_metadata=website_metadata)
            
        finally:
            if tender_page:
                await tender_page.close()
    
    async def download_single_tender(
        self,
        context,
        tender_url: str,
        idx: int,
        semaphore: asyncio.Semaphore
    ) -> DownloadedTender:
        """
        LEGACY: Download a single tender to memory and extract website metadata.
        Use scrape_single_tender + download_tender_zip for optimized flow.
        
        Returns:
            DownloadedTender with ZIP bytes in memory and website metadata
        """
        async with semaphore:
            if self._stop_requested:
                return DownloadedTender(idx, tender_url, False, "Stopped by user")
            
            tender_page = None
            website_metadata = None
            try:
                tender_page = await context.new_page()
                
                # Navigate to tender page
                await tender_page.goto(
                    tender_url, 
                    timeout=settings.SCRAPER_TIMEOUT_PAGE
                )
                
                # Extract website metadata BEFORE clicking download
                website_metadata = await self.extract_website_metadata(tender_page)
                
                # Click download button
                await tender_page.click(
                    'a[id="ctl0_CONTENU_PAGE_linkDownloadDce"]',
                    timeout=settings.SCRAPER_TIMEOUT_PAGE // 2
                )
                
                # Wait for form
                await tender_page.wait_for_selector(
                    '#ctl0_CONTENU_PAGE_EntrepriseFormulaireDemande_nom',
                    timeout=settings.SCRAPER_TIMEOUT_PAGE // 2
                )
                
                # Fill form
                await tender_page.check(
                    '#ctl0_CONTENU_PAGE_EntrepriseFormulaireDemande_accepterConditions'
                )
                await tender_page.fill(
                    '#ctl0_CONTENU_PAGE_EntrepriseFormulaireDemande_nom',
                    settings.FORM_NOM
                )
                await tender_page.fill(
                    '#ctl0_CONTENU_PAGE_EntrepriseFormulaireDemande_prenom',
                    settings.FORM_PRENOM
                )
                await tender_page.fill(
                    '#ctl0_CONTENU_PAGE_EntrepriseFormulaireDemande_email',
                    settings.FORM_EMAIL
                )
                
                # Submit form
                await tender_page.click('#ctl0_CONTENU_PAGE_validateButton')
                
                # Wait for download button
                await tender_page.wait_for_selector(
                    '#ctl0_CONTENU_PAGE_EntrepriseDownloadDce_completeDownload',
                    timeout=settings.SCRAPER_TIMEOUT_PAGE // 2
                )
                
                # Trigger download and capture to memory
                async with tender_page.expect_download(
                    timeout=settings.SCRAPER_TIMEOUT_DOWNLOAD
                ) as download_info:
                    await tender_page.click(
                        '#ctl0_CONTENU_PAGE_EntrepriseDownloadDce_completeDownload'
                    )
                
                download = await download_info.value
                
                # Read download to memory (NO DISK WRITE)
                path = await download.path()
                if path:
                    with open(path, 'rb') as f:
                        zip_bytes = f.read()
                else:
                    zip_bytes = await download.read_bytes() if hasattr(download, 'read_bytes') else None
                
                self.progress.downloaded += 1
                ref_display = website_metadata.reference_tender if website_metadata and website_metadata.reference_tender else f"tender_{idx}"
                self.progress.log(
                    "success", 
                    f"Downloaded: {ref_display} ({download.suggested_filename[:30]})"
                )
                self._update_progress()
                
                return DownloadedTender(
                    index=idx,
                    url=tender_url,
                    success=True,
                    zip_bytes=zip_bytes,
                    suggested_filename=download.suggested_filename,
                    website_metadata=website_metadata
                )
                
            except PlaywrightTimeout as e:
                self.progress.failed += 1
                self.progress.log("error", f"Timeout on tender #{idx}")
                self._update_progress()
                return DownloadedTender(idx, tender_url, False, f"Timeout: {str(e)[:100]}")
                
            except Exception as e:
                self.progress.failed += 1
                self.progress.log("error", f"Failed tender #{idx}: {type(e).__name__}")
                self._update_progress()
                return DownloadedTender(idx, tender_url, False, f"{type(e).__name__}: {str(e)[:100]}")
                
            finally:
                if tender_page:
                    await tender_page.close()
    
    async def run(
        self, 
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> List[DownloadedTender]:
        """
        Execute full scraping run with LAZY DOWNLOAD optimization.
        
        1. Scrape website metadata for all tenders (no download)
        2. For tenders with incomplete data, download ZIP for fallback
        3. Return list of DownloadedTender (some may have zip_bytes=None if website was sufficient)
        
        Args:
            start_date: Start date to scrape (YYYY-MM-DD). Defaults to yesterday.
            end_date: End date to scrape (YYYY-MM-DD). Defaults to start_date.
        
        Returns:
            List of DownloadedTender objects (with or without ZIP bytes)
        """
        self._stop_requested = False
        self.progress = ScraperProgress(is_running=True)
        
        # Default to yesterday
        if not start_date:
            yesterday = datetime.today() - timedelta(days=1)
            start_date = yesterday.strftime('%Y-%m-%d')
        
        # Default end_date to start_date (single day)
        if not end_date:
            end_date = start_date
        
        self.progress.log("info", "=" * 50)
        self.progress.log("info", f"Starting scraper (optimized)")
        self.progress.log("info", f"Date range: {start_date} → {end_date}")
        self.progress.log("info", "=" * 50)
        
        start_time = datetime.now()
        results: List[DownloadedTender] = []
        
        async with async_playwright() as p:
            # Phase 1: Browser init
            self.progress.phase = "Launching browser"
            self._update_progress()
            
            browser = await p.chromium.launch(headless=settings.SCRAPER_HEADLESS)
            context = await browser.new_context(accept_downloads=True)
            self.progress.log("success", "Browser ready (headless)")
            
            try:
                # Phase 2: Collect links
                self.progress.phase = "Collecting tender links"
                self._update_progress()
                
                page = await context.new_page()
                tender_links = await self.collect_tender_links(page, start_date, end_date)
                await page.close()
                
                self.progress.total = len(tender_links)
                self._update_progress()
                
                if not tender_links:
                    self.progress.log("warning", "No tenders found")
                    return []
                
                # Phase 3: Scrape website metadata (NO DOWNLOAD YET)
                self.progress.phase = f"Scraping {len(tender_links)} tender pages"
                self.progress.log("info", f"Phase 3: Extracting website metadata (no download)")
                self._update_progress()
                
                semaphore = asyncio.Semaphore(settings.SCRAPER_MAX_CONCURRENT)
                
                # Scrape all tender pages
                scrape_tasks = [
                    self.scrape_single_tender(context, url, idx, semaphore)
                    for idx, url in enumerate(tender_links, 1)
                ]
                
                scraped_tenders = await asyncio.gather(*scrape_tasks, return_exceptions=True)
                scraped_tenders = [
                    t for t in scraped_tenders 
                    if isinstance(t, ScrapedTender)
                ]
                
                # Phase 4: Download ZIPs only for incomplete tenders
                needs_download = [t for t in scraped_tenders if t.success and t.needs_document_download()]
                complete_tenders = [t for t in scraped_tenders if t.success and not t.needs_document_download()]
                
                self.progress.log("info", f"Complete from website: {len(complete_tenders)}")
                self.progress.log("info", f"Need document fallback: {len(needs_download)}")
                
                # Convert complete tenders to DownloadedTender (no ZIP)
                for scraped in complete_tenders:
                    results.append(DownloadedTender(
                        index=scraped.index,
                        url=scraped.url,
                        success=True,
                        zip_bytes=None,  # No download needed
                        website_metadata=scraped.website_metadata
                    ))
                
                if needs_download:
                    self.progress.phase = f"Downloading {len(needs_download)} ZIPs (fallback)"
                    self.progress.log("info", f"Phase 4: Downloading documents for {len(needs_download)} incomplete tenders")
                    self._update_progress()
                    
                    for scraped in needs_download:
                        if self._stop_requested:
                            break
                        downloaded = await self.download_tender_zip(
                            context, 
                            scraped.url, 
                            scraped.index, 
                            scraped.website_metadata
                        )
                        results.append(downloaded)
                
                # Add failed scraped tenders as failed downloads
                for scraped in scraped_tenders:
                    if not scraped.success:
                        results.append(DownloadedTender(
                            index=scraped.index,
                            url=scraped.url,
                            success=False,
                            error=scraped.error
                        ))
                
            finally:
                await browser.close()
        
        # Finalize
        elapsed = (datetime.now() - start_time).total_seconds()
        self.progress.elapsed_seconds = elapsed
        self.progress.phase = "Completed"
        self.progress.is_running = False
        
        success_count = sum(1 for r in results if r.success)
        fail_count = sum(1 for r in results if not r.success)
        download_count = sum(1 for r in results if r.success and r.zip_bytes)
        
        self.progress.log("info", "=" * 50)
        self.progress.log("success", f"Scraped: {success_count}/{len(tender_links)}")
        self.progress.log("info", f"ZIP Downloads: {download_count} (only for incomplete)")
        self.progress.log("error" if fail_count else "success", f"Failed: {fail_count}")
        self.progress.log("info", f"Time: {elapsed:.1f}s")
        self.progress.log("info", "=" * 50)
        
        self._update_progress()
        
        return results
