import { useEffect, useState } from 'react';

import {
  DB_ACCESS_TIERS,
  type DbAccessTier,
  type DbCredential,
  grantDbAccess,
  listDbCredentials,
  listProvisionRequests,
  type ProvisionRequest,
  revokeDbCredential,
} from '../api';
import { useAuth } from '../auth/AuthContext';
import { formatDateTime } from '../format';
import {
  Alert,
  Badge,
  Button,
  Card,
  CardBody,
  EmptyState,
  Field,
  INPUT_CLASSES,
  LoadingState,
  PageHeader,
} from '../ui';

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function requestTone(status: string): 'success' | 'danger' | 'warning' | 'neutral' {
  if (status === 'done') return 'success';
  if (status === 'failed') return 'danger';
  if (status === 'pending') return 'warning';
  return 'neutral';
}

/**
 * Admin DB-credential console (design doc §3.10 revision). Grants are *requested*
 * here, never minted: the form enqueues a provision request that a privileged
 * worker fulfils out of band (stele_api has no CREATEROLE). The recipient then
 * signs in and reveals their own password once (see MyDbAccessView).
 *
 * The reviewer (PII) tier is gated behind re-confirming the admin's own password,
 * since it mints a credential that can read identifying data. The view is
 * admin-only, server- and client-side.
 */
export function DbCredentialsView() {
  const { user } = useAuth();
  const [grants, setGrants] = useState<DbCredential[] | null>(null);
  const [requests, setRequests] = useState<ProvisionRequest[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Grant form.
  const [email, setEmail] = useState('');
  const [access, setAccess] = useState<DbAccessTier>('analyst');
  const [initialPassword, setInitialPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');

  const refresh = (): void => {
    listDbCredentials()
      .then((rows) => {
        setGrants(rows);
        setError(null);
      })
      .catch((err: unknown) => setError(errorMessage(err)));
    listProvisionRequests()
      .then(setRequests)
      .catch(() => {
        /* the registry is the primary surface; a request-list hiccup is non-fatal */
      });
  };

  useEffect(() => {
    let active = true;
    listDbCredentials()
      .then((rows) => {
        if (active) setGrants(rows);
      })
      .catch((err: unknown) => {
        if (active) setError(errorMessage(err));
      });
    listProvisionRequests()
      .then((rows) => {
        if (active) setRequests(rows);
      })
      .catch(() => {
        /* non-fatal */
      });
    return () => {
      active = false;
    };
  }, []);

  if (user && !user.roles.includes('admin')) {
    return <Alert tone="error">Only admins can view DB credentials.</Alert>;
  }

  const run = (action: () => Promise<unknown>, onSuccess: () => void): void => {
    setBusy(true);
    setError(null);
    setNotice(null);
    action()
      .then(() => {
        onSuccess();
        refresh();
      })
      .catch((err: unknown) => setError(errorMessage(err)))
      .finally(() => setBusy(false));
  };

  const handleGrant = (): void => {
    const addr = email.trim();
    if (!addr) return;
    run(
      () =>
        grantDbAccess(addr, access, {
          initialPassword: initialPassword || undefined,
          confirmPassword: confirmPassword || undefined,
        }),
      () => {
        setNotice(
          `Queued ${access} access for ${addr}. They sign in to reveal their password once.`,
        );
        setEmail('');
        setInitialPassword('');
        setConfirmPassword('');
      },
    );
  };

  const handleRevoke = (loginRole: string): void => {
    if (!window.confirm(`Revoke ${loginRole}? The login role is dropped; this can't be undone.`)) {
      return;
    }
    run(
      () => revokeDbCredential(loginRole),
      () => setNotice(`Queued revoke of ${loginRole}.`),
    );
  };

  return (
    <section>
      <PageHeader
        title="DB credentials"
        subtitle="Grant analysts and reviewers direct database access. Provisioning runs out of band; the recipient signs in to reveal their password once."
      />

      <Card className="mb-6">
        <CardBody className="flex flex-col gap-4">
          <h2 className="text-sm font-semibold text-ink">Grant DB access</h2>
          <div className="flex flex-wrap items-end gap-3">
            <Field
              label="Email"
              type="email"
              className="min-w-56 flex-1"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="person@example.com"
            />
            <div className="flex flex-col gap-1">
              <label htmlFor="grant-access" className="text-sm font-medium text-ink">
                Access tier
              </label>
              <select
                id="grant-access"
                className={INPUT_CLASSES}
                value={access}
                onChange={(e) => setAccess(e.target.value as DbAccessTier)}
              >
                {DB_ACCESS_TIERS.map((tier) => (
                  <option key={tier} value={tier}>
                    {tier}
                  </option>
                ))}
              </select>
            </div>
            <Field
              label="Initial password"
              type="password"
              className="min-w-56 flex-1"
              value={initialPassword}
              onChange={(e) => setInitialPassword(e.target.value)}
              placeholder="for a new account"
              hint="Required only if they don't have an account yet."
            />
          </div>
          {access === 'reviewer' ? (
            <div
              role="note"
              className="rounded-md border-l-4 border-warning-bg bg-warning-bg px-3 py-2 text-sm text-warning"
            >
              Reviewer access grants <strong>direct read access to identifying data (PII)</strong>.
              Confirm your own password to authorize it.
            </div>
          ) : null}
          {access === 'reviewer' ? (
            <Field
              label="Confirm your password"
              type="password"
              className="min-w-56 max-w-sm"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="your admin password"
            />
          ) : null}
          <div>
            <Button type="button" onClick={handleGrant} disabled={busy || !email.trim()}>
              {busy ? 'Working…' : 'Grant access'}
            </Button>
          </div>
        </CardBody>
      </Card>

      {error ? <Alert tone="error">Error: {error}</Alert> : null}
      {notice ? <Alert tone="success">{notice}</Alert> : null}

      {requests.length > 0 ? (
        <>
          <h2 className="mb-2 mt-6 text-sm font-semibold text-ink">Recent requests</h2>
          <Card className="mb-6">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[40rem] text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wide text-faint">
                    <th className="px-5 py-2 font-medium">Action</th>
                    <th className="px-5 py-2 font-medium">Subject / role</th>
                    <th className="px-5 py-2 font-medium">Status</th>
                    <th className="px-5 py-2 font-medium">Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {requests.map((r) => (
                    <tr key={r.id} className="border-t border-border">
                      <td className="px-5 py-2 text-ink">
                        {r.action}
                        {r.access ? ` (${r.access})` : ''}
                      </td>
                      <td className="px-5 py-2 font-mono text-xs text-muted">
                        {r.subject_label ?? r.login_role ?? '—'}
                      </td>
                      <td className="px-5 py-2">
                        <Badge tone={requestTone(r.status)}>{r.status}</Badge>
                      </td>
                      <td className="px-5 py-2 text-muted">{r.error_detail ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      ) : null}

      <h2 className="mb-2 mt-6 text-sm font-semibold text-ink">Credential registry</h2>
      {grants === null ? (
        error ? null : (
          <LoadingState />
        )
      ) : grants.length === 0 ? (
        <EmptyState>No DB credentials provisioned.</EmptyState>
      ) : (
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[56rem] text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-faint">
                  <th className="px-5 py-2 font-medium">Subject</th>
                  <th className="px-5 py-2 font-medium">Access</th>
                  <th className="px-5 py-2 font-medium">Login role</th>
                  <th className="px-5 py-2 font-medium">Status</th>
                  <th className="px-5 py-2 font-medium">Created</th>
                  <th className="px-5 py-2 font-medium">Revoked</th>
                  <th className="px-5 py-2 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {grants.map((g) => (
                  <tr key={g.id} className="border-t border-border">
                    <td className="px-5 py-2 text-ink">{g.subject_label}</td>
                    <td className="px-5 py-2 text-muted">{g.access}</td>
                    <td className="px-5 py-2 font-mono text-xs text-muted">{g.login_role}</td>
                    <td className="px-5 py-2">
                      <Badge tone={g.status === 'active' ? 'success' : 'neutral'}>{g.status}</Badge>
                    </td>
                    <td className="px-5 py-2 text-muted">{formatDateTime(g.created_at)}</td>
                    <td className="px-5 py-2 text-muted">
                      {g.revoked_at ? formatDateTime(g.revoked_at) : '—'}
                    </td>
                    <td className="px-5 py-2">
                      {g.status === 'active' ? (
                        <Button
                          type="button"
                          size="sm"
                          variant="danger"
                          onClick={() => handleRevoke(g.login_role)}
                          disabled={busy}
                        >
                          Revoke
                        </Button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </section>
  );
}
