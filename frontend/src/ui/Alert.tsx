import type { ReactNode } from 'react';

type Tone = 'error' | 'success' | 'info';

const TONES: Record<Tone, string> = {
  error: 'bg-danger-bg text-danger border-danger',
  success: 'bg-success-bg text-success border-success',
  info: 'bg-brand-light text-brand-dark border-brand',
};

/**
 * Inline status banner. `error` carries role="alert" (assertive); the others use
 * role="status" (polite) — matching how the views already announce errors vs
 * notices to assistive tech, so existing `getByRole('alert')` queries hold.
 */
export function Alert({ tone = 'info', children }: { tone?: Tone; children: ReactNode }) {
  return (
    <div
      role={tone === 'error' ? 'alert' : 'status'}
      className={`rounded-md border-l-4 px-3 py-2 text-sm ${TONES[tone]}`}
    >
      {children}
    </div>
  );
}
