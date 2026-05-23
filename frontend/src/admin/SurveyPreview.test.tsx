import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SurveyPreview } from './SurveyPreview';

const DEFINITION = {
  pages: [
    {
      name: 'p1',
      elements: [{ type: 'radiogroup', name: 'q1', title: 'Pick one', choices: ['a', 'b'] }],
    },
  ],
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('SurveyPreview', () => {
  it('renders the survey with the live engine', async () => {
    render(<SurveyPreview definition={DEFINITION} />);
    expect(await screen.findByText('Pick one')).toBeInTheDocument();
  });

  it('never submits a response on completion (unlike the respondent runner)', async () => {
    // The load-bearing guarantee: previewing must not write a response. Spy on
    // fetch and assert completing the survey makes no network call.
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    render(<SurveyPreview definition={DEFINITION} />);

    await userEvent.click(await screen.findByText('a'));
    await userEvent.click(screen.getByText('Complete'));

    // SurveyJS shows its built-in completion page, and nothing is sent.
    await waitFor(() => {
      expect(screen.getByText(/thank you/i)).toBeInTheDocument();
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
