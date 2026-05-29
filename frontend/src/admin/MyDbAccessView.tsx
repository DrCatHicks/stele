import { useEffect, useState } from 'react';

import {
  listMyCredentials,
  type MyCredential,
  regenerateMyCredential,
  revealMyCredential,
  type RevealedSecret,
} from '../api';
import { formatDateTime } from '../format';
import { Alert, Badge, Button, Card, CardBody, EmptyState, LoadingState, PageHeader } from '../ui';

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

// Regenerate is async (a worker mints the new password out of band), so after
// enqueueing we poll for the fresh one-time secret to land rather than making the
// user reload. Bounded so the page doesn't spin forever if the worker is down.
const POLL_ATTEMPTS = 15;
const POLL_INTERVAL_MS = 2000;

/**
 * The signed-in recipient's own database credentials (design doc §3.10 revision).
 * An analyst or reviewer reveals their freshly-minted password here exactly once —
 * gated to their own session, never an unauthenticated link — and can regenerate
 * it if lost. The password is shown a single time and then wiped server-side, so
 * the page makes that explicit and copyable.
 */
export function MyDbAccessView() {
  const [creds, setCreds] = useState<MyCredential[] | null>(null);
  const [revealed, setRevealed] = useState<RevealedSecret | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = (): void => {
    listMyCredentials()
      .then((rows) => {
        setCreds(rows);
        setError(null);
      })
      .catch((err: unknown) => setError(errorMessage(err)));
  };

  useEffect(() => {
    let active = true;
    listMyCredentials()
      .then((rows) => {
        if (active) setCreds(rows);
      })
      .catch((err: unknown) => {
        if (active) setError(errorMessage(err));
      });
    return () => {
      active = false;
    };
  }, []);

  const handleReveal = (loginRole: string): void => {
    setBusy(true);
    setError(null);
    setNotice(null);
    setRevealed(null);
    revealMyCredential(loginRole)
      .then((secret) => {
        setRevealed(secret);
        refresh();
      })
      .catch((err: unknown) => setError(errorMessage(err)))
      .finally(() => setBusy(false));
  };

  // Poll for the rotated password to land, refreshing the table each tick so the
  // Reveal button reappears on its own (no full-page reload needed).
  const waitForPending = async (loginRole: string): Promise<void> => {
    for (let attempt = 0; attempt < POLL_ATTEMPTS; attempt++) {
      const rows = await listMyCredentials();
      setCreds(rows);
      if (rows.some((c) => c.login_role === loginRole && c.has_pending_secret)) {
        setNotice('Your new password is ready — click Reveal.');
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
    }
    setNotice('Still processing — reload the page in a moment to reveal your new password.');
  };

  const handleRegenerate = (loginRole: string): void => {
    if (!window.confirm(`Regenerate the password for ${loginRole}? The current one stops working.`))
      return;
    setBusy(true);
    setError(null);
    setRevealed(null);
    setNotice('Regenerating — waiting for your new password…');
    regenerateMyCredential(loginRole)
      .then(() => waitForPending(loginRole))
      .catch((err: unknown) => setError(errorMessage(err)))
      .finally(() => setBusy(false));
  };

  return (
    <section>
      <PageHeader
        title="My database access"
        subtitle="Your direct Postgres credentials. A new password can be revealed only once — copy it somewhere safe."
      />

      {error ? <Alert tone="error">Error: {error}</Alert> : null}
      {notice ? <Alert tone="success">{notice}</Alert> : null}

      {revealed ? (
        <Card className="mb-6">
          <CardBody className="flex flex-col gap-2">
            <h2 className="text-sm font-semibold text-ink">
              Password for {revealed.login_role} — shown once
            </h2>
            <p className="text-sm text-muted">
              Connect to Postgres as <code className="text-ink">{revealed.login_role}</code> with
              this password, then run <code className="text-ink">{revealed.set_role_sql}</code> to
              use your <code className="text-ink">{revealed.group_role}</code> access.
            </p>
            <pre className="overflow-x-auto rounded-md bg-canvas px-3 py-2 font-mono text-sm text-ink">
              {revealed.password}
            </pre>
            <p className="text-xs text-faint">
              This is the only time it&apos;s shown. If you lose it, use Regenerate below.
            </p>
          </CardBody>
        </Card>
      ) : null}

      {creds === null ? (
        error ? null : (
          <LoadingState />
        )
      ) : creds.length === 0 ? (
        <EmptyState>
          You have no database credentials. Ask an admin to grant you analyst or reviewer access.
        </EmptyState>
      ) : (
        <Card>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-faint">
                <th className="px-5 py-2 font-medium">Login role</th>
                <th className="px-5 py-2 font-medium">Access</th>
                <th className="px-5 py-2 font-medium">Status</th>
                <th className="px-5 py-2 font-medium">Granted</th>
                <th className="px-5 py-2 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {creds.map((c) => (
                <tr key={c.login_role} className="border-t border-border">
                  <td className="px-5 py-2 font-mono text-xs text-ink">{c.login_role}</td>
                  <td className="px-5 py-2 text-muted">{c.access}</td>
                  <td className="px-5 py-2">
                    <Badge tone={c.status === 'active' ? 'success' : 'neutral'}>{c.status}</Badge>
                  </td>
                  <td className="px-5 py-2 text-muted">{formatDateTime(c.created_at)}</td>
                  <td className="px-5 py-2">
                    <div className="flex flex-wrap gap-2">
                      {c.has_pending_secret ? (
                        <Button
                          type="button"
                          size="sm"
                          onClick={() => handleReveal(c.login_role)}
                          disabled={busy}
                        >
                          Reveal password
                        </Button>
                      ) : null}
                      {c.status === 'active' ? (
                        <Button
                          type="button"
                          size="sm"
                          variant="secondary"
                          onClick={() => handleRegenerate(c.login_role)}
                          disabled={busy}
                        >
                          Regenerate
                        </Button>
                      ) : null}
                    </div>
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
