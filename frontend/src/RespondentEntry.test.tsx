import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { RespondentEntry } from './RespondentEntry';

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <RespondentEntry />
    </MemoryRouter>,
  );
}

describe('RespondentEntry', () => {
  it('prompts for a survey link when no survey id is present', () => {
    renderAt('/');
    expect(screen.getByText('No survey selected')).toBeInTheDocument();
  });

  it('mounts the runner (loading state) when a survey id is present', () => {
    // Keep the fetch pending so the runner stays on its loader rather than
    // resolving to a survey or an error screen.
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {})));
    renderAt('/?survey=abc&version=1');
    expect(screen.queryByText('No survey selected')).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Loading survey…');
  });
});
