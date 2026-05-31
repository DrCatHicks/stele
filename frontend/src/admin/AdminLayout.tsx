import { useState } from 'react';
import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom';

import { useAuth } from '../auth/AuthContext';
import { Button } from '../ui';

/** Shell for the authenticated admin area: a branded header with role-aware
 * navigation, the current user, and a logout control, plus the routed view.
 *
 * Below `lg` (1024px) the full nav can't fit alongside the user/logout block
 * for an admin, so we collapse it behind a hamburger that opens a stacked
 * drawer underneath the header. Above `lg` the drawer never renders. */
export function AdminLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);

  const handleLogout = (): void => {
    void logout().then(() => navigate('/admin/login', { replace: true }));
  };

  const closeMenu = (): void => setMenuOpen(false);

  const navLinkClass = ({ isActive }: { isActive: boolean }): string =>
    [
      'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
      isActive ? 'bg-brand-light text-brand-dark' : 'text-muted hover:bg-canvas hover:text-ink',
    ].join(' ');

  // Same link set is rendered in the desktop top-bar nav and the mobile drawer;
  // the container controls direction/visibility, the links themselves don't care.
  const navLinks = user ? (
    <>
      {/* Authors see the survey workspace; admins the GDPR console; reviewers
          the PII screening queue. Role drives which links appear (design §3.10). */}
      {user.roles.includes('researcher') || user.roles.includes('admin') ? (
        <NavLink to="/admin" end className={navLinkClass} onClick={closeMenu}>
          Surveys
        </NavLink>
      ) : null}
      {user.roles.includes('admin') ? (
        <NavLink to="/admin/etl" className={navLinkClass} onClick={closeMenu}>
          ETL
        </NavLink>
      ) : null}
      {user.roles.includes('admin') ? (
        <NavLink to="/admin/gdpr" className={navLinkClass} onClick={closeMenu}>
          GDPR
        </NavLink>
      ) : null}
      {user.roles.includes('admin') ? (
        <NavLink to="/admin/users" className={navLinkClass} onClick={closeMenu}>
          Users
        </NavLink>
      ) : null}
      {user.roles.includes('admin') ? (
        <NavLink to="/admin/db-credentials" className={navLinkClass} onClick={closeMenu}>
          DB Credentials
        </NavLink>
      ) : null}
      {user.roles.includes('reviewer') ? (
        <NavLink to="/admin/pii-review" className={navLinkClass} onClick={closeMenu}>
          PII Review
        </NavLink>
      ) : null}
      {/* Anyone may hold a DB credential to reveal/regenerate (§3.10). */}
      <NavLink to="/admin/my-access" className={navLinkClass} onClick={closeMenu}>
        My DB access
      </NavLink>
    </>
  ) : null;

  return (
    <div className="min-h-screen">
      <header className="border-b border-border bg-surface">
        <div className="flex items-center justify-between gap-3 px-4 py-3 sm:px-6">
          <div className="flex items-center gap-6">
            <Link to="/admin" className="text-base font-semibold text-brand-dark">
              Stele
            </Link>
            <nav className="hidden items-center gap-1 lg:flex">{navLinks}</nav>
          </div>
          {user ? (
            <>
              <div className="hidden items-center gap-3 lg:flex">
                <span data-testid="current-user" className="text-sm text-muted">
                  {user.email} ({user.roles.join(', ')})
                </span>
                <Button type="button" variant="secondary" size="sm" onClick={handleLogout}>
                  Log out
                </Button>
              </div>
              <button
                type="button"
                aria-label={menuOpen ? 'Close menu' : 'Open menu'}
                aria-expanded={menuOpen}
                aria-controls="admin-mobile-menu"
                onClick={() => setMenuOpen((open) => !open)}
                className="-mr-1 rounded-md p-2 text-ink hover:bg-canvas lg:hidden"
              >
                <MenuIcon open={menuOpen} />
              </button>
            </>
          ) : null}
        </div>
        {user && menuOpen ? (
          <div
            id="admin-mobile-menu"
            className="border-t border-border bg-surface px-4 pb-3 pt-2 lg:hidden"
          >
            <nav className="flex flex-col gap-1">{navLinks}</nav>
            <div className="mt-3 flex items-center justify-between gap-3 border-t border-border pt-3">
              <span
                data-testid="current-user-mobile"
                className="truncate text-sm text-muted"
                title={`${user.email} (${user.roles.join(', ')})`}
              >
                {user.email} ({user.roles.join(', ')})
              </span>
              <Button type="button" variant="secondary" size="sm" onClick={handleLogout}>
                Log out
              </Button>
            </div>
          </div>
        ) : null}
      </header>
      <main className="mx-auto max-w-5xl px-4 py-6 sm:px-6 sm:py-8">
        <Outlet />
      </main>
    </div>
  );
}

function MenuIcon({ open }: { open: boolean }) {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {open ? (
        <>
          <line x1="18" y1="6" x2="6" y2="18" />
          <line x1="6" y1="6" x2="18" y2="18" />
        </>
      ) : (
        <>
          <line x1="3" y1="6" x2="21" y2="6" />
          <line x1="3" y1="12" x2="21" y2="12" />
          <line x1="3" y1="18" x2="21" y2="18" />
        </>
      )}
    </svg>
  );
}
