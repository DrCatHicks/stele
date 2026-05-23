import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { GdprView } from './GdprView';

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  listWithdrawals: vi.fn(),
  triggerWithdrawal: vi.fn(),
}));

vi.mock('../auth/AuthContext', () => ({
  useAuth: vi.fn(),
}));

const { listWithdrawals, triggerWithdrawal } = await import('../api');
const { useAuth } = await import('../auth/AuthContext');
const mockedList = vi.mocked(listWithdrawals);
const mockedTrigger = vi.mocked(triggerWithdrawal);
const mockedAuth = vi.mocked(useAuth);

function asRole(role: string): void {
  mockedAuth.mockReturnValue({
    user: { id: 1, email: 'op@example.com', role, disabled: false, created_at: 't' },
    status: 'ready',
    login: vi.fn(),
    logout: vi.fn(),
  } as unknown as ReturnType<typeof useAuth>);
}

beforeEach(() => {
  asRole('admin');
  mockedList.mockResolvedValue([]);
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderView() {
  return render(
    <MemoryRouter>
      <GdprView />
    </MemoryRouter>,
  );
}

describe('GdprView', () => {
  it('lists the erasure audit', async () => {
    mockedList.mockResolvedValue([
      { id: 2, respondent_id: 'r-2', requested_at: '2026-01-02T00:00:00Z', reason: 'ticket-2' },
    ]);
    renderView();
    expect(await screen.findByText('r-2')).toBeInTheDocument();
    expect(screen.getByText('ticket-2')).toBeInTheDocument();
  });

  it('confirms before triggering an irreversible erasure and shows the result', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    mockedTrigger.mockResolvedValue({
      respondent_id: 'r-9',
      requested_at: 't',
      already_withdrawn: false,
      raw_rows_tombstoned: 3,
      responses_purged: 3,
      pii_rows_deleted: 1,
    });
    renderView();
    await screen.findByText('No withdrawals recorded.');

    await userEvent.type(screen.getByLabelText('Respondent ID'), 'r-9');
    await userEvent.click(screen.getByRole('button', { name: 'Erase respondent' }));

    expect(confirmSpy).toHaveBeenCalledOnce();
    expect(mockedTrigger).toHaveBeenCalledWith('r-9', undefined);
    expect(await screen.findByRole('status')).toHaveTextContent('3 raw row(s) tombstoned');
    confirmSpy.mockRestore();
  });

  it('does not erase when the confirm is dismissed', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderView();
    await screen.findByText('No withdrawals recorded.');

    await userEvent.type(screen.getByLabelText('Respondent ID'), 'r-1');
    await userEvent.click(screen.getByRole('button', { name: 'Erase respondent' }));

    expect(mockedTrigger).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('refuses non-admin roles', async () => {
    asRole('reviewer');
    renderView();
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Only admins'));
    expect(mockedTrigger).not.toHaveBeenCalled();
  });
});
