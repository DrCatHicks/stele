import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { PiiReviewView } from './PiiReviewView';
import type { FreeTextReviewItem } from '../api';

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  listFreeTextForReview: vi.fn(),
  promoteFreeText: vi.fn(),
  rejectFreeText: vi.fn(),
}));

vi.mock('../auth/AuthContext', () => ({
  useAuth: vi.fn(),
}));

const { listFreeTextForReview, promoteFreeText, rejectFreeText } = await import('../api');
const { useAuth } = await import('../auth/AuthContext');
const mockedList = vi.mocked(listFreeTextForReview);
const mockedPromote = vi.mocked(promoteFreeText);
const mockedReject = vi.mocked(rejectFreeText);
const mockedAuth = vi.mocked(useAuth);

function asRole(role: string): void {
  mockedAuth.mockReturnValue({
    user: { id: 1, email: 'rev@example.com', role, disabled: false, created_at: 't' },
    status: 'ready',
    login: vi.fn(),
    logout: vi.fn(),
  } as unknown as ReturnType<typeof useAuth>);
}

const PENDING: FreeTextReviewItem = {
  id: 7,
  raw_response_id: 70,
  respondent_id: 'r-7',
  survey_id: 's-1',
  survey_version: 1,
  question_name: 'ft_high',
  value_text: 'I lead the platform team',
  created_at: '2026-01-01T00:00:00Z',
  status: null,
};

beforeEach(() => {
  asRole('reviewer');
  mockedList.mockResolvedValue([PENDING]);
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderView() {
  return render(
    <MemoryRouter>
      <PiiReviewView />
    </MemoryRouter>,
  );
}

describe('PiiReviewView', () => {
  it('shows pending high-risk answers with their text', async () => {
    renderView();
    expect(await screen.findByText('I lead the platform team')).toBeInTheDocument();
    expect(screen.getByText('ft_high')).toBeInTheDocument();
  });

  it('promotes a pending answer and reloads', async () => {
    mockedPromote.mockResolvedValue({
      free_text_id: 7,
      raw_response_id: 70,
      question_name: 'ft_high',
      status: 'promoted',
      reviewed_at: 't',
    });
    renderView();
    await screen.findByText('I lead the platform team');

    await userEvent.click(screen.getByRole('button', { name: 'Promote' }));

    expect(mockedPromote).toHaveBeenCalledWith(7);
    // Reloaded the current ('pending') queue after deciding.
    await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(2));
    expect(mockedList).toHaveBeenLastCalledWith('pending');
  });

  it('rejects a pending answer', async () => {
    mockedReject.mockResolvedValue({
      free_text_id: 7,
      raw_response_id: 70,
      question_name: 'ft_high',
      status: 'rejected',
      reviewed_at: 't',
    });
    renderView();
    await screen.findByText('I lead the platform team');

    await userEvent.click(screen.getByRole('button', { name: 'Reject' }));
    expect(mockedReject).toHaveBeenCalledWith(7);
  });

  it('switches queues by status tab', async () => {
    renderView();
    await screen.findByText('I lead the platform team');

    await userEvent.click(screen.getByRole('tab', { name: 'promoted' }));
    await waitFor(() => expect(mockedList).toHaveBeenLastCalledWith('promoted'));
  });

  it('refuses non-reviewer roles', async () => {
    asRole('admin');
    renderView();
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Only reviewers'));
    expect(mockedPromote).not.toHaveBeenCalled();
  });
});
