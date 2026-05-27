import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import { App } from './App';
// SurveyJS ships its own theme CSS; defaultV2's primary (#19b394) is the same
// brand colour our Tailwind tokens mirror, so the survey runner and the operator
// chrome share one palette. This styles every rendered <Survey> (runner + preview).
import 'survey-core/defaultV2.min.css';
import './index.css';

const rootElement = document.getElementById('app');
if (rootElement) {
  createRoot(rootElement).render(
    <StrictMode>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </StrictMode>,
  );
}
