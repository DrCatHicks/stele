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
    roles: ['admin'],
    disabled: false,
    created_at: 't',
  }),
  logout: vi.fn().mockResolvedValue(undefined),
}));

const { logout, fetchCurrentUser } = await import('../api');
const mockedLogout = vi.mocked(logout);
const mockedFetchUser = vi.mocked(fetchCurrentUser);

afterEach(() => {
  vi.clearAllMocks();
});

function asRole(role: string): void {
  mockedFetchUser.mockResolvedValue({
    id: 1,
    email: `${role}@example.com`,
    roles: [role],
    disabled: false,
    created_at: 't',
  });
}

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

  it('shows admins the Surveys + GDPR + Users + DB Credentials nav, not PII Review', async () => {
    asRole('admin');
    renderLayout();
    await screen.findByTestId('current-user');
    expect(screen.getByRole('link', { name: 'Surveys' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'GDPR' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Users' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'DB Credentials' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'PII Review' })).not.toBeInTheDocument();
  });

  it('shows reviewers only the PII Review nav', async () => {
    asRole('reviewer');
    renderLayout();
    await screen.findByTestId('current-user');
    expect(screen.getByRole('link', { name: 'PII Review' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Surveys' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'GDPR' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Users' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'DB Credentials' })).not.toBeInTheDocument();
  });

  it('shows researchers the Surveys nav only', async () => {
    asRole('researcher');
    renderLayout();
    await screen.findByTestId('current-user');
    expect(screen.getByRole('link', { name: 'Surveys' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'GDPR' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'PII Review' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Users' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'DB Credentials' })).not.toBeInTheDocument();
  });

  it('shows an analyst only the My DB access nav', async () => {
    asRole('analyst');
    renderLayout();
    await screen.findByTestId('current-user');
    expect(screen.getByRole('link', { name: 'My DB access' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Surveys' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'DB Credentials' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'PII Review' })).not.toBeInTheDocument();
  });

  // The desktop nav doesn't fit on small viewports alongside the user/logout
  // block, so we collapse it behind a hamburger button (issue #48). jsdom
  // doesn't apply Tailwind's responsive CSS, so we can't assert visibility, but
  // we can assert the collapsed-menu DOM contract: button toggles the drawer
  // and the drawer mirrors the nav + logout.
  it('exposes a hamburger that opens a mirror nav drawer', async () => {
    asRole('admin');
    renderLayout();
    await screen.findByTestId('current-user');

    const toggle = screen.getByRole('button', { name: 'Open menu' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByTestId('current-user-mobile')).not.toBeInTheDocument();

    await userEvent.click(toggle);

    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(toggle).toHaveAccessibleName('Close menu');
    // Drawer renders its own user block and a second copy of every nav link.
    expect(screen.getByTestId('current-user-mobile')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: 'Surveys' })).toHaveLength(2);
    expect(screen.getAllByRole('button', { name: 'Log out' })).toHaveLength(2);
  });

  it('closes the drawer when a nav link is clicked', async () => {
    asRole('admin');
    renderLayout();
    await screen.findByTestId('current-user');

    await userEvent.click(screen.getByRole('button', { name: 'Open menu' }));
    expect(screen.getByTestId('current-user-mobile')).toBeInTheDocument();

    // Click the drawer copy of the Surveys link. Both copies route to the same
    // place; either close path is fine, but tapping a link is the common one.
    const surveysLinks = screen.getAllByRole('link', { name: 'Surveys' });
    await userEvent.click(surveysLinks[surveysLinks.length - 1]!);

    expect(screen.queryByTestId('current-user-mobile')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open menu' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
  });
});
