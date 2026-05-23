import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';

import {
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
  // Set once the user takes an explicit auth action (login/logout). Guards the
  // mount probe from clobbering a newer state if a slow /auth/me resolves after
  // the user has already logged in/out.
  const probeSuperseded = useRef(false);

  // Probe the cookie session once on mount. A 401 here just means "not logged
  // in" (not an expired session), so it must NOT fire the global unauthorized
  // handler — that's only armed in .finally, once the probe has settled.
  useEffect(() => {
    let active = true;
    fetchCurrentUser()
      .then((u) => {
        if (active && !probeSuperseded.current) setUser(u);
      })
      .catch(() => {
        // fetchCurrentUser's 401 doesn't fire the handler yet (still null here).
        if (active && !probeSuperseded.current) setUser(null);
      })
      .finally(() => {
        if (!active) return;
        setStatus('ready');
        // Now arm it: a 401 from any *later* call (expired/revoked session)
        // clears the user so the route guard bounces to login next render.
        setUnauthorizedHandler(() => setUser(null));
      });
    return () => {
      active = false;
      setUnauthorizedHandler(null);
    };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const u = await apiLogin(email, password);
    probeSuperseded.current = true;
    setUser(u);
  }, []);

  const logout = useCallback(async () => {
    // Best-effort: logout is idempotent server-side, so clear client state
    // regardless of the outcome — an HTTP error (already-gone session) or a
    // network failure must not leave the user stuck "logged in" in the UI.
    probeSuperseded.current = true;
    try {
      await apiLogout();
    } catch {
      // swallowed deliberately
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
