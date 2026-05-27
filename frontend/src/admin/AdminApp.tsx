import { Navigate, Route, Routes } from 'react-router-dom';

import { AuthProvider, useAuth } from '../auth/AuthContext';
import { RequireAuth } from '../auth/RequireAuth';
import { AdminLayout } from './AdminLayout';
import { GdprView } from './GdprView';
import { LoginView } from './LoginView';
import { PiiReviewView } from './PiiReviewView';
import { SurveyEditorView } from './SurveyEditorView';
import { SurveyListView } from './SurveyListView';

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
 *
 * This whole subtree (and its heavy deps — CodeMirror, the admin views) is a
 * lazy-loaded chunk (see App.tsx), so the public respondent path never downloads
 * it. Default export so React.lazy can import it.
 */
export default function AdminApp() {
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
