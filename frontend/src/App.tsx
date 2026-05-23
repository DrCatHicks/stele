import { Route, Routes } from 'react-router-dom';

import { RespondentEntry } from './RespondentEntry';
import { AdminLayout } from './admin/AdminLayout';
import { LoginView } from './admin/LoginView';
import { SurveyEditorView } from './admin/SurveyEditorView';
import { SurveyListView } from './admin/SurveyListView';
import { AuthProvider } from './auth/AuthContext';
import { RequireAuth } from './auth/RequireAuth';

/**
 * Top-level routes. "/" is the public respondent runner; everything under
 * "/admin" is the operator area — the login screen is open, the rest sits behind
 * RequireAuth. AuthProvider wraps it all so both areas share one session probe.
 */
export function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/" element={<RespondentEntry />} />
        <Route path="/admin/login" element={<LoginView />} />
        <Route element={<RequireAuth />}>
          <Route path="/admin" element={<AdminLayout />}>
            <Route index element={<SurveyListView />} />
            <Route path="surveys/:surveyId/versions/:version" element={<SurveyEditorView />} />
          </Route>
        </Route>
      </Routes>
    </AuthProvider>
  );
}
