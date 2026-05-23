import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import {
  ApiError,
  fetchCurrentUser,
  login as apiLogin,
  logout as apiLogout,
  setUnauthorizedHandler,
  type User,
} from '../api';

interface AuthState {
  user: User | null;
  // 'loading' until the initial /auth/me probe resolves; guards avoid flicker.
  status: 'loading' | 'ready';
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<'loading' | 'ready'>('loading');

  // Probe the cookie session once on mount. A 401 just means "not logged in";
  // any other failure also leaves us logged-out (fail closed for an admin area).
  useEffect(() => {
    let active = true;
    fetchCurrentUser()
      .then((u) => {
        if (active) setUser(u);
      })
      .catch(() => {
        if (active) setUser(null);
      })
      .finally(() => {
        if (active) setStatus('ready');
      });
    return () => {
      active = false;
    };
  }, []);

  // A 401 from any later call (expired/revoked session) clears the user, so the
  // route guard bounces to login on the next render.
  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    return () => setUnauthorizedHandler(null);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const u = await apiLogin(email, password);
    setUser(u);
  }, []);

  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } catch (err) {
      // Logout is idempotent server-side; a 401 just means the session was
      // already gone. Clear locally regardless.
      if (!(err instanceof ApiError)) throw err;
    }
    setUser(null);
  }, []);

  const value = useMemo<AuthState>(
    () => ({ user, status, login, logout }),
    [user, status, login, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (ctx === null) throw new Error('useAuth must be used within an AuthProvider');
  return ctx;
}
