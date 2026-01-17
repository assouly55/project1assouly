import { cn } from '@/lib/utils';
import type { TenderStatus } from '@/types/tender';

interface StatusBadgeProps {
  status: TenderStatus;
  className?: string;
}

const statusConfig: Record<TenderStatus, { label: string; className: string }> = {
  PENDING: { label: 'Pending', className: 'badge-pending' },
  LISTED: { label: 'Listed', className: 'badge-listed' },
  ANALYZED: { label: 'Analyzed', className: 'badge-analyzed' },
  ERROR: { label: 'Error', className: 'badge-error' },
};

export function StatusBadge({ status, className }: StatusBadgeProps) {
  const config = statusConfig[status];
  
  return (
    <span 
      className={cn(
        "inline-flex items-center px-2 py-0.5 rounded text-xs font-medium",
        config.className,
        className
      )}
    >
      {config.label}
    </span>
  );
}
