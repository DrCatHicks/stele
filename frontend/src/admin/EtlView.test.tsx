import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { EtlRun } from '../api';
import { EtlView } from './EtlView';

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  listEtlRuns: vi.fn(),
  triggerEtlRun: vi.fn(),
  getEtlRun: vi.fn(),
  clearEtlRun: vi.fn(),
}));

vi.mock('../auth/AuthContext', () => ({
  useAuth: vi.fn(),
}));

const { listEtlRuns, triggerEtlRun, getEtlRun, clearEtlRun } = await import('../api');
const { useAuth } = await import('../auth/AuthContext');
const mockedList = vi.mocked(listEtlRuns);
const mockedTrigger = vi.mocked(triggerEtlRun);
const mockedGet = vi.mocked(getEtlRun);
const mockedClear = vi.mocked(clearEtlRun);
const mockedAuth = vi.mocked(useAuth);

function asRole(role: string): void {
  mockedAuth.mockReturnValue({
    user: { id: 1, email: 'op@example.com', roles: [role], disabled: false, created_at: 't' },
    status: 'ready',
    login: vi.fn(),
    logout: vi.fn(),
  } as unknown as ReturnType<typeof useAuth>);
}

function run(overrides: Partial<EtlRun> = {}): EtlRun {
  return {
    run_id: 'r1',
    status: 'success',
    started_at: '2026-01-01T00:00:00Z',
    completed_at: '2026-01-01T00:01:00Z',
    source_row_counts: { 'app.raw_responses': 10 },
    mart_row_counts: { 'marts.fact_response': 5 },
    dbt_version: '1.7.0',
    git_sha: 'abcdef1234567890',
    interrupted: false,
    failures: [],
    ...overrides,
  };
}

beforeEach(() => {
  asRole('admin');
  mockedList.mockResolvedValue([]);
  mockedGet.mockResolvedValue(run());
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderView() {
  return render(
    <MemoryRouter>
      <EtlView />
    </MemoryRouter>,
  );
}

describe('EtlView', () => {
  it('lists recent runs with totals and surfaces failures', async () => {
    mockedList.mockResolvedValue([
      run({
        run_id: 'r-failed',
        status: 'failed',
        mart_row_counts: null,
        failures: [{ unique_id: 'test.shown_set', status: 'fail', message: 'Got 3, expected 0' }],
      }),
    ]);
    renderView();

    expect(await screen.findByText('failed')).toBeInTheDocument();
    expect(screen.getByText('test.shown_set')).toBeInTheDocument();
    expect(screen.getByText(/Got 3, expected 0/)).toBeInTheDocument();
    // Sources counted (10), marts unknown on a failed run.
    expect(screen.getByText('10 → —')).toBeInTheDocument();
  });

  it('shows an unknown source total when a source could not be read', async () => {
    // The backend records null for an unreadable source; summing it as 0 would
    // understate the total, so the whole total reads "—".
    mockedList.mockResolvedValue([
      run({ source_row_counts: { 'app.raw_responses': 10, 'pii.free_text': null } }),
    ]);
    renderView();

    expect(await screen.findByText('— → 5')).toBeInTheDocument();
  });

  it('confirms before triggering a rebuild and then shows the run in progress', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    mockedTrigger.mockResolvedValue(run({ run_id: 'r2', status: 'running', completed_at: null }));
    // First load is empty; after the trigger the refresh shows the running row.
    mockedList
      .mockResolvedValueOnce([])
      .mockResolvedValue([run({ run_id: 'r2', status: 'running', completed_at: null })]);
    mockedGet.mockResolvedValue(run({ run_id: 'r2', status: 'running', completed_at: null }));
    renderView();
    await screen.findByText('No ETL runs yet.');

    await userEvent.click(screen.getByRole('button', { name: 'Run ETL now' }));

    expect(confirmSpy).toHaveBeenCalledOnce();
    expect(mockedTrigger).toHaveBeenCalledOnce();
    expect(await screen.findByTestId('etl-running')).toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it('does not trigger when the confirm is dismissed', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderView();
    await screen.findByText('No ETL runs yet.');

    await userEvent.click(screen.getByRole('button', { name: 'Run ETL now' }));

    expect(mockedTrigger).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('disables the trigger when a run is already in progress on load', async () => {
    mockedList.mockResolvedValue([run({ status: 'running', completed_at: null })]);
    renderView();

    expect(await screen.findByTestId('etl-running')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run in progress…' })).toBeDisabled();
  });

  it('surfaces an interrupted run with a Clear action and does not wedge the trigger', async () => {
    const interrupted = run({
      run_id: 'r-orphan',
      status: 'running',
      completed_at: null,
      interrupted: true,
    });
    mockedList.mockResolvedValueOnce([interrupted]).mockResolvedValue([]);
    mockedClear.mockResolvedValue(
      run({ run_id: 'r-orphan', status: 'failed', interrupted: false }),
    );
    renderView();

    expect(await screen.findByText('interrupted')).toBeInTheDocument();
    // An interrupted run is not "active", so the trigger stays usable.
    expect(screen.getByRole('button', { name: 'Run ETL now' })).toBeEnabled();
    expect(screen.queryByTestId('etl-running')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Clear' }));
    expect(mockedClear).toHaveBeenCalledWith('r-orphan');
  });

  it('refuses non-admin roles', async () => {
    asRole('researcher');
    renderView();

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Only admins'));
    expect(mockedTrigger).not.toHaveBeenCalled();
  });
});
