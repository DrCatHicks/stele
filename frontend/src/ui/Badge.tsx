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
 * status (draft/published) and review decisions (pending/promoted/rejected)
 * colour consistently wherever they appear.
 */
export function statusTone(status: string): Tone {
  switch (status) {
    case 'published':
    case 'promoted':
      return 'success';
    case 'rejected':
      return 'danger';
    case 'pending':
      return 'warning';
    case 'draft':
      return 'brand';
    default:
      return 'neutral';
  }
}
