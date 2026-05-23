import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { AuthProvider } from '../auth/AuthContext';
import { AdminLayout } from './AdminLayout';

// Factory is hoisted above imports, so it can't reference module-level consts —
// the user literal is inlined here.
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  fetchCurrentUser: vi.fn().mockResolvedValue({
    id: 1,
    email: 'admin@example.com',
    role: 'admin',
    disabled: false,
    created_at: 't',
  }),
  logout: vi.fn().mockResolvedValue(undefined),
}));

const { logout } = await import('../api');
const mockedLogout = vi.mocked(logout);

afterEach(() => {
  vi.clearAllMocks();
});

function renderLayout() {
  return render(
    <MemoryRouter initialEntries={['/admin']}>
      <AuthProvider>
        <Routes>
          <Route path="/admin" element={<AdminLayout />}>
            <Route index element={<div>Survey list</div>} />
          </Route>
          <Route path="/admin/login" element={<div>Login screen</div>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe('AdminLayout', () => {
  it('shows the current user and the routed child', async () => {
    renderLayout();
    expect(await screen.findByTestId('current-user')).toHaveTextContent(
      'admin@example.com (admin)',
    );
    expect(screen.getByText('Survey list')).toBeInTheDocument();
  });

  it('logs out and navigates to the login screen', async () => {
    renderLayout();
    await screen.findByTestId('current-user');

    await userEvent.click(screen.getByRole('button', { name: 'Log out' }));

    expect(mockedLogout).toHaveBeenCalledOnce();
    expect(await screen.findByText('Login screen')).toBeInTheDocument();
  });
});
