import { useState, useEffect } from 'react';
import { Play, Square, Calendar, Clock, Download, AlertCircle, CheckCircle2, ArrowRight } from 'lucide-react';
import { AppLayout } from '@/components/layout/AppLayout';
import { StatCard } from '@/components/dashboard/StatCard';
import { Terminal } from '@/components/dashboard/Terminal';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useScraperStatus, useTriggerScraper, useStopScraper } from '@/hooks/useScraper';
import { useBackendHealth } from '@/hooks/useTenders';
import { toast } from 'sonner';

interface LogEntry {
  id: string;
  timestamp: string;
  level: 'info' | 'success' | 'warning' | 'error';
  message: string;
}

export default function Scraper() {
  // Date range state
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
  
  const [logs, setLogs] = useState<LogEntry[]>([
    {
      id: '1',
      timestamp: new Date().toLocaleTimeString('en-GB'),
      level: 'info',
      message: 'Scraper ready. Select date range and click "Run Scraper" to start.',
    },
  ]);

  const { data: isBackendOnline } = useBackendHealth();
  const { data: scraperStatus } = useScraperStatus();
  const triggerScraper = useTriggerScraper();
  const stopScraper = useStopScraper();

  const isRunning = scraperStatus?.is_running || false;

  // Update logs based on scraper status
  useEffect(() => {
    if (scraperStatus?.logs && scraperStatus.logs.length > 0) {
      const newLogs: LogEntry[] = scraperStatus.logs.map((log, idx) => ({
        id: `server-${idx}`,
        timestamp: new Date().toLocaleTimeString('en-GB'),
        level: log.level as LogEntry['level'],
        message: log.message,
      }));
      setLogs(prev => {
        const existingMessages = new Set(prev.map(l => l.message));
        const unique = newLogs.filter(l => !existingMessages.has(l.message));
        return [...prev, ...unique];
      });
    }
  }, [scraperStatus?.logs]);

  const addLog = (level: LogEntry['level'], message: string) => {
    const now = new Date().toLocaleTimeString('en-GB');
    setLogs(prev => [...prev, {
      id: Date.now().toString(),
      timestamp: now,
      level,
      message,
    }]);
  };

  const handleRunScraper = async () => {
    if (isRunning) return;
    
    setLogs([]);
    addLog('info', `Starting scraper...`);
    addLog('info', `Date de mise en ligne: ${formatDate(startDate)} → ${formatDate(endDate)}`);
    addLog('info', 'Category filter: Fournitures (2)');
    
    try {
      const result = await triggerScraper.mutateAsync({ startDate, endDate });
      addLog('success', `Scraper job submitted: ${result?.date_range || ''}`);
      toast.success('Scraper started');
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unknown error';
      addLog('error', `Failed to start scraper: ${message}`);
      toast.error(`Failed to start scraper: ${message}`);
    }
  };

  const handleStopScraper = async () => {
    addLog('warning', 'Stopping scraper...');
    
    try {
      await stopScraper.mutateAsync();
      addLog('info', 'Scraper stopped');
      toast.info('Scraper stopped');
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unknown error';
      addLog('error', `Failed to stop scraper: ${message}`);
    }
  };

  const formatDate = (dateStr: string) => {
    const [year, month, day] = dateStr.split('-');
    return `${day}/${month}/${year}`;
  };

  const stats = scraperStatus?.stats || {
    total: scraperStatus?.total_tenders || 0,
    downloaded: scraperStatus?.downloaded || 0,
    failed: scraperStatus?.failed || 0,
    elapsed: scraperStatus?.elapsed_seconds || 0,
  };

  return (
    <AppLayout>
      <div className="space-y-6">
        {/* Header */}
        <div>
          <h1 className="text-2xl font-semibold">Scraper Control</h1>
          <p className="text-muted-foreground text-sm mt-1">
            Download tenders from marchespublics.gov.ma
          </p>
        </div>

        {/* Backend Status Alert */}
        {isBackendOnline === false ? (
          <div className="flex items-start gap-3 p-4 rounded-lg bg-destructive/10 border border-destructive/20">
            <AlertCircle className="w-5 h-5 text-destructive shrink-0 mt-0.5" />
            <div className="text-sm">
              <p className="font-medium text-destructive">Backend Offline</p>
              <p className="text-muted-foreground mt-1">
                Start the backend: <code className="mx-1 px-1.5 py-0.5 bg-muted rounded font-mono text-xs">cd backend && python main.py</code>
              </p>
            </div>
          </div>
        ) : (
          <div className="flex items-start gap-3 p-4 rounded-lg bg-success/10 border border-success/20">
            <CheckCircle2 className="w-5 h-5 text-success shrink-0 mt-0.5" />
            <div className="text-sm">
              <p className="font-medium text-success">Backend Online</p>
              <p className="text-muted-foreground mt-1">
                Connected to <code className="mx-1 px-1.5 py-0.5 bg-muted rounded font-mono text-xs">localhost:8000</code>
              </p>
            </div>
          </div>
        )}

        {/* Controls */}
        <div className="grid md:grid-cols-2 gap-6">
          {/* Configuration */}
          <div className="data-card space-y-4">
            <h2 className="font-medium">Configuration — Date de mise en ligne</h2>
            
            {/* Date Range Inputs */}
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="startDate">Start Date</Label>
                <div className="relative">
                  <Calendar className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <Input
                    id="startDate"
                    type="date"
                    value={startDate}
                    onChange={(e) => {
                      setStartDate(e.target.value);
                      // Auto-update end date if it's before start
                      if (e.target.value > endDate) {
                        setEndDate(e.target.value);
                      }
                    }}
                    className="pl-10"
                    disabled={isRunning}
                  />
                </div>
              </div>
              
              <div className="space-y-2">
                <Label htmlFor="endDate">End Date</Label>
                <div className="relative">
                  <Calendar className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <Input
                    id="endDate"
                    type="date"
                    value={endDate}
                    min={startDate}
                    onChange={(e) => setEndDate(e.target.value)}
                    className="pl-10"
                    disabled={isRunning}
                  />
                </div>
              </div>
            </div>

            {/* Date Preview */}
            <div className="flex items-center gap-2 text-sm text-muted-foreground bg-muted/50 p-3 rounded-md">
              <span className="font-mono">{formatDate(startDate)}</span>
              <ArrowRight className="w-4 h-4" />
              <span className="font-mono">{formatDate(endDate)}</span>
              <span className="ml-auto text-xs">
                {startDate === endDate ? '(1 day)' : `(${Math.ceil((new Date(endDate).getTime() - new Date(startDate).getTime()) / (1000 * 60 * 60 * 24)) + 1} days)`}
              </span>
            </div>

            <div className="pt-2 flex gap-3">
              {!isRunning ? (
                <Button 
                  onClick={handleRunScraper} 
                  className="flex-1"
                  disabled={triggerScraper.isPending || isBackendOnline === false}
                >
                  <Play className="w-4 h-4 mr-2" />
                  {triggerScraper.isPending ? 'Starting...' : 'Run Scraper'}
                </Button>
              ) : (
                <Button 
                  onClick={handleStopScraper} 
                  variant="destructive" 
                  className="flex-1"
                  disabled={stopScraper.isPending}
                >
                  <Square className="w-4 h-4 mr-2" />
                  {stopScraper.isPending ? 'Stopping...' : 'Stop Scraper'}
                </Button>
              )}
            </div>
          </div>

          {/* Stats */}
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <StatCard 
                label="Total Found" 
                value={stats.total} 
                icon={<Download className="w-4 h-4" />}
              />
              <StatCard 
                label="Downloaded" 
                value={stats.downloaded} 
                variant="success"
              />
              <StatCard 
                label="Failed" 
                value={stats.failed} 
                variant="destructive"
              />
              <StatCard 
                label="Elapsed" 
                value={`${(stats.elapsed || 0).toFixed(1)}s`}
                icon={<Clock className="w-4 h-4" />}
              />
            </div>
          </div>
        </div>

        {/* Terminal */}
        <Terminal 
          title="Scraper Output" 
          logs={logs} 
          maxHeight="400px" 
        />
      </div>
    </AppLayout>
  );
}
