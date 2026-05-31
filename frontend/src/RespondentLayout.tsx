import type { ReactNode } from 'react';

/**
 * Public page shell for the respondent-facing survey runner — a light branded
 * header over a centered content column. Deliberately separate from AdminLayout:
 * the respondent path carries no auth, nav, or operator affordances.
 */
export function RespondentLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen">
      <header className="border-b border-border bg-surface px-4 py-3 sm:px-6">
        <span className="text-base font-semibold text-brand-dark">Stele</span>
      </header>
      <main className="mx-auto max-w-2xl px-4 py-6 sm:py-10">{children}</main>
    </div>
  );
}
