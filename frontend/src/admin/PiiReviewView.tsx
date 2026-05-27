import { useCallback, useEffect, useState } from 'react';

import {
  listFreeTextForReview,
  promoteFreeText,
  rejectFreeText,
  scrubFreeText,
  type FreeTextReviewItem,
  type ReviewStatus,
} from '../api';
import { useAuth } from '../auth/AuthContext';
import { formatDate } from '../format';
import {
  Alert,
  Badge,
  Button,
  Card,
  EmptyState,
  LoadingState,
  PageHeader,
  statusTone,
} from '../ui';

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

const TABS: ReviewStatus[] = ['pending', 'promoted', 'rejected', 'scrubbed'];

/**
 * Reviewer PII-screening console (design §3.9/§3.10): screen high-risk free-text
 * answers and promote the safe ones to the marts, or reject them. Only the
 * reviewer role reaches the endpoints (403 otherwise); this view also hides
 * itself from other roles. Promotion takes effect in the marts on the next dbt
 * build, so the UI says so rather than implying an instant analyst-visible change.
 */
export function PiiReviewView() {
  const { user } = useAuth();
  const [status, setStatus] = useState<ReviewStatus>('pending');
  const [items, setItems] = useState<FreeTextReviewItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = useCallback((next: ReviewStatus): void => {
    setItems(null);
    setError(null);
    listFreeTextForReview(next)
      .then(setItems)
      .catch((err: unknown) => setError(errorMessage(err)));
  }, []);

  useEffect(() => {
    let active = true;
    setError(null);
    listFreeTextForReview(status)
      .then((rows) => {
        if (active) setItems(rows);
      })
      .catch((err: unknown) => {
        if (active) setError(errorMessage(err));
      });
    return () => {
      active = false;
    };
  }, [status]);

  if (user && user.role !== 'reviewer') {
    return <Alert tone="error">Only reviewers can screen free-text PII.</Alert>;
  }

  const decide = (id: number, action: 'promote' | 'reject'): void => {
    setBusyId(id);
    setError(null);
    const call = action === 'promote' ? promoteFreeText(id) : rejectFreeText(id);
    call
      .then(() => load(status))
      .catch((err: unknown) => setError(errorMessage(err)))
      .finally(() => setBusyId(null));
  };

  const scrub = (item: FreeTextReviewItem): void => {
    // Destructive and irreversible: the PII is destroyed in raw, read-model, and
    // the PII copy. Confirm before firing.
    const ok = window.confirm(
      `Permanently scrub the answer to "${item.question_name}"? ` +
        'This destroys the text everywhere it is stored and cannot be undone.',
    );
    if (!ok) return;
    setBusyId(item.id);
    setError(null);
    scrubFreeText(item.id)
      .then(() => load(status))
      .catch((err: unknown) => setError(errorMessage(err)))
      .finally(() => setBusyId(null));
  };

  const tabClass = (tab: ReviewStatus): string =>
    [
      'rounded-full px-3 py-1 text-sm font-medium capitalize transition-colors',
      status === tab ? 'bg-brand text-white' : 'bg-canvas text-muted hover:text-ink',
    ].join(' ');

  return (
    <section>
      <PageHeader
        title="Free-text PII review"
        subtitle="Promoted answers reach the analyst marts on the next ETL build. The default is redacted — promote only answers screened free of PII and proprietary content. Scrub permanently destroys an answer's text everywhere it is stored; the response itself is kept."
      />

      <div role="tablist" className="mb-4 flex gap-2">
        {TABS.map((tab) => (
          <button
            key={tab}
            type="button"
            role="tab"
            aria-selected={status === tab}
            onClick={() => setStatus(tab)}
            className={tabClass(tab)}
          >
            {tab}
          </button>
        ))}
      </div>

      {error ? <Alert tone="error">Error: {error}</Alert> : null}

      {items === null ? (
        error ? null : (
          <LoadingState />
        )
      ) : items.length === 0 ? (
        <EmptyState>No {status} answers.</EmptyState>
      ) : (
        <Card>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-faint">
                <th className="px-5 py-2 font-medium">Respondent</th>
                <th className="px-5 py-2 font-medium">Question</th>
                <th className="px-5 py-2 font-medium">Answer</th>
                <th className="px-5 py-2 font-medium">Submitted</th>
                <th className="px-5 py-2 font-medium">
                  {status === 'pending' ? 'Decision' : 'Status'}
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id} className="border-t border-border align-top">
                  <td className="px-5 py-3 font-mono text-xs text-muted">{item.respondent_id}</td>
                  <td className="px-5 py-3 text-ink">{item.question_name}</td>
                  <td className="px-5 py-3 text-ink">{item.value_text ?? '—'}</td>
                  <td className="px-5 py-3 text-muted">{formatDate(item.created_at)}</td>
                  <td className="px-5 py-3">
                    {status === 'scrubbed' ? (
                      <Badge tone={statusTone('scrubbed')}>scrubbed</Badge>
                    ) : (
                      <div className="flex flex-wrap items-center gap-2">
                        {status === 'pending' ? (
                          <>
                            <Button
                              type="button"
                              size="sm"
                              onClick={() => decide(item.id, 'promote')}
                              disabled={busyId === item.id}
                            >
                              Promote
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              variant="danger"
                              onClick={() => decide(item.id, 'reject')}
                              disabled={busyId === item.id}
                            >
                              Reject
                            </Button>
                          </>
                        ) : (
                          <Badge tone={statusTone(status)}>{status}</Badge>
                        )}
                        {/* Scrub stays available on pending/promoted/rejected: the
                            PII persists in storage until it is scrubbed, whatever
                            the review decision. */}
                        <Button
                          type="button"
                          size="sm"
                          variant="danger"
                          onClick={() => scrub(item)}
                          disabled={busyId === item.id}
                        >
                          Scrub
                        </Button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </section>
  );
}
