import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from './api';
import { ShortCodeEntry } from './ShortCodeEntry';

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  resolveShortCode: vi.fn(),
}));

// Stub the runner so the test stays off SurveyJS — we only assert the handoff.
vi.mock('./SurveyRunner', () => ({
  SurveyRunner: ({ surveyId, version }: { surveyId: string; version: number }) => (
    <div>
      runner {surveyId} v{version}
    </div>
  ),
}));

const { resolveShortCode } = await import('./api');
const mockedResolve = vi.mocked(resolveShortCode);

afterEach(() => {
  vi.clearAllMocks();
});

function renderAt(code: string) {
  return render(
    <MemoryRouter initialEntries={[`/s/${code}`]}>
      <Routes>
        <Route path="/s/:code" element={<ShortCodeEntry />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ShortCodeEntry', () => {
  it('resolves the code and hands off to the runner with the published version', async () => {
    mockedResolve.mockResolvedValue({ survey_id: 'sid-123', version: 4 });
    renderAt('climate-2026');

    expect(await screen.findByText('runner sid-123 v4')).toBeInTheDocument();
    expect(mockedResolve).toHaveBeenCalledWith('climate-2026');
  });

  it('shows the loading state while resolving', () => {
    mockedResolve.mockReturnValue(new Promise(() => {}));
    renderAt('pending');
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('shows a friendly not-available card on a 404', async () => {
    mockedResolve.mockRejectedValue(new ApiError(404, 'no published survey for this link'));
    renderAt('unknown');
    expect(await screen.findByText('Survey not available')).toBeInTheDocument();
  });

  it('shows a retryable error (not "not available") on a non-404 failure', async () => {
    // A server/network fault is an operational problem, not a bad link — it must
    // not be mislabelled as "not available", and the respondent can retry.
    mockedResolve.mockRejectedValueOnce(new ApiError(500, 'boom'));
    mockedResolve.mockResolvedValueOnce({ survey_id: 'sid-9', version: 1 });
    renderAt('flaky');

    expect(await screen.findByText('Something went wrong')).toBeInTheDocument();
    expect(screen.queryByText('Survey not available')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('runner sid-9 v1')).toBeInTheDocument();
  });

  it('treats a thrown non-ApiError (network failure) as retryable', async () => {
    mockedResolve.mockRejectedValue(new TypeError('Failed to fetch'));
    renderAt('offline');
    expect(await screen.findByText('Something went wrong')).toBeInTheDocument();
  });
});
