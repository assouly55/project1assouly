// Tender AI Platform — Type Definitions (V3 Simplified)
// Phase 1: Primary/Avis metadata
// Phase 2: Bordereau des Prix items

export type TenderStatus = 'PENDING' | 'LISTED' | 'ANALYZED' | 'ERROR';

// Lot structure (Phase 1)
export interface TenderLot {
  numero_lot: string | null;
  objet_lot: string | null;
  estimation_lot: string | null;
  caution_provisoire: string | null;
}

// Bordereau item (Phase 2)
export interface BordereauItem {
  numero_prix: string | null;
  designation: string | null;
  unite: string | null;
  quantite: string | null;
}

// Lot with articles (Phase 2)
export interface LotArticles {
  numero_lot: string;
  articles: BordereauItem[];
}

// Category assignment
export interface TenderCategory {
  main_category: string;
  subcategory: string;
  item: string | null;
  confidence: number;
  reason?: string;
}

// Phase 1: Avis/Primary metadata
export interface AvisMetadata {
  reference_marche: string | null;
  type_procedure: string | null;
  organisme_acheteur: string | null;
  lieu_execution?: string | null;
  date_limite_remise_plis: {
    date: string | null;
    heure: string | null;
  };
  lieu_ouverture_plis: string | null;
  objet_marche: string | null;
  estimation_totale: {
    montant: string | null;
    devise: string | null;
  };
  lots: TenderLot[];
  website_extended?: {
    contact_administratif?: { value: string | null; source_document: string | null };
  };
}

// Phase 2: Bordereau des Prix
export interface BordereauMetadata {
  lots_articles: LotArticles[];
  _completeness?: {
    is_complete: boolean;
    total_articles: number;
    lots_count: number;
  };
}

// Document
export interface TenderDocument {
  id: string;
  tender_id: string;
  document_type: string;
  filename: string;
  raw_text: string | null;
  page_count: number | null;
  extraction_method: 'DIGITAL' | 'OCR';
  extracted_at: string;
}

// Main Tender record
export interface Tender {
  id: string;
  external_reference: string;
  source_url: string;
  status: TenderStatus;
  scraped_at: string;
  download_date: string;
  avis_metadata: AvisMetadata | null;
  bordereau_metadata: BordereauMetadata | null;
  universal_metadata: BordereauMetadata | null;  // Backward compat alias
  categories?: TenderCategory[] | null;
  documents?: TenderDocument[];
  created_at: string;
  updated_at: string;
}

// API Response types
export interface ApiResponse<T> {
  success: boolean;
  data?: T;
  error?: string;
  message?: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

// Scraper types
export interface ScraperLogEntry {
  level: 'info' | 'success' | 'warning' | 'error';
  message: string;
  timestamp?: string;
}

export interface ScraperLogEntry {
  level: 'info' | 'success' | 'warning' | 'error';
  message: string;
  timestamp?: string;
}

export interface ScraperStats {
  total: number;
  downloaded: number;
  failed: number;
  elapsed: number;
}

export interface ScraperStatus {
  is_running: boolean;
  current_phase: string;
  total_tenders: number;
  downloaded: number;
  failed: number;
  elapsed_seconds: number;
  last_run: string | null;
  logs?: ScraperLogEntry[];
  stats?: ScraperStats;
}

export interface TenderSearchParams {
  query?: string;
  status?: TenderStatus;
  date_from?: string;
  date_to?: string;
  page?: number;
  per_page?: number;
}
