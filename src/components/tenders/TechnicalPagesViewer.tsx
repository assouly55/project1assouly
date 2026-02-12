import { useState, useEffect, useCallback } from 'react';
import { FileSearch, Download, X, Loader2, AlertCircle, CheckCircle2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { api } from '@/lib/api';
import type { TechnicalPagesResponse } from '@/lib/api';

interface TechnicalPagesViewerProps {
  tenderId: string;
  tenderReference: string;
}

type ExtractionState = 'idle' | 'extracting' | 'done' | 'error';

export function TechnicalPagesViewer({ tenderId, tenderReference }: TechnicalPagesViewerProps) {
  const [state, setState] = useState<ExtractionState>('idle');
  const [progress, setProgress] = useState(0);
  const [progressMessage, setProgressMessage] = useState('');
  const [result, setResult] = useState<TechnicalPagesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);

  // Cleanup PDF blob URL on unmount
  useEffect(() => {
    return () => {
      if (pdfUrl) {
        URL.revokeObjectURL(pdfUrl);
      }
    };
  }, [pdfUrl]);

  const handleExtract = useCallback(async () => {
    setState('extracting');
    setError(null);
    setResult(null);
    setProgress(10);
    setProgressMessage('🔍 AI analyse les documents pour identifier les spécifications techniques...');

    const progressInterval = setInterval(() => {
      setProgress(prev => {
        if (prev >= 85) return prev;
        const increment = Math.random() * 8;
        const newVal = Math.min(prev + increment, 85);

        if (newVal < 30) setProgressMessage('🔍 AI analyse les documents pour identifier les spécifications techniques...');
        else if (newVal < 50) setProgressMessage('📄 Identification du document et des pages...');
        else if (newVal < 70) setProgressMessage('📥 Re-téléchargement et extraction des pages...');
        else setProgressMessage('📑 Génération du PDF...');

        return newVal;
      });
    }, 800);

    try {
      const response = await api.extractTechnicalPages(tenderId);
      clearInterval(progressInterval);

      if (response.success && response.data?.success && response.data.pdf_base64) {
        setProgress(100);
        setProgressMessage('✅ Extraction terminée!');
        setResult(response.data);

        // Convert base64 to blob URL
        const byteCharacters = atob(response.data.pdf_base64);
        const byteNumbers = new Array(byteCharacters.length);
        for (let i = 0; i < byteCharacters.length; i++) {
          byteNumbers[i] = byteCharacters.charCodeAt(i);
        }
        const byteArray = new Uint8Array(byteNumbers);
        const blob = new Blob([byteArray], { type: 'application/pdf' });
        setPdfUrl(URL.createObjectURL(blob));

        setState('done');
      } else {
        setError(response.data?.error || response.error || 'Extraction échouée');
        setState('error');
      }
    } catch (err) {
      clearInterval(progressInterval);
      setError(err instanceof Error ? err.message : 'Erreur réseau');
      setState('error');
    }
  }, [tenderId]);

  const handleDownload = useCallback(() => {
    if (!pdfUrl) return;
    const a = document.createElement('a');
    a.href = pdfUrl;
    a.download = `${tenderReference}_specs_techniques.pdf`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }, [pdfUrl, tenderReference]);

  const handleClose = useCallback(() => {
    if (pdfUrl) {
      URL.revokeObjectURL(pdfUrl);
      setPdfUrl(null);
    }
    setResult(null);
    setState('idle');
    setProgress(0);
    setError(null);
  }, [pdfUrl]);

  return (
    <>
      {/* Trigger button — always visible in header area */}
      <Button
        variant="outline"
        size="sm"
        onClick={handleExtract}
        disabled={state === 'extracting'}
        className="gap-2"
      >
        {state === 'extracting' ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : (
          <FileSearch className="w-4 h-4" />
        )}
        {state === 'extracting' ? 'Extraction...' : 'Specs Techniques'}
      </Button>

      {/* Extraction panel — renders below via portal-like pattern */}
      {state !== 'idle' && (
        <TechnicalPagesPanel
          state={state}
          progress={progress}
          progressMessage={progressMessage}
          result={result}
          error={error}
          pdfUrl={pdfUrl}
          onRetry={handleExtract}
          onClose={handleClose}
          onDownload={handleDownload}
        />
      )}
    </>
  );
}

/** Standalone panel that displays extraction progress / result / PDF viewer */
function TechnicalPagesPanel({
  state,
  progress,
  progressMessage,
  result,
  error,
  pdfUrl,
  onRetry,
  onClose,
  onDownload,
}: {
  state: ExtractionState;
  progress: number;
  progressMessage: string;
  result: TechnicalPagesResponse | null;
  error: string | null;
  pdfUrl: string | null;
  onRetry: () => void;
  onClose: () => void;
  onDownload: () => void;
}) {
  if (state === 'extracting') {
    return (
      <div className="fixed bottom-6 right-6 z-50 w-96 bg-card border border-border rounded-lg p-5 shadow-xl space-y-3">
        <div className="flex items-center gap-3">
          <Loader2 className="w-5 h-5 animate-spin text-primary flex-shrink-0" />
          <div className="text-sm font-medium">Extraction des pages techniques</div>
        </div>
        <Progress value={progress} className="h-2" />
        <p className="text-xs text-muted-foreground">{progressMessage}</p>
      </div>
    );
  }

  if (state === 'error') {
    return (
      <div className="fixed bottom-6 right-6 z-50 w-96 bg-card border border-destructive/30 rounded-lg p-5 shadow-xl space-y-3">
        <div className="flex items-center gap-3">
          <AlertCircle className="w-5 h-5 text-destructive flex-shrink-0" />
          <div className="text-sm font-medium">Extraction échouée</div>
        </div>
        <p className="text-xs text-muted-foreground">{error}</p>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={onRetry}>Réessayer</Button>
          <Button variant="ghost" size="sm" onClick={onClose}>Fermer</Button>
        </div>
      </div>
    );
  }

  // Done — full-page PDF viewer overlay
  if (state === 'done' && pdfUrl) {
    return (
      <div className="fixed inset-0 z-50 bg-background/95 backdrop-blur-sm flex flex-col">
        {/* Top bar */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border bg-card">
          <div className="flex items-center gap-3">
            <CheckCircle2 className="w-5 h-5 text-success" />
            <div>
              <div className="text-sm font-medium">Spécifications Techniques</div>
              <div className="text-xs text-muted-foreground">
                {result?.page_count} page{(result?.page_count || 0) > 1 ? 's' : ''} — {result?.source_document}
              </div>
            </div>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={onDownload} className="gap-2">
              <Download className="w-4 h-4" />
              Télécharger PDF
            </Button>
            <Button variant="ghost" size="sm" onClick={onClose}>
              <X className="w-4 h-4" />
            </Button>
          </div>
        </div>

        {/* AI reasoning bar */}
        {result?.reasoning && (
          <div className="px-6 py-2 bg-muted/30 border-b border-border">
            <p className="text-xs text-muted-foreground">🤖 {result.reasoning}</p>
          </div>
        )}

        {/* PDF iframe — fills remaining space */}
        <div className="flex-1 overflow-hidden">
          <iframe
            src={pdfUrl}
            className="w-full h-full border-0"
            title="Spécifications Techniques PDF"
          />
        </div>
      </div>
    );
  }

  return null;
}
