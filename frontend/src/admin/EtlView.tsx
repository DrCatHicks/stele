import { useCallback, useEffect, useState } from 'react';

import { clearEtlRun, getEtlRun, listEtlRuns, triggerEtlRun, type EtlRun } from '../api';
import { useAuth } from '../auth/AuthContext';
import { formatDateTime } from '../format';
import {
  Alert,
  Badge,
  Button,
  Card,
  CardBody,
  EmptyState,
  LoadingState,
  PageHeader,
  statusTone,
} from '../ui';

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** Total across a per-table row-count map; null when the run didn't record it
 * (e.g. marts on a failed run) or when any member is null — the backend uses null
 * for a source it couldn't read, and summing that as 0 would understate the total,
 * so we show "unknown" rather than a misleading number. */
function total(counts: Record<string, number | null> | null): number | null {
  if (!counts) return null;
  const values = Object.values(counts);
  if (values.some((n) => n === null)) return null;
  return values.reduce<number>((sum, n) => sum + (n ?? 0), 0);
}

/** Human elapsed for a finished run; "—" while still running. */
function elapsed(run: EtlRun): string {
  if (!run.completed_at) return '—';
  const ms = new Date(run.completed_at).getTime() - new Date(run.started_at).getTime();
  const secs = Math.max(0, Math.round(ms / 1000));
  return secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m ${secs % 60}s`;
}

const POLL_MS = 2000;

/**
 * Admin ETL console: trigger a full-refresh `dbt build` and watch it run.
 *
 * The trigger is admin-only (the API gate returns 403 otherwise; this view also
 * hides itself). A run is a heavy rebuild, so the button confirms first and then
 * disables while a run is in flight. The triggered run resolves in the background
 * on the server, so we poll its row until it leaves `running`, then refresh the
 * history table. Only one run can be active at a time (the API returns 409), which
 * the polling + disabled button keep the UI consistent with.
 *
 * A run orphaned by a restart (status 'running' past the server's stale window)
 * comes back flagged `interrupted`: it doesn't count as active (so it never wedges
 * the trigger) and the row offers a "Clear" action that resolves it to failed.
 */
export function EtlView() {
  const { user } = useAuth();
  const [runs, setRuns] = useState<EtlRun[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [clearingId, setClearingId] = useState<string | null>(null);
  // The run we're actively polling (the one we just triggered, or one already
  // running when we arrived). Null when nothing is in flight.
  const [activeRunId, setActiveRunId] = useState<string | null>(null);

  const refresh = useCallback((): Promise<void> => {
    return listEtlRuns()
      .then((rows) => {
        setRuns(rows);
        setError(null);
        // Track a genuinely live run (the daily cron, or one started from another
        // tab), but not an interrupted one — that's resolved by Clear, not polling.
        const live = rows.find((r) => r.status === 'running' && !r.interrupted);
        setActiveRunId(live ? live.run_id : null);
      })
      .catch((err: unknown) => setError(errorMessage(err)));
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Poll the active run until it resolves, then refresh the table and stop.
  useEffect(() => {
    if (!activeRunId) return;
    let cancelled = false;
    const tick = (): void => {
      getEtlRun(activeRunId)
        .then((run) => {
          if (cancelled) return;
          if (run.status !== 'running') void refresh();
        })
        .catch((err: unknown) => {
          if (!cancelled) setError(errorMessage(err));
        });
    };
    const id = window.setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [activeRunId, refresh]);

  if (user && !user.roles.includes('admin')) {
    return <Alert tone="error">Only admins can run ETL.</Alert>;
  }

  const handleRun = (): void => {
    if (
      !window.confirm(
        'Run a full ETL rebuild now? This rebuilds every marts table from raw responses.',
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    triggerEtlRun()
      .then((run) => {
        setActiveRunId(run.run_id);
        return refresh();
      })
      .catch((err: unknown) => setError(errorMessage(err)))
      .finally(() => setBusy(false));
  };

  const handleClear = (runId: string): void => {
    setClearingId(runId);
    setError(null);
    clearEtlRun(runId)
      .then(() => refresh())
      .catch((err: unknown) => setError(errorMessage(err)))
      .finally(() => setClearingId(null));
  };

  const running = activeRunId !== null;

  return (
    <section>
      <PageHeader
        title="ETL"
        subtitle="Rebuild the analytics marts from raw responses and review past runs."
      />

      <Card className="mb-6">
        <CardBody className="flex flex-col gap-4">
          <h2 className="text-sm font-semibold text-ink">Run ETL</h2>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="button" onClick={handleRun} disabled={busy || running}>
              {busy ? 'Starting…' : running ? 'Run in progress…' : 'Run ETL now'}
            </Button>
            {running ? (
              <span data-testid="etl-running" className="text-sm text-muted">
                A run is in progress; status updates below.
              </span>
            ) : (
              <span className="text-sm text-muted">
                A full rebuild; usually a minute or two. Only one run at a time.
              </span>
            )}
          </div>
        </CardBody>
      </Card>

      {error ? (
        <div className="mb-6">
          <Alert tone="error">Error: {error}</Alert>
        </div>
      ) : null}

      <h2 className="mb-2 text-sm font-semibold text-ink">Recent runs</h2>
      {runs === null ? (
        error ? null : (
          <LoadingState />
        )
      ) : runs.length === 0 ? (
        <EmptyState>No ETL runs yet.</EmptyState>
      ) : (
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[44rem] text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-faint">
                  <th className="px-5 py-2 font-medium">Status</th>
                  <th className="px-5 py-2 font-medium">Started</th>
                  <th className="px-5 py-2 font-medium">Elapsed</th>
                  <th className="px-5 py-2 font-medium">Sources → marts</th>
                  <th className="px-5 py-2 font-medium">Version</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => {
                  const sources = total(run.source_row_counts);
                  const marts = total(run.mart_row_counts);
                  return (
                    <tr key={run.run_id} className="border-t border-border align-top">
                      <td className="px-5 py-2">
                        <Badge tone={run.interrupted ? 'danger' : statusTone(run.status)}>
                          {run.interrupted ? 'interrupted' : run.status}
                        </Badge>
                        {run.interrupted ? (
                          <div className="mt-1 flex items-center gap-2 text-xs text-muted">
                            <span>Likely stopped by a restart.</span>
                            <Button
                              type="button"
                              variant="secondary"
                              size="sm"
                              onClick={() => handleClear(run.run_id)}
                              disabled={clearingId === run.run_id}
                            >
                              {clearingId === run.run_id ? 'Clearing…' : 'Clear'}
                            </Button>
                          </div>
                        ) : null}
                        {run.failures.length > 0 ? (
                          <ul className="mt-1 list-none space-y-0.5 text-xs text-danger">
                            {run.failures.map((f, i) => (
                              <li key={f.unique_id ?? i}>
                                <span className="font-mono">{f.unique_id ?? '?'}</span>
                                {f.message ? `: ${f.message}` : null}
                              </li>
                            ))}
                          </ul>
                        ) : null}
                      </td>
                      <td className="px-5 py-2 text-muted">{formatDateTime(run.started_at)}</td>
                      <td className="px-5 py-2 text-muted">{elapsed(run)}</td>
                      <td className="px-5 py-2 text-muted">
                        {sources ?? '—'} → {marts ?? '—'}
                      </td>
                      <td className="px-5 py-2 font-mono text-xs text-muted">
                        {run.dbt_version ?? '—'}
                        {run.git_sha ? ` @ ${run.git_sha.slice(0, 7)}` : ''}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </section>
  );
}
