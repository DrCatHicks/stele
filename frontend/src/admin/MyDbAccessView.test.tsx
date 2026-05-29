import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { MyCredential } from '../api';
import { MyDbAccessView } from './MyDbAccessView';

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  listMyCredentials: vi.fn(),
  revealMyCredential: vi.fn(),
  regenerateMyCredential: vi.fn(),
}));

const { listMyCredentials, revealMyCredential, regenerateMyCredential } = await import('../api');
const mockedList = vi.mocked(listMyCredentials);
const mockedReveal = vi.mocked(revealMyCredential);
const mockedRegenerate = vi.mocked(regenerateMyCredential);

function credential(overrides: Partial<MyCredential> = {}): MyCredential {
  return {
    login_role: 'stele_analyst_me_a1b2',
    access: 'analyst',
    status: 'active',
    created_at: '2026-01-02T00:00:00Z',
    has_pending_secret: true,
    ...overrides,
  };
}

beforeEach(() => {
  mockedList.mockResolvedValue([]);
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderView() {
  return render(
    <MemoryRouter>
      <MyDbAccessView />
    </MemoryRouter>,
  );
}

describe('MyDbAccessView', () => {
  it('shows an empty state when the user has no credentials', async () => {
    renderView();
    expect(await screen.findByText(/You have no database credentials/)).toBeInTheDocument();
  });

  it('reveals a pending password once and shows the SET ROLE hint', async () => {
    mockedList.mockResolvedValue([credential()]);
    mockedReveal.mockResolvedValue({
      login_role: 'stele_analyst_me_a1b2',
      access: 'analyst',
      group_role: 'stele_analyst',
      password: 'super-secret-pw',
      set_role_sql: 'SET ROLE stele_analyst;',
    });
    renderView();
    await screen.findByText('stele_analyst_me_a1b2');

    await userEvent.click(screen.getByRole('button', { name: 'Reveal password' }));

    await waitFor(() => expect(mockedReveal).toHaveBeenCalledWith('stele_analyst_me_a1b2'));
    expect(await screen.findByText('super-secret-pw')).toBeInTheDocument();
    expect(screen.getByText('SET ROLE stele_analyst;')).toBeInTheDocument();
  });

  it('does not offer reveal when nothing is pending', async () => {
    mockedList.mockResolvedValue([credential({ has_pending_secret: false })]);
    renderView();
    await screen.findByText('stele_analyst_me_a1b2');
    expect(screen.queryByRole('button', { name: 'Reveal password' })).not.toBeInTheDocument();
    // Regenerate is still available for an active credential.
    expect(screen.getByRole('button', { name: 'Regenerate' })).toBeInTheDocument();
  });

  it('regenerates, then polls until the new password is ready to reveal', async () => {
    // Initial mount: nothing pending. After regenerate, the worker has produced the
    // new secret, so the poll's next read shows it pending.
    mockedList.mockResolvedValueOnce([credential({ has_pending_secret: false })]);
    mockedList.mockResolvedValue([credential({ has_pending_secret: true })]);
    mockedRegenerate.mockResolvedValue({
      id: 9,
      action: 'rotate',
      access: 'analyst',
      subject_label: null,
      login_role: 'stele_analyst_me_a1b2',
      status: 'pending',
      error_detail: null,
      created_at: 't',
      processed_at: null,
    });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderView();
    await screen.findByText('stele_analyst_me_a1b2');

    await userEvent.click(screen.getByRole('button', { name: 'Regenerate' }));

    await waitFor(() => expect(mockedRegenerate).toHaveBeenCalledWith('stele_analyst_me_a1b2'));
    // The poll surfaces the rotated password and the Reveal button appears.
    expect(await screen.findByText(/ready — click Reveal/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reveal password' })).toBeInTheDocument();
  });
});
