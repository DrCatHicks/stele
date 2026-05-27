import { useEffect, useState } from 'react';

import {
  listWithdrawals,
  triggerWithdrawal,
  type WithdrawalAudit,
  type WithdrawalResult,
} from '../api';
import { useAuth } from '../auth/AuthContext';
import { formatDateTime } from '../format';
import { Alert, Button, Card, CardBody, EmptyState, Field, LoadingState, PageHeader } from '../ui';

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Admin GDPR console: trigger respondent erasure (wired to the M2.2 withdrawal
 * endpoint) and list the pii.withdrawals audit. Erasure is irreversible, so the
 * trigger asks for an explicit confirm. Reviewer/author roles never reach the
 * endpoint (403); this view also hides itself from them.
 */
export function GdprView() {
  const { user } = useAuth();
  const [audit, setAudit] = useState<WithdrawalAudit[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [respondentId, setRespondentId] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [lastResult, setLastResult] = useState<WithdrawalResult | null>(null);

  const refresh = (): void => {
    listWithdrawals()
      .then((rows) => {
        setAudit(rows);
        setError(null);
      })
      .catch((err: unknown) => setError(errorMessage(err)));
  };

  useEffect(() => {
    let active = true;
    listWithdrawals()
      .then((rows) => {
        if (active) setAudit(rows);
      })
      .catch((err: unknown) => {
        if (active) setError(errorMessage(err));
      });
    return () => {
      active = false;
    };
  }, []);

  if (user && user.role !== 'admin') {
    return <Alert tone="error">Only admins can access the GDPR console.</Alert>;
  }

  const handleErase = (): void => {
    const id = respondentId.trim();
    if (!id) return;
    if (
      !window.confirm(
        `Erase all data for respondent ${id}? This is irreversible: their responses ` +
          `are tombstoned and PII deleted across every survey.`,
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    setLastResult(null);
    triggerWithdrawal(id, reason.trim() || undefined)
      .then((result) => {
        setLastResult(result);
        setRespondentId('');
        setReason('');
        refresh();
      })
      .catch((err: unknown) => setError(errorMessage(err)))
      .finally(() => setBusy(false));
  };

  return (
    <section>
      <PageHeader
        title="GDPR / erasure"
        subtitle="Permanently erase a respondent's data and review the erasure audit."
      />

      <Card className="mb-6">
        <CardBody className="flex flex-col gap-4">
          <h2 className="text-sm font-semibold text-ink">Erase a respondent</h2>
          <div className="flex flex-wrap items-end gap-3">
            <Field
              label="Respondent ID"
              className="min-w-56 flex-1"
              value={respondentId}
              onChange={(e) => setRespondentId(e.target.value)}
              placeholder="respondent UUID"
            />
            <Field
              label="Reason"
              className="min-w-56 flex-1"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="ticket reference (optional)"
            />
            <Button
              type="button"
              variant="danger"
              onClick={handleErase}
              disabled={busy || !respondentId.trim()}
            >
              {busy ? 'Erasing…' : 'Erase respondent'}
            </Button>
          </div>
          {lastResult ? (
            <Alert tone="success">
              {lastResult.already_withdrawn
                ? 'Already withdrawn — no further data to erase.'
                : `Erased: ${lastResult.raw_rows_tombstoned} raw row(s) tombstoned, ` +
                  `${lastResult.responses_purged} response(s) purged, ` +
                  `${lastResult.pii_rows_deleted} PII row(s) deleted.`}
            </Alert>
          ) : null}
        </CardBody>
      </Card>

      {error ? <Alert tone="error">Error: {error}</Alert> : null}

      <h2 className="mb-2 text-sm font-semibold text-ink">Erasure audit</h2>
      {audit === null ? (
        error ? null : (
          <LoadingState />
        )
      ) : audit.length === 0 ? (
        <EmptyState>No withdrawals recorded.</EmptyState>
      ) : (
        <Card>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-faint">
                <th className="px-5 py-2 font-medium">Respondent</th>
                <th className="px-5 py-2 font-medium">Requested</th>
                <th className="px-5 py-2 font-medium">Reason</th>
              </tr>
            </thead>
            <tbody>
              {audit.map((w) => (
                <tr key={w.id} className="border-t border-border">
                  <td className="px-5 py-2 font-mono text-xs text-muted">{w.respondent_id}</td>
                  <td className="px-5 py-2 text-muted">{formatDateTime(w.requested_at)}</td>
                  <td className="px-5 py-2 text-ink">{w.reason ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </section>
  );
}
