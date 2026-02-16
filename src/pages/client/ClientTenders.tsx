import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Search, Filter, FileText } from 'lucide-react';
import { ClientLayout } from '@/components/layout/ClientLayout';
import { TenderTable } from '@/components/tenders/TenderTable';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { useTenders } from '@/hooks/useTenders';
import type { TenderSearchParams } from '@/types/tender';

export default function ClientTenders() {
  const [searchQuery, setSearchQuery] = useState('');
  const [searchParams, setSearchParams] = useState<TenderSearchParams>({
    status: 'ANALYZED', // Clients only see analyzed tenders
  });

  const { data, isLoading, error } = useTenders(searchParams);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setSearchParams(prev => ({ ...prev, query: searchQuery }));
  };

  const tenders = data?.items || [];
  const totalCount = data?.total || 0;

  return (
    <ClientLayout>
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-semibold">Appels d'Offres</h1>
          <p className="text-muted-foreground text-sm mt-1">
            {totalCount} appel{totalCount !== 1 ? 's' : ''} d'offres disponible{totalCount !== 1 ? 's' : ''}
          </p>
        </div>

        {/* Search */}
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

        {error && (
          <div className="data-card text-center py-8 border-destructive/50">
            <FileText className="w-10 h-10 text-destructive mx-auto mb-3" />
            <p className="text-destructive font-medium">Erreur de chargement</p>
          </div>
        )}

        {!error && <TenderTable tenders={tenders} isLoading={isLoading} />}
      </div>
    </ClientLayout>
  );
}
