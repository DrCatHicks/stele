import { Navigate, Route, Routes } from 'react-router-dom';

import { RespondentEntry } from './RespondentEntry';
import { AdminLayout } from './admin/AdminLayout';
import { GdprView } from './admin/GdprView';
import { LoginView } from './admin/LoginView';
import { PiiReviewView } from './admin/PiiReviewView';
import { SurveyEditorView } from './admin/SurveyEditorView';
import { SurveyListView } from './admin/SurveyListView';
import { AuthProvider, useAuth } from './auth/AuthContext';
import { RequireAuth } from './auth/RequireAuth';

/**
 * Role-aware landing for "/admin". Reviewers don't author surveys (the list
 * endpoint is author-gated → 403), so send them to their PII queue; everyone
 * else lands on the survey list.
 */
function AdminIndex() {
  const { user } = useAuth();
  if (user?.role === 'reviewer') return <Navigate to="/admin/pii-review" replace />;
  return <SurveyListView />;
}

/**
 * The operator area, mounted under "/admin/*". AuthProvider lives here — not at
 * the app root — so the public respondent path never triggers a session probe.
 * The login screen is open; everything else sits behind RequireAuth.
 */
function AdminApp() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="login" element={<LoginView />} />
        <Route element={<RequireAuth />}>
          <Route element={<AdminLayout />}>
            <Route index element={<AdminIndex />} />
            <Route path="surveys/:surveyId/versions/:version" element={<SurveyEditorView />} />
            <Route path="gdpr" element={<GdprView />} />
            <Route path="pii-review" element={<PiiReviewView />} />
          </Route>
        </Route>
      </Routes>
    </AuthProvider>
  );
}

/**
 * Top-level routes. "/" is the public respondent runner (no auth machinery);
 * "/admin/*" is the operator area.
 */
export function App() {
  return (
    <Routes>
      <Route path="/" element={<RespondentEntry />} />
      <Route path="/admin/*" element={<AdminApp />} />
    </Routes>
  );
}
