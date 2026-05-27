import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { App } from './App';

// AdminApp mounts AuthProvider, which probes /auth/me on mount; treat any failure
// as "logged out". The respondent path must not need this at all.
vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  fetchCurrentUser: vi.fn().mockRejectedValue(new Error('no session')),
}));

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe('App routing', () => {
  it('renders the public respondent path at "/" without the admin area', () => {
    renderAt('/');
    expect(screen.getByText('No survey selected')).toBeInTheDocument();
    expect(screen.queryByText('Sign in')).not.toBeInTheDocument();
  });

  it('lazy-loads the operator area at "/admin/*"', async () => {
    renderAt('/admin/login');
    // AdminApp is a lazy chunk; the login screen appears once it resolves.
    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeInTheDocument();
  });
});
