import { render, screen } from '@testing-library/react';
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
});
