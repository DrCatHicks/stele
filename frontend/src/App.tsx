import { Route, Routes } from 'react-router-dom';

import { RespondentEntry } from './RespondentEntry';
import { AdminLayout } from './admin/AdminLayout';
import { LoginView } from './admin/LoginView';
import { SurveyEditorView } from './admin/SurveyEditorView';
import { SurveyListView } from './admin/SurveyListView';
import { AuthProvider } from './auth/AuthContext';
import { RequireAuth } from './auth/RequireAuth';

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
            <Route index element={<SurveyListView />} />
            <Route path="surveys/:surveyId/versions/:version" element={<SurveyEditorView />} />
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
