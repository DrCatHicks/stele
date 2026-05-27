import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, type User } from '../api';
import { AuthProvider, useAuth } from './AuthContext';

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  fetchCurrentUser: vi.fn(),
  login: vi.fn(),
}));

const { fetchCurrentUser, login } = await import('../api');
const mockedFetchCurrentUser = vi.mocked(fetchCurrentUser);
const mockedLogin = vi.mocked(login);

const USER: User = {
  id: 1,
  email: 'admin@example.com',
  roles: ['admin'],
  disabled: false,
  created_at: 't',
};

afterEach(() => {
  vi.clearAllMocks();
});

function Consumer() {
  const { user, login: doLogin } = useAuth();
  return (
    <div>
      <span data-testid="user">{user?.email ?? 'none'}</span>
      <button type="button" onClick={() => void doLogin('admin@example.com', 'pw')}>
        login
      </button>
    </div>
  );
}

describe('AuthProvider', () => {
  it('a slow probe that 401s after an explicit login does not clobber the user', async () => {
    // The mount probe stays pending; the user logs in before it settles.
    let rejectProbe!: (reason: unknown) => void;
    mockedFetchCurrentUser.mockReturnValue(
      new Promise<User>((_, reject) => {
        rejectProbe = reject;
      }),
    );
    mockedLogin.mockResolvedValue(USER);

    render(
      <AuthProvider>
        <Consumer />
      </AuthProvider>,
    );

    await userEvent.click(screen.getByText('login'));
    await waitFor(() => expect(screen.getByTestId('user')).toHaveTextContent('admin@example.com'));

    // The stale probe finally fails with a 401 — it must NOT log the user out.
    await act(async () => {
      rejectProbe(new ApiError(401, 'not authenticated'));
    });

    expect(screen.getByTestId('user')).toHaveTextContent('admin@example.com');
  });
});
