import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { FileText, Search, Filter, FlaskConical, Loader2, Link as LinkIcon } from 'lucide-react';
import { AppLayout } from '@/components/layout/AppLayout';
import { TenderTable } from '@/components/tenders/TenderTable';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { useTenders } from '@/hooks/useTenders';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import type { TenderSearchParams } from '@/types/tender';

export default function Index() {
  const navigate = useNavigate();
  const [searchQuery, setSearchQuery] = useState('');
  const [searchParams, setSearchParams] = useState<TenderSearchParams>({});
  
  // Single tender import state
  const [importUrl, setImportUrl] = useState('');
  const [isImporting, setIsImporting] = useState(false);
  
  const { data, isLoading, error, refetch } = useTenders(searchParams);
  
  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setSearchParams(prev => ({ ...prev, query: searchQuery }));
  };

  const handleImportSingle = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!importUrl.trim()) {
      toast.error('Veuillez entrer une URL');
      return;
    }
    
    setIsImporting(true);
    try {
      const result = await api.importSingleTender(importUrl.trim());
      if (result.success && result.data) {
        toast.success('Appel d\'offres importé avec succès');
        setImportUrl('');
        refetch();
        // Navigate to the tender detail page with analyze flag
        navigate(`/tender/${result.data.id}?analyze=true`);
      } else {
        toast.error(result.error || 'Erreur lors de l\'import');
      }
    } catch (err) {
      toast.error('Erreur lors de l\'import');
    } finally {
      setIsImporting(false);
    }
  };

  const tenders = data?.items || [];
  const totalCount = data?.total || 0;

  return (
    <AppLayout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-semibold">Appels d'Offres</h1>
            <p className="text-muted-foreground text-sm mt-1">
              {totalCount} appel{totalCount !== 1 ? 's' : ''} d'offres analysé{totalCount !== 1 ? 's' : ''}
            </p>
          </div>
        </div>

        {/* Test: Import Single Tender */}
        <div className="data-card p-4 border-dashed border-primary/30 bg-primary/5">
          <div className="flex items-center gap-2 mb-3">
            <FlaskConical className="w-4 h-4 text-primary" />
            <span className="text-sm font-medium">Test: Importer un appel d'offres</span>
          </div>
          <form onSubmit={handleImportSingle} className="flex gap-3">
            <div className="relative flex-1">
              <LinkIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <Input
                type="url"
                placeholder="https://www.marchespublics.gov.ma/index.php?page=entreprise.EntrepriseDetailsConsultation&refConsultation=..."
                value={importUrl}
                onChange={(e) => setImportUrl(e.target.value)}
                className="pl-10 font-mono text-xs"
                disabled={isImporting}
              />
            </div>
            <Button type="submit" disabled={isImporting || !importUrl.trim()}>
              {isImporting ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Import...
                </>
              ) : (
                <>
                  <FlaskConical className="w-4 h-4 mr-2" />
                  Importer & Analyser
                </>
              )}
            </Button>
          </form>
          <p className="text-xs text-muted-foreground mt-2">
            Collez l'URL directe d'un appel d'offres depuis marchespublics.gov.ma pour l'importer et lancer l'analyse.
          </p>
        </div>

        {/* Search & Filters */}
        <form onSubmit={handleSearch} className="flex gap-3">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <Input
              type="text"
              placeholder="Rechercher par référence, sujet, organisme..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-10"
            />
          </div>
          <Button type="submit" variant="secondary">
            <Filter className="w-4 h-4 mr-2" />
            Filtrer
          </Button>
        </form>

        {/* Error State */}
        {error && (
          <div className="data-card text-center py-8 border-destructive/50">
            <FileText className="w-10 h-10 text-destructive mx-auto mb-3" />
            <p className="text-destructive font-medium">Erreur de chargement</p>
            <p className="text-muted-foreground text-sm mt-1">
              {error instanceof Error ? error.message : 'Une erreur est survenue'}
            </p>
          </div>
        )}

        {/* Tender Table */}
        {!error && (
          <TenderTable tenders={tenders} isLoading={isLoading} />
        )}
      </div>
    </AppLayout>
  );
}
