import { Navigate, Outlet, useLocation } from 'react-router-dom';

import { useAuth } from './AuthContext';

/**
 * Route guard for the admin area. While the initial session probe is in flight
 * we show a neutral loading indicator rather than redirecting — this avoids a
 * login-screen flash for already-authenticated users. Once resolved, an
 * unauthenticated visitor is redirected to the login screen, preserving where
 * they were headed so login can send them back.
 */
export function RequireAuth() {
  const { user, status } = useAuth();
  const location = useLocation();

  if (status === 'loading') return <div role="status">Loading…</div>;
  if (user === null) {
    return <Navigate to="/admin/login" replace state={{ from: location }} />;
  }
  return <Outlet />;
}
