import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { DbCredential, ProvisionRequest } from '../api';
import { DbCredentialsView } from './DbCredentialsView';

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  listDbCredentials: vi.fn(),
  listProvisionRequests: vi.fn(),
  grantDbAccess: vi.fn(),
  revokeDbCredential: vi.fn(),
}));

vi.mock('../auth/AuthContext', () => ({
  useAuth: vi.fn(),
}));

const { listDbCredentials, listProvisionRequests, grantDbAccess, revokeDbCredential } =
  await import('../api');
const { useAuth } = await import('../auth/AuthContext');
const mockedList = vi.mocked(listDbCredentials);
const mockedRequests = vi.mocked(listProvisionRequests);
const mockedGrant = vi.mocked(grantDbAccess);
const mockedRevoke = vi.mocked(revokeDbCredential);
const mockedAuth = vi.mocked(useAuth);

function asRole(role: string): void {
  mockedAuth.mockReturnValue({
    user: { id: 1, email: 'me@example.com', roles: [role], disabled: false, created_at: 't' },
    status: 'ready',
    login: vi.fn(),
    logout: vi.fn(),
  } as unknown as ReturnType<typeof useAuth>);
}

function grant(overrides: Partial<DbCredential> = {}): DbCredential {
  return {
    id: 1,
    subject_label: 'jane.analyst',
    access: 'analyst',
    login_role: 'stele_analyst_jane',
    status: 'active',
    provisioned_by: 1,
    created_at: '2026-01-02T00:00:00Z',
    revoked_at: null,
    rotated_at: null,
    ...overrides,
  };
}

function pendingRequest(): ProvisionRequest {
  return {
    id: 7,
    action: 'provision',
    access: 'analyst',
    subject_label: 'jane.analyst',
    login_role: null,
    status: 'pending',
    error_detail: null,
    created_at: '2026-01-02T00:00:00Z',
    processed_at: null,
  };
}

beforeEach(() => {
  asRole('admin');
  mockedList.mockResolvedValue([]);
  mockedRequests.mockResolvedValue([]);
  mockedGrant.mockResolvedValue(pendingRequest());
  mockedRevoke.mockResolvedValue({ ...pendingRequest(), action: 'revoke' });
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderView() {
  return render(
    <MemoryRouter>
      <DbCredentialsView />
    </MemoryRouter>,
  );
}

describe('DbCredentialsView', () => {
  it('lists the credential registry', async () => {
    mockedList.mockResolvedValue([
      grant(),
      grant({
        id: 2,
        subject_label: 'sam.reviewer',
        access: 'reviewer',
        login_role: 'stele_reviewer_sam',
        status: 'revoked',
        revoked_at: '2026-03-01T00:00:00Z',
      }),
    ]);
    renderView();
    expect(await screen.findByText('jane.analyst')).toBeInTheDocument();
    expect(screen.getByText('stele_analyst_jane')).toBeInTheDocument();
    expect(screen.getByText('sam.reviewer')).toBeInTheDocument();
  });

  it('shows an empty state when no credentials are provisioned', async () => {
    renderView();
    expect(await screen.findByText('No DB credentials provisioned.')).toBeInTheDocument();
  });

  it('refuses non-admin roles', async () => {
    asRole('reviewer');
    renderView();
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('Only admins can view DB credentials'),
    );
  });

  it('grants analyst access by enqueuing a provision request', async () => {
    renderView();
    await screen.findByText('No DB credentials provisioned.');

    await userEvent.type(screen.getByLabelText('Email'), 'new@example.com');
    await userEvent.click(screen.getByRole('button', { name: 'Grant access' }));

    await waitFor(() => expect(mockedGrant).toHaveBeenCalledOnce());
    expect(mockedGrant).toHaveBeenCalledWith('new@example.com', 'analyst', {
      initialPassword: undefined,
      confirmPassword: undefined,
    });
    expect(
      await screen.findByText(/Queued analyst access for new@example.com/),
    ).toBeInTheDocument();
  });

  it('requires confirming the admin password for the reviewer (PII) tier', async () => {
    renderView();
    await screen.findByText('No DB credentials provisioned.');

    await userEvent.selectOptions(screen.getByLabelText('Access tier'), 'reviewer');
    // The PII caution and the confirm-password field appear for the reviewer tier.
    expect(screen.getByText(/direct read access to identifying data/i)).toBeInTheDocument();
    expect(screen.getByLabelText('Confirm your password')).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText('Email'), 'rev@example.com');
    await userEvent.type(screen.getByLabelText('Confirm your password'), 'admin-pw');
    await userEvent.click(screen.getByRole('button', { name: 'Grant access' }));

    await waitFor(() => expect(mockedGrant).toHaveBeenCalledOnce());
    expect(mockedGrant).toHaveBeenCalledWith('rev@example.com', 'reviewer', {
      initialPassword: undefined,
      confirmPassword: 'admin-pw',
    });
  });

  it('revokes an active credential after confirmation', async () => {
    mockedList.mockResolvedValue([grant()]);
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderView();
    await screen.findByText('jane.analyst');

    await userEvent.click(screen.getByRole('button', { name: 'Revoke' }));

    await waitFor(() => expect(mockedRevoke).toHaveBeenCalledWith('stele_analyst_jane'));
  });
});
