import type { ReactNode } from 'react';

type Tone = 'neutral' | 'brand' | 'success' | 'danger' | 'warning';

const TONES: Record<Tone, string> = {
  neutral: 'bg-canvas text-muted border-border',
  brand: 'bg-brand-light text-brand-dark border-brand-light',
  success: 'bg-success-bg text-success border-success-bg',
  danger: 'bg-danger-bg text-danger border-danger-bg',
  warning: 'bg-warning-bg text-warning border-warning-bg',
};

export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={`inline-block rounded-full border px-2 py-0.5 text-xs font-semibold capitalize ${TONES[tone]}`}
    >
      {children}
    </span>
  );
}

/**
 * Map the domain statuses the operator UI displays to a badge tone, so survey
 * status (draft/published), review decisions (pending/promoted/rejected), and ETL
 * run outcomes (running/success/failed) colour consistently wherever they appear.
 */
export function statusTone(status: string): Tone {
  switch (status) {
    case 'published':
    case 'promoted':
    case 'success':
      return 'success';
    case 'rejected':
    case 'failed':
      return 'danger';
    case 'pending':
    case 'running':
      return 'warning';
    case 'draft':
      return 'brand';
    // 'scrubbed' is terminal/erased — neutral grey, distinct from a rejection.
    case 'scrubbed':
      return 'neutral';
    default:
      return 'neutral';
  }
}
