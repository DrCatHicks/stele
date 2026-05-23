import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api';
import { AuthProvider } from './AuthContext';
import { RequireAuth } from './RequireAuth';

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  fetchCurrentUser: vi.fn(),
}));

const { fetchCurrentUser } = await import('../api');
const mockedFetchCurrentUser = vi.mocked(fetchCurrentUser);

afterEach(() => {
  vi.clearAllMocks();
});

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <Routes>
          <Route path="/admin/login" element={<div>Login screen</div>} />
          <Route element={<RequireAuth />}>
            <Route path="/admin" element={<div>Protected dashboard</div>} />
          </Route>
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe('RequireAuth', () => {
  it('redirects an unauthenticated visitor to the login screen', async () => {
    mockedFetchCurrentUser.mockRejectedValue(new ApiError(401, 'unauthenticated'));
    renderAt('/admin');
    expect(await screen.findByText('Login screen')).toBeInTheDocument();
    expect(screen.queryByText('Protected dashboard')).not.toBeInTheDocument();
  });

  it('renders the protected route once the session probe resolves to a user', async () => {
    mockedFetchCurrentUser.mockResolvedValue({
      id: 1,
      email: 'admin@example.com',
      role: 'admin',
      disabled: false,
      created_at: 't',
    });
    renderAt('/admin');
    expect(await screen.findByText('Protected dashboard')).toBeInTheDocument();
  });
});
