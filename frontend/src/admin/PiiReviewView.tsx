import { useCallback, useEffect, useState } from 'react';

import {
  listFreeTextForReview,
  promoteFreeText,
  rejectFreeText,
  type FreeTextReviewItem,
  type ReviewStatus,
} from '../api';
import { useAuth } from '../auth/AuthContext';

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

const TABS: ReviewStatus[] = ['pending', 'promoted', 'rejected'];

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
    listFreeTextForReview(next)
      .then(setItems)
      .catch((err: unknown) => setError(errorMessage(err)));
  }, []);

  useEffect(() => {
    let active = true;
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
    return <div role="alert">Only reviewers can screen free-text PII.</div>;
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

  return (
    <section>
      <h1>Free-text PII review</h1>
      <p>
        Promoted answers reach the analyst marts on the next ETL build. The default is redacted —
        promote only answers screened free of PII and proprietary content.
      </p>

      <div role="tablist" style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem' }}>
        {TABS.map((tab) => (
          <button
            key={tab}
            type="button"
            role="tab"
            aria-selected={status === tab}
            onClick={() => setStatus(tab)}
            disabled={status === tab}
          >
            {tab}
          </button>
        ))}
      </div>

      {error ? <div role="alert">Error: {error}</div> : null}

      {items === null ? (
        <div role="status">Loading…</div>
      ) : items.length === 0 ? (
        <p>No {status} answers.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Respondent</th>
              <th>Question</th>
              <th>Answer</th>
              <th>Submitted</th>
              {status === 'pending' ? <th>Decision</th> : null}
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id}>
                <td>{item.respondent_id}</td>
                <td>{item.question_name}</td>
                <td>{item.value_text ?? '—'}</td>
                <td>{new Date(item.created_at).toLocaleString()}</td>
                {status === 'pending' ? (
                  <td>
                    <button
                      type="button"
                      onClick={() => decide(item.id, 'promote')}
                      disabled={busyId === item.id}
                    >
                      Promote
                    </button>{' '}
                    <button
                      type="button"
                      onClick={() => decide(item.id, 'reject')}
                      disabled={busyId === item.id}
                    >
                      Reject
                    </button>
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
