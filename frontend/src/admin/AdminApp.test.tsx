import { render, screen } from '@testing-library/react';
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { User } from '../api';
import AdminApp from './AdminApp';

const authState = vi.hoisted(() => ({
  user: null as User | null,
}));

vi.mock('../auth/AuthContext', () => ({
  AuthProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
  useAuth: () => ({ user: authState.user }),
}));

vi.mock('../auth/RequireAuth', () => ({
  RequireAuth: () => <Outlet />,
}));

vi.mock('./AdminLayout', () => ({
  AdminLayout: () => <Outlet />,
}));

vi.mock('./LoginView', () => ({
  LoginView: () => <div>Login view</div>,
}));

vi.mock('./SurveyListView', () => ({
  SurveyListView: () => <div>Survey list view</div>,
}));

vi.mock('./PiiReviewView', () => ({
  PiiReviewView: () => <div>PII review view</div>,
}));

vi.mock('./MyDbAccessView', () => ({
  MyDbAccessView: () => <div>My access view</div>,
}));

vi.mock('./SurveyEditorView', () => ({
  SurveyEditorView: () => <div>Survey editor view</div>,
}));

vi.mock('./GdprView', () => ({
  GdprView: () => <div>GDPR view</div>,
}));

vi.mock('./EtlView', () => ({
  EtlView: () => <div>ETL view</div>,
}));

vi.mock('./UsersView', () => ({
  UsersView: () => <div>Users view</div>,
}));

vi.mock('./DbCredentialsView', () => ({
  DbCredentialsView: () => <div>DB credentials view</div>,
}));

afterEach(() => {
  authState.user = null;
  vi.clearAllMocks();
});

function setRoles(roles: string[]) {
  authState.user = {
    id: 1,
    email: 'user@example.com',
    roles,
    disabled: false,
    created_at: 't',
  };
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/admin/*" element={<AdminApp />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('AdminApp', () => {
  it('renders the login route without requiring auth', async () => {
    renderAt('/admin/login');
    expect(await screen.findByText('Login view')).toBeInTheDocument();
  });

  it('lands authors on the survey list', async () => {
    setRoles(['researcher']);
    renderAt('/admin');
    expect(await screen.findByText('Survey list view')).toBeInTheDocument();
  });

  it('redirects reviewers to the pii review queue', async () => {
    setRoles(['reviewer']);
    renderAt('/admin');
    expect(await screen.findByText('PII review view')).toBeInTheDocument();
  });

  it('redirects analysts to my database access', async () => {
    setRoles(['analyst']);
    renderAt('/admin');
    expect(await screen.findByText('My access view')).toBeInTheDocument();
  });

  it('falls back to survey list for unknown role sets', async () => {
    setRoles(['viewer']);
    renderAt('/admin');
    expect(await screen.findByText('Survey list view')).toBeInTheDocument();
  });
});
