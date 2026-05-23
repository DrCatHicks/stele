import { Link, Outlet, useNavigate } from 'react-router-dom';

import { useAuth } from '../auth/AuthContext';

/** Shell for the authenticated admin area: a header with the current user and a
 * logout control, plus the routed view below. */
export function AdminLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = (): void => {
    void logout().then(() => navigate('/admin/login', { replace: true }));
  };

  return (
    <div>
      <header
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '0.5rem 1rem',
          borderBottom: '1px solid #ddd',
        }}
      >
        <Link to="/admin">Surveys</Link>
        <span>
          {user ? (
            <>
              <span data-testid="current-user">
                {user.email} ({user.role})
              </span>{' '}
              <button type="button" onClick={handleLogout}>
                Log out
              </button>
            </>
          ) : null}
        </span>
      </header>
      <main style={{ padding: '1rem' }}>
        <Outlet />
      </main>
    </div>
  );
}
