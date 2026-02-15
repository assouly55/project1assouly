import { useState, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { ExternalLink, FileText, ChevronRight, ChevronDown, ChevronUp, FlaskConical, Clock, Percent, Award, Shield } from 'lucide-react';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { Badge } from '@/components/ui/badge';
import type { Tender, AvisMetadata, ContractDetails } from '@/types/tender';

interface TenderTableProps {
  tenders: Tender[];
  isLoading?: boolean;
}

// Normalize backend field names to frontend expected structure
function normalizeAvisMetadata(raw: any): AvisMetadata | null {
  if (!raw) return null;
  
  return {
    reference_marche: raw.reference_marche ?? raw.reference_tender?.value ?? null,
    type_procedure: raw.type_procedure ?? raw.procedure?.value ?? raw.tender_type?.value ?? null,
    organisme_acheteur: raw.organisme_acheteur ?? raw.issuing_institution?.value ?? null,
    lieu_execution: raw.lieu_execution ?? raw.execution_location?.value ?? null,
    date_limite_remise_plis: {
      date: raw.date_limite_remise_plis?.date ?? raw.submission_deadline?.date?.value ?? null,
      heure: raw.date_limite_remise_plis?.heure ?? raw.submission_deadline?.time?.value ?? null,
    },
    lieu_ouverture_plis: raw.lieu_ouverture_plis ?? raw.bid_opening_location?.value ?? null,
    objet_marche: raw.objet_marche ?? raw.subject?.value ?? null,
    estimation_totale: {
      montant: raw.estimation_totale?.montant ?? raw.total_estimated_value?.value?.toString() ?? null,
      devise: raw.estimation_totale?.devise ?? raw.total_estimated_value?.currency ?? null,
    },
    lots: (raw.lots || []).map((lot: any) => ({
      numero_lot: lot.numero_lot ?? lot.lot_number ?? null,
      objet_lot: lot.objet_lot ?? lot.lot_subject ?? null,
      estimation_lot: lot.estimation_lot ?? lot.lot_estimated_value?.toString() ?? null,
      caution_provisoire: lot.caution_provisoire?.toString() ?? null,
    })),
    website_extended: raw.website_extended ?? undefined,
  };
}

function MetadataField({ label, value }: { label: string; value: string | null | undefined }) {
  // Don't render if value is null or undefined
  if (!value) return null;
  
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-muted-foreground uppercase tracking-wide">{label}</span>
      <span className="text-sm">{value}</span>
    </div>
  );
}

// --- Contract detail formatting helpers ---

/** Format délai d'exécution as a clean period string */
function formatDelai(raw: string): string {
  if (!raw) return '—';
  const lower = raw.toLowerCase().trim();
  // Extract number + unit
  const match = lower.match(/(\d+)\s*(jours?|mois|ans?|semaines?|days?|months?|years?|weeks?|calendaires?|ouvrables?)/i);
  if (match) {
    const num = match[1];
    const unit = match[2].toLowerCase();
    if (unit.startsWith('jour') || unit.startsWith('day') || unit.startsWith('calendaire') || unit.startsWith('ouvrable')) return `${num} Jours`;
    if (unit.startsWith('mois') || unit.startsWith('month')) return `${num} Mois`;
    if (unit.startsWith('an') || unit.startsWith('year')) return `${num} Ans`;
    if (unit.startsWith('semaine') || unit.startsWith('week')) return `${num} Semaines`;
  }
  return raw;
}

/** Format pénalité de retard as a percentage string */
function formatPenalite(pen: ContractDetails['penalite_retard']): string {
  if (!pen) return '—';
  if (typeof pen === 'string') return pen;
  const taux = pen.taux;
  if (!taux) return '—';
  // Normalize: if it contains ‰ or /1000, display as-is. If it has %, display as-is.
  return taux;
}

/** Format caution définitive taux */
function formatCautionTaux(cd: ContractDetails['caution_definitive']): string {
  if (!cd) return '—';
  if (typeof cd === 'string') return cd;
  const taux = cd.taux;
  if (!taux) return '—';
  // Ensure it shows as percentage
  const clean = taux.trim();
  if (!clean.includes('%')) return `${clean}%`;
  return clean;
}

function AvisMetadataDetails({ rawMetadata, contractDetails }: { rawMetadata: any; contractDetails?: ContractDetails | null }) {
  const metadata = normalizeAvisMetadata(rawMetadata);
  
  if (!metadata) {
    return (
      <div className="p-4 bg-muted/30 text-muted-foreground italic text-sm">
        Aucune métadonnée extraite
      </div>
    );
  }

  // Check if any field has a value
  const hasGeneralInfo = metadata.reference_marche || metadata.type_procedure || 
    metadata.organisme_acheteur || metadata.lieu_execution || metadata.lieu_ouverture_plis;
  
  const hasDeadlineInfo = metadata.date_limite_remise_plis?.date || metadata.date_limite_remise_plis?.heure;
  
  const hasFinancialInfo = metadata.estimation_totale?.montant || metadata.estimation_totale?.devise;
  
  const hasLots = metadata.lots && metadata.lots.length > 0;

  return (
    <div className="p-4 bg-muted/20 border-t border-border space-y-4">
      {/* Main Fields Grid - only show if there's data */}
      {(hasGeneralInfo || hasDeadlineInfo || hasFinancialInfo) && (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
          <MetadataField label="Référence" value={metadata.reference_marche} />
          <MetadataField label="Procédure" value={metadata.type_procedure} />
          <MetadataField label="Organisme" value={metadata.organisme_acheteur} />
          <MetadataField label="Lieu d'exécution" value={metadata.lieu_execution} />
          <MetadataField label="Lieu d'ouverture des plis" value={metadata.lieu_ouverture_plis} />
          <MetadataField label="Date Limite" value={metadata.date_limite_remise_plis?.date} />
          <MetadataField label="Heure Limite" value={metadata.date_limite_remise_plis?.heure} />
          <MetadataField label="Estimation (TTC)" value={metadata.estimation_totale?.montant} />
          <MetadataField label="Devise" value={metadata.estimation_totale?.devise} />
        </div>
      )}

      {/* Subject - Full Width */}
      {metadata.objet_marche && (
        <div className="border-t border-border pt-4">
          <MetadataField label="Objet" value={metadata.objet_marche} />
        </div>
      )}

      {/* Lots - only show if there are lots with data */}
      {hasLots && (
        <div className="border-t border-border pt-4">
          <span className="text-xs text-muted-foreground uppercase tracking-wide block mb-2">
            Lots ({metadata.lots.length})
          </span>
          <div className="space-y-2">
            {metadata.lots.map((lot, idx) => {
              const hasLotData = lot.numero_lot || lot.objet_lot || lot.estimation_lot || lot.caution_provisoire;
              if (!hasLotData) return null;
              
              return (
                <div key={idx} className="bg-background/50 rounded p-3 text-sm grid grid-cols-2 md:grid-cols-4 gap-2">
                  {lot.numero_lot && (
                    <div>
                      <span className="text-muted-foreground text-xs">Lot #:</span>{' '}
                      <span className="font-mono">{lot.numero_lot}</span>
                    </div>
                  )}
                  {lot.objet_lot && (
                    <div className="col-span-2">
                      <span className="text-muted-foreground text-xs">Objet:</span>{' '}
                      {lot.objet_lot}
                    </div>
                  )}
                  {lot.estimation_lot && (
                    <div>
                      <span className="text-muted-foreground text-xs">Estimation:</span>{' '}
                      {lot.estimation_lot}
                    </div>
                  )}
                  {lot.caution_provisoire && (
                    <div>
                      <span className="text-muted-foreground text-xs">Caution:</span>{' '}
                      {lot.caution_provisoire}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
      {/* Contract Details */}
      {contractDetails && (
        <div className="border-t border-border pt-4">
          <span className="text-xs text-muted-foreground uppercase tracking-wide block mb-2">
            Détails Contractuels
          </span>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {contractDetails.delai_execution && (
              <div className="flex items-center gap-2 text-sm">
                <Clock className="w-3.5 h-3.5 text-primary flex-shrink-0" />
                <div>
                  <span className="text-xs text-muted-foreground block">Délai d'exécution</span>
                  <span>{formatDelai(contractDetails.delai_execution)}</span>
                </div>
              </div>
            )}
            {contractDetails.penalite_retard && (
              <div className="flex items-center gap-2 text-sm">
                <Percent className="w-3.5 h-3.5 text-warning flex-shrink-0" />
                <div>
                  <span className="text-xs text-muted-foreground block">Pénalité de retard</span>
                  <span>{formatPenalite(contractDetails.penalite_retard)}</span>
                  {typeof contractDetails.penalite_retard === 'object' && contractDetails.penalite_retard.plafond && (
                    <span className="text-xs text-muted-foreground block">Plafond: {contractDetails.penalite_retard.plafond}</span>
                  )}
                </div>
              </div>
            )}
            {contractDetails.mode_attribution && (
              <div className="flex items-center gap-2 text-sm">
                <Award className="w-3.5 h-3.5 text-success flex-shrink-0" />
                <div>
                  <span className="text-xs text-muted-foreground block">Attribution</span>
                  <span>{contractDetails.mode_attribution}</span>
                </div>
              </div>
            )}
            {contractDetails.caution_definitive && (
              <div className="flex items-center gap-2 text-sm">
                <Shield className="w-3.5 h-3.5 text-primary flex-shrink-0" />
                <div>
                  <span className="text-xs text-muted-foreground block">Caution Déf.</span>
                  <span>{formatCautionTaux(contractDetails.caution_definitive)}</span>
                  {typeof contractDetails.caution_definitive === 'object' && contractDetails.caution_definitive.montant_estime && (
                    <span className="text-xs text-primary block font-mono">≈ {contractDetails.caution_definitive.montant_estime}</span>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function TenderTable({ tenders, isLoading }: TenderTableProps) {
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());

  const toggleRow = (id: string) => {
    setExpandedRows(prev => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  // Normalize all tender metadata for table display
  const normalizedTenders = useMemo(() => {
    return tenders.map(tender => ({
      ...tender,
      normalizedMetadata: normalizeAvisMetadata(tender.avis_metadata)
    }));
  }, [tenders]);

  if (isLoading) {
    return (
      <div className="data-card">
        <div className="animate-pulse space-y-3">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="h-12 bg-muted rounded" />
          ))}
        </div>
      </div>
    );
  }

  if (tenders.length === 0) {
    return (
      <div className="data-card text-center py-12">
        <FileText className="w-12 h-12 text-muted-foreground mx-auto mb-4" />
        <h3 className="text-lg font-medium mb-2">Aucun appel d'offres</h3>
        <p className="text-muted-foreground text-sm">
          Lancez le scraper pour collecter des appels d'offres
        </p>
      </div>
    );
  }

  return (
    <div className="data-card p-0 overflow-hidden">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent border-border">
            <TableHead className="w-[40px]"></TableHead>
            <TableHead className="w-[140px]">Référence</TableHead>
            <TableHead>Objet</TableHead>
            <TableHead className="w-[150px]">Organisme</TableHead>
            <TableHead className="w-[100px]">Date Limite</TableHead>
            <TableHead className="w-[90px]">Statut</TableHead>
            <TableHead className="w-[80px]"></TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {normalizedTenders.map((tender) => {
            const isExpanded = expandedRows.has(tender.id);
            const meta = tender.normalizedMetadata;
            
            return (
              <>
                <TableRow 
                  key={tender.id} 
                  className="border-border hover:bg-table-hover cursor-pointer"
                  onClick={() => toggleRow(tender.id)}
                >
                  <TableCell className="p-2">
                    <button 
                      className="p-1 rounded hover:bg-muted transition-colors"
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleRow(tender.id);
                      }}
                    >
                      {isExpanded ? (
                        <ChevronUp className="w-4 h-4 text-muted-foreground" />
                      ) : (
                        <ChevronDown className="w-4 h-4 text-muted-foreground" />
                      )}
                    </button>
                  </TableCell>
                  <TableCell className="font-mono text-sm">
                    {meta?.reference_marche || tender.external_reference || tender.id.slice(0, 8)}
                  </TableCell>
                  <TableCell className="max-w-[400px] truncate">
                    {meta?.objet_marche || 
                      <span className="text-muted-foreground italic">Objet non extrait</span>
                    }
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground truncate max-w-[150px]">
                    {meta?.organisme_acheteur || '—'}
                  </TableCell>
                  <TableCell className="font-mono text-sm">
                    {meta?.date_limite_remise_plis?.date || '—'}
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={tender.status} />
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1">
                      <a
                        href={tender.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="p-1.5 rounded hover:bg-muted transition-colors"
                        title="Voir l'original"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <ExternalLink className="w-4 h-4 text-muted-foreground" />
                      </a>
                      <Link
                        to={`/tender/${tender.id}?analyze=true`}
                        className="p-1.5 rounded hover:bg-accent text-accent-foreground transition-colors"
                        title="Forcer l'analyse (test)"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <FlaskConical className="w-4 h-4 text-primary" />
                      </Link>
                      <Link
                        to={`/tender/${tender.id}`}
                        className="p-1.5 rounded hover:bg-muted transition-colors"
                        title="Voir les détails"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <ChevronRight className="w-4 h-4 text-muted-foreground" />
                      </Link>
                    </div>
                  </TableCell>
                </TableRow>
                {isExpanded && (
                  <TableRow key={`${tender.id}-details`} className="hover:bg-transparent">
                    <TableCell colSpan={7} className="p-0">
                      <AvisMetadataDetails rawMetadata={tender.avis_metadata} contractDetails={tender.contract_details} />
                    </TableCell>
                  </TableRow>
                )}
              </>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
