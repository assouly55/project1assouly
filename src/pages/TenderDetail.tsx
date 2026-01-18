import { useParams, Link, useSearchParams } from 'react-router-dom';
import { useEffect, useState, useMemo } from 'react';
import { ArrowLeft, ExternalLink, Bot, FileText, RefreshCw, Loader2, CheckCircle2, AlertCircle, User, Mail, Phone, Building2, Tag, MessageSquare } from 'lucide-react';
import { AppLayout } from '@/components/layout/AppLayout';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Progress } from '@/components/ui/progress';
import { api } from '@/lib/api';
import { AskAIChat } from '@/components/tenders/AskAIChat';
import type { Tender, TenderLot, BordereauItem, LotArticles, AvisMetadata, BordereauMetadata, TenderCategory } from '@/types/tender';

// Merged lot with both avis and bordereau items
interface MergedLot extends TenderLot {
  articles?: BordereauItem[];
}

// Parse contact text into structured format
interface ParsedContact {
  name?: string;
  role?: string;
  email?: string;
  mobile?: string;
  institutionPhone?: string;
}

function parseContactText(text: string): ParsedContact {
  const contact: ParsedContact = {};
  const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
  
  for (const line of lines) {
    // Email detection
    if (line.includes('@')) {
      contact.email = line;
      continue;
    }
    
    // Phone detection - mobile starts with 06, 07, +2126, +2127, etc.
    const phoneMatch = line.match(/(\+?212?\s*\(?\d?\)?\s*[67]\d[\d\s.-]{7,}|\b0[67]\d[\d\s.-]{7,})/);
    if (phoneMatch) {
      const cleanPhone = line.replace(/\s/g, '');
      // Check if mobile (6 or 7 after country code or 0)
      const isMobile = /(\+?212\s*\(?\d?\)?\s*[67]|^0[67])/.test(cleanPhone);
      if (isMobile) {
        contact.mobile = contact.mobile ? contact.mobile : line;
      } else {
        contact.institutionPhone = contact.institutionPhone ? contact.institutionPhone : line;
      }
      continue;
    }
    
    // Institution phone (05xx)
    const instPhoneMatch = line.match(/(\+?212?\s*\(?\d?\)?\s*5\d[\d\s.-]{7,}|\b05\d[\d\s.-]{7,})/);
    if (instPhoneMatch) {
      contact.institutionPhone = contact.institutionPhone ? contact.institutionPhone : line;
      continue;
    }
    
    // First non-phone, non-email line is likely name
    if (!contact.name) {
      contact.name = line;
    }
  }
  
  return contact;
}

function ContactDisplay({ contactText }: { contactText: string }) {
  const contact = parseContactText(contactText);
  const hasData = contact.name || contact.email || contact.mobile || contact.institutionPhone;
  
  if (!hasData) return null;
  
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
      {contact.name && (
        <div className="flex items-center gap-2">
          <User className="w-4 h-4 text-muted-foreground" />
          <span>{contact.name}</span>
        </div>
      )}
      {contact.email && (
        <div className="flex items-center gap-2">
          <Mail className="w-4 h-4 text-muted-foreground" />
          <a href={`mailto:${contact.email}`} className="text-primary hover:underline">{contact.email}</a>
        </div>
      )}
      {contact.mobile && (
        <div className="flex items-center gap-2">
          <Phone className="w-4 h-4 text-muted-foreground" />
          <span>{contact.mobile}</span>
          <span className="text-xs text-muted-foreground">(Mobile)</span>
        </div>
      )}
      {contact.institutionPhone && (
        <div className="flex items-center gap-2">
          <Building2 className="w-4 h-4 text-muted-foreground" />
          <span>{contact.institutionPhone}</span>
          <span className="text-xs text-muted-foreground">(Institution)</span>
        </div>
      )}
    </div>
  );
}

function MetadataField({ label, value, source }: { label: string; value: string | null | undefined; source?: string | null }) {
  // Don't render if value is null/undefined/empty
  if (!value) return null;
  
  return (
    <div className="py-3 border-b border-border last:border-0">
      <div className="text-xs text-muted-foreground mb-1">{label}</div>
      <div className="font-medium">{value}</div>
      {source && (
        <div className="text-xs text-muted-foreground mt-1">
          Source: <span className="font-mono">{source}</span>
        </div>
      )}
    </div>
  );
}

// Display best category as a path (main > sub > item)
function CategoryPath({ categories }: { categories: TenderCategory[] }) {
  if (!categories || categories.length === 0) return null;
  
  // Get the best category (highest confidence)
  const bestCategory = categories.reduce((best, current) => 
    current.confidence > best.confidence ? current : best
  , categories[0]);
  
  const confidenceColor = bestCategory.confidence >= 0.9 
    ? 'text-success' 
    : bestCategory.confidence >= 0.7 
      ? 'text-primary'
      : 'text-muted-foreground';
  
  return (
    <div className="flex items-center gap-2 text-sm">
      <Tag className="w-4 h-4 text-muted-foreground flex-shrink-0" />
      <div className="flex items-center gap-1 flex-wrap">
        <span className="font-medium">{bestCategory.main_category}</span>
        <span className="text-muted-foreground">›</span>
        <span>{bestCategory.subcategory}</span>
        {bestCategory.item && (
          <>
            <span className="text-muted-foreground">›</span>
            <span className="text-muted-foreground">{bestCategory.item}</span>
          </>
        )}
        <span className={`text-xs ${confidenceColor} ml-2`}>
          ({Math.round(bestCategory.confidence * 100)}%)
        </span>
      </div>
    </div>
  );
}

// Helper to safely extract string value from potentially nested objects like {value, source_document}
function safeString(val: unknown): string | null {
  if (val === null || val === undefined) return null;
  if (typeof val === 'string') return val;
  if (typeof val === 'number') return String(val);
  if (typeof val === 'object') {
    // Handle {value: ...} structure
    if ('value' in (val as object)) {
      const nested = (val as { value: unknown }).value;
      return safeString(nested); // Recursive call to handle nested values
    }
    // For any other object, try to get a meaningful string
    try {
      const str = JSON.stringify(val);
      // If it's just "{}", return null
      if (str === '{}' || str === '[]') return null;
      // Otherwise return the stringified version for debugging
      return null; // Don't render raw objects
    } catch {
      return null;
    }
  }
  return null;
}

function LotCard({ lot, index, showArticles }: { lot: MergedLot; index: number; showArticles: boolean }) {
  // Safely access lot properties with defaults (French field names)
  const lotNumber = safeString(lot?.numero_lot) ?? String(index + 1);
  const lotSubject = safeString(lot?.objet_lot);
  const lotValue = safeString(lot?.estimation_lot) ?? '-';
  const cautionProv = safeString(lot?.caution_provisoire) ?? '-';

  // Safely access articles and normalize nested objects
  const safeArticles: BordereauItem[] = Array.isArray(lot?.articles)
    ? lot.articles
        .filter((it): it is BordereauItem => it && typeof it === 'object')
        .map((art) => ({
          numero_prix: safeString(art.numero_prix),
          designation: safeString(art.designation),
          unite: safeString(art.unite),
          quantite: safeString(art.quantite),
        }))
    : [];

  return (
    <div className="data-card space-y-4">
      <div className="flex items-start justify-between">
        <div className="flex-1">
          <div className="text-sm text-muted-foreground">Lot {lotNumber}</div>
          <div className="font-medium mt-1">
            {lotSubject || <span className="text-muted-foreground italic">Objet non spécifié</span>}
          </div>
        </div>
        <div className="text-right">
          <div className="font-mono text-sm">{lotValue}</div>
          <div className="text-xs text-muted-foreground mt-1">Caution Provisoire: {cautionProv}</div>
        </div>
      </div>

      {showArticles && safeArticles.length > 0 && (
        <div className="border-t border-border pt-4">
          <div className="text-sm font-medium mb-3">Articles ({safeArticles.length})</div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left py-2 px-2 text-muted-foreground font-medium">N° Prix</th>
                  <th className="text-left py-2 px-2 text-muted-foreground font-medium">Désignation</th>
                  <th className="text-left py-2 px-2 text-muted-foreground font-medium">Unité</th>
                  <th className="text-right py-2 px-2 text-muted-foreground font-medium">Quantité</th>
                </tr>
              </thead>
              <tbody>
                {safeArticles.map((article, articleIndex) => (
                  <tr key={articleIndex} className="border-b border-border/50 last:border-0">
                    <td className="py-2 px-2 font-mono">{article.numero_prix || '-'}</td>
                    <td className="py-2 px-2">{article.designation || '-'}</td>
                    <td className="py-2 px-2">{article.unite || '-'}</td>
                    <td className="py-2 px-2 text-right font-mono">{article.quantite || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function LoadingOverlay({ progress, message }: { progress: number; message: string }) {
  return (
    <div className="fixed inset-0 bg-background/80 backdrop-blur-sm z-50 flex items-center justify-center">
      <div className="bg-card border border-border rounded-lg p-8 max-w-md w-full mx-4 space-y-4 shadow-lg">
        <div className="flex items-center gap-3">
          <Loader2 className="w-6 h-6 animate-spin text-primary" />
          <div className="text-lg font-medium">Analyse en cours</div>
        </div>
        <Progress value={progress} className="h-2" />
        <p className="text-sm text-muted-foreground">{message}</p>
        <p className="text-xs text-muted-foreground">
          Extraction des données du Bordereau des Prix...
        </p>
      </div>
    </div>
  );
}

export default function TenderDetail() {
  const { id } = useParams<{ id: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const [tender, setTender] = useState<Tender | null>(null);
  const [loading, setLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeProgress, setAnalyzeProgress] = useState(0);
  const [analyzeMessage, setAnalyzeMessage] = useState('Initialisation...');
  const [error, setError] = useState<string | null>(null);

  // Check for ?analyze=true query param (for testing direct analysis)
  const shouldForceAnalyze = searchParams.get('analyze') === 'true';

  // Fetch tender on mount
  useEffect(() => {
    if (!id) return;
    
    const fetchTender = async () => {
      setLoading(true);
      setError(null);
      
      const response = await api.getTender(id);
      
      if (response.success && response.data) {
        setTender(response.data);
        
        // Auto-trigger analysis if:
        // 1. Force analyze via URL param (?analyze=true), OR
        // 2. Tender is LISTED (not yet analyzed)
        if (shouldForceAnalyze || (response.data.status === 'LISTED' && !response.data.bordereau_metadata)) {
          // Clear the analyze param from URL after triggering
          if (shouldForceAnalyze) {
            setSearchParams({}, { replace: true });
          }
          triggerAnalysis(id);
        }
      } else {
        setError(response.error || 'Échec du chargement');
      }
      
      setLoading(false);
    };
    
    fetchTender();
  }, [id, shouldForceAnalyze]);

  const triggerAnalysis = async (tenderId: string) => {
    setAnalyzing(true);
    setAnalyzeProgress(10);
    setAnalyzeMessage('Connexion au pipeline AI...');
    
    // Simulate progress updates
    const progressInterval = setInterval(() => {
      setAnalyzeProgress(prev => {
        if (prev >= 90) return prev;
        const increment = Math.random() * 15;
        return Math.min(prev + increment, 90);
      });
      
      // Update message based on progress
      setAnalyzeProgress(prev => {
        if (prev < 30) setAnalyzeMessage('Extraction du texte des documents...');
        else if (prev < 50) setAnalyzeMessage('Analyse avec IA...');
        else if (prev < 70) setAnalyzeMessage('Traitement des lots et articles...');
        else setAnalyzeMessage('Finalisation...');
        return prev;
      });
    }, 500);
    
    try {
      const response = await api.analyzeTender(tenderId);
      
      clearInterval(progressInterval);
      setAnalyzeProgress(100);
      setAnalyzeMessage('Terminé!');
      
      if (response.success && response.data) {
        setTimeout(() => {
          setTender(response.data!);
          setAnalyzing(false);
        }, 500);
      } else {
        setError(response.error || 'Analyse échouée');
        setAnalyzing(false);
      }
    } catch (err) {
      clearInterval(progressInterval);
      setError('Analyse échouée');
      setAnalyzing(false);
    }
  };

  const handleManualAnalyze = () => {
    if (id) triggerAnalysis(id);
  };

  // Use avis_metadata as the primary source - normalize field names from backend
  const rawAvisMetadata = tender?.avis_metadata as any;
  const avisMetadata: AvisMetadata | null = rawAvisMetadata ? {
    reference_marche: rawAvisMetadata.reference_marche ?? rawAvisMetadata.reference_tender?.value ?? null,
    type_procedure: rawAvisMetadata.type_procedure ?? rawAvisMetadata.procedure?.value ?? rawAvisMetadata.tender_type?.value ?? null,
    organisme_acheteur: rawAvisMetadata.organisme_acheteur ?? rawAvisMetadata.issuing_institution?.value ?? null,
    lieu_execution: rawAvisMetadata.lieu_execution ?? rawAvisMetadata.execution_location?.value ?? null,
    date_limite_remise_plis: {
      date: rawAvisMetadata.date_limite_remise_plis?.date ?? rawAvisMetadata.submission_deadline?.date?.value ?? null,
      heure: rawAvisMetadata.date_limite_remise_plis?.heure ?? rawAvisMetadata.submission_deadline?.time?.value ?? null,
    },
    lieu_ouverture_plis: rawAvisMetadata.lieu_ouverture_plis ?? rawAvisMetadata.bid_opening_location?.value ?? null,
    objet_marche: rawAvisMetadata.objet_marche ?? rawAvisMetadata.subject?.value ?? null,
    estimation_totale: {
      montant: rawAvisMetadata.estimation_totale?.montant ?? rawAvisMetadata.total_estimated_value?.value?.toString() ?? null,
      devise: rawAvisMetadata.estimation_totale?.devise ?? rawAvisMetadata.total_estimated_value?.currency ?? null,
    },
    lots: (rawAvisMetadata.lots || []).map((lot: any) => {
      // Helper to extract value from potentially nested object
      const extract = (v: unknown): string | null => {
        if (v === null || v === undefined) return null;
        if (typeof v === 'string') return v;
        if (typeof v === 'number') return String(v);
        if (typeof v === 'object' && 'value' in (v as object)) {
          return extract((v as { value: unknown }).value);
        }
        return null;
      };
      return {
        numero_lot: extract(lot.numero_lot) ?? extract(lot.lot_number) ?? null,
        objet_lot: extract(lot.objet_lot) ?? extract(lot.lot_subject) ?? null,
        estimation_lot: extract(lot.estimation_lot) ?? extract(lot.lot_estimated_value) ?? null,
        caution_provisoire: extract(lot.caution_provisoire) ?? null,
      };
    }),
    website_extended: rawAvisMetadata.website_extended ?? rawAvisMetadata.contact_administratif ? {
      contact_administratif: rawAvisMetadata.website_extended?.contact_administratif ?? rawAvisMetadata.contact_administratif
    } : undefined,
  } : null;
  
  // Prefer bordereau_metadata, fallback to universal_metadata for backward compat
  const bordereauxMetadata: BordereauMetadata | null = tender?.bordereau_metadata || tender?.universal_metadata || null;
  const hasBordereauData = !!bordereauxMetadata && (bordereauxMetadata._completeness?.total_articles ?? 0) > 0;
  
  // Merge lots: base from avis, articles from bordereau
  const mergedLots: MergedLot[] = useMemo(() => {
    const avisLots = avisMetadata?.lots || [];
    const bordereauxMap = new Map<string, BordereauItem[]>();
    
    if (bordereauxMetadata?.lots_articles) {
      for (const lotArticle of bordereauxMetadata.lots_articles) {
        bordereauxMap.set(lotArticle.numero_lot, lotArticle.articles);
      }
    }
    
    return avisLots.map(lot => ({
      ...lot,
      articles: lot.numero_lot ? bordereauxMap.get(lot.numero_lot) : undefined
    }));
  }, [avisMetadata, bordereauxMetadata]);

  // Total articles count
  const totalArticles = useMemo(() => {
    return bordereauxMetadata?._completeness?.total_articles || 
      (bordereauxMetadata?.lots_articles?.reduce((sum, lot) => sum + lot.articles.length, 0) ?? 0);
  }, [bordereauxMetadata]);

  if (loading) {
    return (
      <AppLayout>
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
        </div>
      </AppLayout>
    );
  }

  if (error || !tender) {
    return (
      <AppLayout>
        <div className="space-y-6">
          <Link 
            to="/" 
            className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            Retour aux appels d'offres
          </Link>
          <div className="data-card text-center py-12">
            <AlertCircle className="w-12 h-12 text-destructive mx-auto mb-4" />
            <h2 className="text-lg font-medium mb-2">Échec du chargement</h2>
            <p className="text-muted-foreground">{error || 'Appel d\'offres non trouvé'}</p>
          </div>
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      {analyzing && (
        <LoadingOverlay progress={analyzeProgress} message={analyzeMessage} />
      )}
      
      <div className="space-y-6">
        {/* Back link */}
        <Link 
          to="/" 
          className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          Retour aux appels d'offres
        </Link>

        {/* Header */}
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-3 mb-2">
              <h1 className="text-2xl font-semibold font-mono">
                {tender.external_reference}
              </h1>
              <StatusBadge status={tender.status} />
              {hasBordereauData && (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-success/10 text-success rounded text-xs">
                  <CheckCircle2 className="w-3 h-3" />
                  {totalArticles} articles
                </span>
              )}
            </div>
            <p className="text-muted-foreground max-w-2xl">
              {avisMetadata?.objet_marche}
            </p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" asChild>
              <a href={tender.source_url} target="_blank" rel="noopener noreferrer">
                <ExternalLink className="w-4 h-4 mr-2" />
                Original
              </a>
            </Button>
            {!hasBordereauData && (
              <Button size="sm" onClick={handleManualAnalyze} disabled={analyzing}>
                <RefreshCw className={`w-4 h-4 mr-2 ${analyzing ? 'animate-spin' : ''}`} />
                Analyser
              </Button>
            )}
          </div>
        </div>

        {/* Tabs */}
        <Tabs defaultValue="metadata" className="space-y-4">
          <TabsList>
            <TabsTrigger value="metadata">Métadonnées</TabsTrigger>
            <TabsTrigger value="lots">Lots ({mergedLots.length})</TabsTrigger>
            <TabsTrigger value="documents">Documents</TabsTrigger>
            <TabsTrigger value="ask">Ask AI</TabsTrigger>
            <TabsTrigger value="raw">JSON Brut</TabsTrigger>
          </TabsList>

          <TabsContent value="metadata" className="space-y-4">
            {/* Data source indicator */}
            <div className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">Source:</span>
              <span className="px-2 py-0.5 rounded text-xs bg-muted">
                Métadonnées Avis (Phase 1)
              </span>
              {hasBordereauData && (
                <span className="px-2 py-0.5 rounded text-xs bg-success/10 text-success">
                  + Bordereau des Prix (Phase 2)
                </span>
              )}
            </div>

            <div className="grid md:grid-cols-2 gap-6">
              {/* Left column - Basic Info */}
              <div className="data-card">
                <h3 className="font-medium mb-4">Informations Générales</h3>
                <MetadataField 
                  label="Référence" 
                  value={avisMetadata?.reference_marche} 
                />
                <MetadataField 
                  label="Procédure" 
                  value={avisMetadata?.type_procedure} 
                />
                <MetadataField 
                  label="Organisme Acheteur" 
                  value={avisMetadata?.organisme_acheteur} 
                />
                <MetadataField 
                  label="Lieu d'exécution" 
                  value={avisMetadata?.lieu_execution} 
                />
                <MetadataField 
                  label="Lieu d'ouverture des plis" 
                  value={avisMetadata?.lieu_ouverture_plis} 
                />
              </div>

              {/* Right column - Submission & Financial Details */}
              <div className="data-card">
                <h3 className="font-medium mb-4">Détails de Soumission</h3>
                <MetadataField 
                  label="Date Limite" 
                  value={avisMetadata?.date_limite_remise_plis?.date} 
                />
                <MetadataField 
                  label="Heure Limite" 
                  value={avisMetadata?.date_limite_remise_plis?.heure} 
                />
                <MetadataField 
                  label="Estimation Totale" 
                  value={avisMetadata?.estimation_totale?.montant} 
                />
                {avisMetadata?.estimation_totale?.devise && (
                  <MetadataField 
                    label="Devise" 
                    value={avisMetadata.estimation_totale.devise} 
                  />
                )}
              </div>
            </div>

            {/* Contact Administratif (if available) */}
            {avisMetadata?.website_extended?.contact_administratif?.value && (
              <div className="data-card">
                <h3 className="font-medium mb-4">Contact Administratif</h3>
                <ContactDisplay contactText={avisMetadata.website_extended.contact_administratif.value} />
              </div>
            )}

            {/* Subject */}
            <div className="data-card">
              <h3 className="font-medium mb-4">Objet du Marché</h3>
              <p className="text-sm whitespace-pre-wrap">{avisMetadata?.objet_marche || 'Non extrait'}</p>
            </div>

            {/* Category Path */}
            {tender?.categories && tender.categories.length > 0 && (
              <div className="data-card">
                <h3 className="font-medium mb-4">Catégorie</h3>
                <CategoryPath categories={tender.categories} />
              </div>
            )}
          </TabsContent>

          <TabsContent value="lots" className="space-y-4">
            <ErrorBoundary title="Échec du rendu des lots">
              {/* Lots summary */}
              <div className="flex items-center justify-between">
                <div className="text-sm text-muted-foreground">
                  {mergedLots.length} lot{mergedLots.length !== 1 ? 's' : ''} extrait{mergedLots.length !== 1 ? 's' : ''}
                </div>
                {hasBordereauData && (
                  <span className="text-xs text-success">{totalArticles} articles au total</span>
                )}
              </div>

              {mergedLots.length > 0 ? (
                <div className="space-y-3">
                  {mergedLots.map((lot, index) => (
                    <LotCard
                      key={index}
                      lot={lot}
                      index={index}
                      showArticles={hasBordereauData}
                    />
                  ))}
                </div>
              ) : (
                <div className="data-card text-center py-8">
                  <FileText className="w-10 h-10 text-muted-foreground mx-auto mb-3" />
                  <p className="text-muted-foreground">Aucun lot extrait</p>
                </div>
              )}
            </ErrorBoundary>
          </TabsContent>

          <TabsContent value="documents" className="space-y-4">
            {tender.documents && tender.documents.length > 0 ? (
              <div className="space-y-3">
                {tender.documents.map((doc) => (
                  <div key={doc.id} className="data-card">
                    <div className="flex items-start justify-between">
                      <div>
                        <div className="flex items-center gap-2">
                          <FileText className="w-4 h-4 text-muted-foreground" />
                          <span className="font-medium">{doc.filename}</span>
                          <span className="px-2 py-0.5 bg-muted rounded text-xs">{doc.document_type}</span>
                        </div>
                        <div className="text-sm text-muted-foreground mt-1">
                          {doc.page_count} pages • {doc.extraction_method}
                        </div>
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {new Date(doc.extracted_at).toLocaleDateString()}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="data-card text-center py-8">
                <FileText className="w-10 h-10 text-muted-foreground mx-auto mb-3" />
                <p className="text-muted-foreground">Documents apparaîtront ici après extraction</p>
                <p className="text-xs text-muted-foreground mt-2">AVIS, RC, CPS, Annexes</p>
              </div>
            )}
          </TabsContent>

          <TabsContent value="ask" className="space-y-4">
            <AskAIChat 
              tenderId={tender.id} 
              tenderReference={tender.external_reference} 
            />
          </TabsContent>

          <TabsContent value="raw" className="space-y-4">
            {/* Show both metadata sources */}
            <div className="space-y-4">
              {tender.avis_metadata && (
                <div className="terminal">
                  <div className="terminal-header">
                    <div className="terminal-dot bg-primary" />
                    <div className="terminal-dot bg-warning" />
                    <div className="terminal-dot bg-destructive" />
                    <span className="ml-2 text-xs text-muted-foreground">avis_metadata.json (Phase 1)</span>
                  </div>
                  <pre className="p-4 text-xs overflow-auto max-h-[400px]">
                    {JSON.stringify(tender.avis_metadata, null, 2)}
                  </pre>
                </div>
              )}
              
              {(tender.bordereau_metadata || tender.universal_metadata) && (
                <div className="terminal">
                  <div className="terminal-header">
                    <div className="terminal-dot bg-success" />
                    <div className="terminal-dot bg-warning" />
                    <div className="terminal-dot bg-destructive" />
                    <span className="ml-2 text-xs text-muted-foreground">bordereau_metadata.json (Phase 2)</span>
                  </div>
                  <pre className="p-4 text-xs overflow-auto max-h-[400px]">
                    {JSON.stringify(tender.bordereau_metadata || tender.universal_metadata, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </AppLayout>
  );
}
