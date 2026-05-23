import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api';
import { AuthProvider } from '../auth/AuthContext';
import { LoginView } from './LoginView';

// Factory is hoisted above the imports, so it can't reference module-level
// bindings (e.g. ApiError). The initial-probe rejection value is irrelevant —
// AuthProvider treats any failure as "logged out".
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  fetchCurrentUser: vi.fn().mockRejectedValue(new Error('no session')),
  login: vi.fn(),
}));

const { login } = await import('../api');
const mockedLogin = vi.mocked(login);

afterEach(() => {
  vi.clearAllMocks();
});

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={['/admin/login']}>
      <AuthProvider>
        <Routes>
          <Route path="/admin/login" element={<LoginView />} />
          <Route path="/admin" element={<div>Dashboard</div>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe('LoginView', () => {
  it('submits credentials and navigates to the admin area on success', async () => {
    mockedLogin.mockResolvedValue({
      id: 1,
      email: 'admin@example.com',
      role: 'admin',
      disabled: false,
      created_at: 't',
    });
    renderLogin();

    await userEvent.type(screen.getByLabelText('Email'), 'admin@example.com');
    await userEvent.type(screen.getByLabelText('Password'), 'secret');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    expect(mockedLogin).toHaveBeenCalledWith('admin@example.com', 'secret');
    expect(await screen.findByText('Dashboard')).toBeInTheDocument();
  });

  it('shows a generic message on invalid credentials (401)', async () => {
    mockedLogin.mockRejectedValue(new ApiError(401, 'invalid email or password'));
    renderLogin();

    await userEvent.type(screen.getByLabelText('Email'), 'admin@example.com');
    await userEvent.type(screen.getByLabelText('Password'), 'wrong');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Invalid email or password.');
    });
    expect(screen.queryByText('Dashboard')).not.toBeInTheDocument();
  });
});
