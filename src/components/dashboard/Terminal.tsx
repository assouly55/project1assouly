import { useRef, useEffect } from 'react';
import { cn } from '@/lib/utils';

interface LogEntry {
  id: string;
  timestamp: string;
  level: 'info' | 'success' | 'warning' | 'error';
  message: string;
}

interface TerminalProps {
  title?: string;
  logs: LogEntry[];
  maxHeight?: string;
  className?: string;
}

const levelColors = {
  info: 'text-primary',
  success: 'text-success',
  warning: 'text-warning',
  error: 'text-destructive',
};

const levelIcons = {
  info: 'ℹ',
  success: '✓',
  warning: '⚠',
  error: '✗',
};

export function Terminal({ title = 'Terminal', logs, maxHeight = '300px', className }: TerminalProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs]);

  return (
    <div className={cn("terminal", className)}>
      <div className="terminal-header">
        <div className="terminal-dot bg-destructive" />
        <div className="terminal-dot bg-warning" />
        <div className="terminal-dot bg-success" />
        <span className="ml-2 text-xs text-muted-foreground font-mono">{title}</span>
      </div>
      <div 
        ref={scrollRef}
        className="overflow-auto p-4 space-y-1"
        style={{ maxHeight }}
      >
        {logs.length === 0 ? (
          <div className="text-muted-foreground text-sm">No logs yet...</div>
        ) : (
          logs.map((log) => (
            <div key={log.id} className="flex gap-2 text-sm font-mono">
              <span className="text-muted-foreground shrink-0">
                {log.timestamp}
              </span>
              <span className={cn("shrink-0", levelColors[log.level])}>
                {levelIcons[log.level]}
              </span>
              <span className={levelColors[log.level]}>
                {log.message}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
