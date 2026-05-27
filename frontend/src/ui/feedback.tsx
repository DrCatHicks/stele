import type { ReactNode } from 'react';

/** Centered "Loading…" placeholder; role="status" so the existing queries hold. */
export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 py-8 text-muted">
      <span
        aria-hidden
        className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-border border-t-brand"
      />
      {label}
    </div>
  );
}

/** Empty-list placeholder inside a dashed surface. */
export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-border bg-surface px-4 py-10 text-center text-muted">
      {children}
    </div>
  );
}
