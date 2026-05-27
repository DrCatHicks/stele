import { Suspense, lazy } from 'react';
import { Route, Routes } from 'react-router-dom';

import { RespondentEntry } from './RespondentEntry';
import { ShortCodeEntry } from './ShortCodeEntry';
import { LoadingState } from './ui';

// The operator area and its heavy dependencies (CodeMirror, the admin views,
// auth machinery) are split into a lazy chunk so the public respondent path at
// "/" never downloads them.
const AdminApp = lazy(() => import('./admin/AdminApp'));

/**
 * Top-level routes. "/" is the public respondent runner (no auth machinery);
 * "/s/:code" is the public short-link entry; "/admin/*" is the lazily-loaded
 * operator area.
 */
export function App() {
  return (
    <Routes>
      <Route path="/" element={<RespondentEntry />} />
      <Route path="/s/:code" element={<ShortCodeEntry />} />
      <Route
        path="/admin/*"
        element={
          <Suspense fallback={<LoadingState />}>
            <AdminApp />
          </Suspense>
        }
      />
    </Routes>
  );
}
