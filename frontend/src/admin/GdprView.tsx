import { useEffect, useState } from 'react';

import {
  listWithdrawals,
  triggerWithdrawal,
  type WithdrawalAudit,
  type WithdrawalResult,
} from '../api';
import { useAuth } from '../auth/AuthContext';

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
      .then(setAudit)
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
    return <div role="alert">Only admins can access the GDPR console.</div>;
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
      <h1>GDPR / erasure</h1>

      <div style={{ marginBottom: '1.5rem' }}>
        <h2>Erase a respondent</h2>
        <label>
          Respondent ID{' '}
          <input
            type="text"
            value={respondentId}
            onChange={(e) => setRespondentId(e.target.value)}
            placeholder="respondent UUID"
            aria-label="Respondent ID"
          />
        </label>{' '}
        <label>
          Reason (optional){' '}
          <input
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="ticket reference"
            aria-label="Reason"
          />
        </label>{' '}
        <button type="button" onClick={handleErase} disabled={busy || !respondentId.trim()}>
          {busy ? 'Erasing…' : 'Erase respondent'}
        </button>
        {lastResult ? (
          <p role="status">
            {lastResult.already_withdrawn
              ? 'Already withdrawn — no further data to erase.'
              : `Erased: ${lastResult.raw_rows_tombstoned} raw row(s) tombstoned, ` +
                `${lastResult.responses_purged} response(s) purged, ` +
                `${lastResult.pii_rows_deleted} PII row(s) deleted.`}
          </p>
        ) : null}
      </div>

      {error ? <div role="alert">Error: {error}</div> : null}

      <h2>Erasure audit</h2>
      {audit === null ? (
        <div role="status">Loading…</div>
      ) : audit.length === 0 ? (
        <p>No withdrawals recorded.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Respondent</th>
              <th>Requested</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {audit.map((w) => (
              <tr key={w.id}>
                <td>{w.respondent_id}</td>
                <td>{new Date(w.requested_at).toLocaleString()}</td>
                <td>{w.reason ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
