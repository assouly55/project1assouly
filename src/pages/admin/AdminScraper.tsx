import { AdminLayout } from '@/components/layout/AdminLayout';
import { useState, useEffect } from 'react';
import { Play, Square, Calendar, Clock, Download, AlertCircle, CheckCircle2, ArrowRight, FlaskConical, Link as LinkIcon, Loader2 } from 'lucide-react';
import { StatCard } from '@/components/dashboard/StatCard';
import { Terminal } from '@/components/dashboard/Terminal';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useScraperStatus, useTriggerScraper, useStopScraper } from '@/hooks/useScraper';
import { useBackendHealth } from '@/hooks/useTenders';
import { api } from '@/lib/api';
import { toast } from 'sonner';

interface LogEntry {
  id: string;
  timestamp: string;
  level: 'info' | 'success' | 'warning' | 'error';
  message: string;
}

export default function AdminScraper() {
  const [startDate, setStartDate] = useState(() => {
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    return yesterday.toISOString().split('T')[0];
  });
  const [endDate, setEndDate] = useState(() => {
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    return yesterday.toISOString().split('T')[0];
  });

  const [importUrl, setImportUrl] = useState('');
  const [isImporting, setIsImporting] = useState(false);

  const [logs, setLogs] = useState<LogEntry[]>([{
    id: '1', timestamp: new Date().toLocaleTimeString('en-GB'),
    level: 'info', message: 'Scraper ready.',
  }]);

  const { data: isBackendOnline } = useBackendHealth();
  const { data: scraperStatus } = useScraperStatus();
  const triggerScraper = useTriggerScraper();
  const stopScraper = useStopScraper();
  const isRunning = scraperStatus?.is_running || false;

  useEffect(() => {
    if (scraperStatus?.logs && scraperStatus.logs.length > 0) {
      const newLogs: LogEntry[] = scraperStatus.logs.map((log, idx) => ({
        id: `server-${idx}`, timestamp: new Date().toLocaleTimeString('en-GB'),
        level: log.level as LogEntry['level'], message: log.message,
      }));
      setLogs(prev => {
        const existingMessages = new Set(prev.map(l => l.message));
        const unique = newLogs.filter(l => !existingMessages.has(l.message));
        return [...prev, ...unique];
      });
    }
  }, [scraperStatus?.logs]);

  const addLog = (level: LogEntry['level'], message: string) => {
    setLogs(prev => [...prev, { id: Date.now().toString(), timestamp: new Date().toLocaleTimeString('en-GB'), level, message }]);
  };

  const handleRunScraper = async () => {
    if (isRunning) return;
    setLogs([]);
    addLog('info', `Starting scraper: ${startDate} → ${endDate}`);
    try {
      const result = await triggerScraper.mutateAsync({ startDate, endDate });
      addLog('success', `Job submitted: ${result?.date_range || ''}`);
      toast.success('Scraper started');
    } catch (error) {
      addLog('error', `Failed: ${error instanceof Error ? error.message : 'Unknown'}`);
      toast.error('Failed to start scraper');
    }
  };

  const handleStopScraper = async () => {
    addLog('warning', 'Stopping scraper...');
    try {
      await stopScraper.mutateAsync();
      addLog('info', 'Scraper stopped');
    } catch (error) {
      addLog('error', `Failed: ${error instanceof Error ? error.message : 'Unknown'}`);
    }
  };

  const handleImportSingle = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!importUrl.trim()) return;
    setIsImporting(true);
    try {
      const result = await api.importSingleTender(importUrl.trim());
      if (result.success) {
        toast.success('Tender imported');
        setImportUrl('');
      } else {
        toast.error(result.error || 'Import failed');
      }
    } catch { toast.error('Import failed'); }
    finally { setIsImporting(false); }
  };

  const formatDate = (d: string) => { const [y, m, dd] = d.split('-'); return `${dd}/${m}/${y}`; };

  const stats = scraperStatus?.stats || {
    total: scraperStatus?.total_tenders || 0,
    downloaded: scraperStatus?.downloaded || 0,
    failed: scraperStatus?.failed || 0,
    elapsed: scraperStatus?.elapsed_seconds || 0,
  };

  return (
    <AdminLayout>
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-semibold">Contrôle Scraper</h1>
          <p className="text-muted-foreground text-sm mt-1">Télécharger les appels d'offres depuis marchespublics.gov.ma</p>
        </div>

        {/* Backend Status */}
        {isBackendOnline === false ? (
          <div className="flex items-start gap-3 p-4 rounded-lg bg-destructive/10 border border-destructive/20">
            <AlertCircle className="w-5 h-5 text-destructive shrink-0" />
            <div className="text-sm">
              <p className="font-medium text-destructive">Backend Hors Ligne</p>
              <p className="text-muted-foreground mt-1">Démarrez: <code className="px-1.5 py-0.5 bg-muted rounded font-mono text-xs">cd backend && python main.py</code></p>
            </div>
          </div>
        ) : (
          <div className="flex items-start gap-3 p-4 rounded-lg bg-success/10 border border-success/20">
            <CheckCircle2 className="w-5 h-5 text-success shrink-0" />
            <div className="text-sm"><p className="font-medium text-success">Backend En Ligne</p></div>
          </div>
        )}

        <div className="grid md:grid-cols-2 gap-6">
          {/* Config */}
          <div className="data-card space-y-4">
            <h2 className="font-medium">Configuration — Date de mise en ligne</h2>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>Début</Label>
                <div className="relative">
                  <Calendar className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <Input type="date" value={startDate} onChange={e => { setStartDate(e.target.value); if (e.target.value > endDate) setEndDate(e.target.value); }} className="pl-10" disabled={isRunning} />
                </div>
              </div>
              <div className="space-y-2">
                <Label>Fin</Label>
                <div className="relative">
                  <Calendar className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <Input type="date" value={endDate} min={startDate} onChange={e => setEndDate(e.target.value)} className="pl-10" disabled={isRunning} />
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2 text-sm text-muted-foreground bg-muted/50 p-3 rounded-md">
              <span className="font-mono">{formatDate(startDate)}</span>
              <ArrowRight className="w-4 h-4" />
              <span className="font-mono">{formatDate(endDate)}</span>
            </div>
            <div className="flex gap-3">
              {!isRunning ? (
                <Button onClick={handleRunScraper} className="flex-1" disabled={triggerScraper.isPending || isBackendOnline === false}>
                  <Play className="w-4 h-4 mr-2" />{triggerScraper.isPending ? 'Démarrage...' : 'Lancer le Scraper'}
                </Button>
              ) : (
                <Button onClick={handleStopScraper} variant="destructive" className="flex-1" disabled={stopScraper.isPending}>
                  <Square className="w-4 h-4 mr-2" />{stopScraper.isPending ? 'Arrêt...' : 'Arrêter'}
                </Button>
              )}
            </div>
          </div>

          {/* Stats */}
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <StatCard label="Trouvés" value={stats.total} icon={<Download className="w-4 h-4" />} />
              <StatCard label="Téléchargés" value={stats.downloaded} variant="success" />
              <StatCard label="Échoués" value={stats.failed} variant="destructive" />
              <StatCard label="Durée" value={`${(stats.elapsed || 0).toFixed(1)}s`} icon={<Clock className="w-4 h-4" />} />
            </div>
          </div>
        </div>

        {/* Single Import */}
        <div className="data-card p-4 border-dashed border-primary/30 bg-primary/5">
          <div className="flex items-center gap-2 mb-3">
            <FlaskConical className="w-4 h-4 text-primary" />
            <span className="text-sm font-medium">Import Direct</span>
          </div>
          <form onSubmit={handleImportSingle} className="flex gap-3">
            <div className="relative flex-1">
              <LinkIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <Input type="url" placeholder="URL de l'appel d'offres..." value={importUrl} onChange={e => setImportUrl(e.target.value)} className="pl-10 font-mono text-xs" disabled={isImporting} />
            </div>
            <Button type="submit" disabled={isImporting || !importUrl.trim()}>
              {isImporting ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <FlaskConical className="w-4 h-4 mr-2" />}
              Importer
            </Button>
          </form>
        </div>

        <Terminal title="Sortie Scraper" logs={logs} maxHeight="400px" />
      </div>
    </AdminLayout>
  );
}
