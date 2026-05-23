import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { createSurvey, listSurveys, type SurveySummary } from '../api';

// A minimal valid starter so a freshly-created draft renders in the editor and
// preview; the author replaces it. One empty page keeps publish-validation honest
// (an author must add elements before publishing).
const STARTER_DEFINITION = { pages: [{ name: 'page1', elements: [] }] };

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function SurveyListView() {
  const navigate = useNavigate();
  const [surveys, setSurveys] = useState<SurveySummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    let active = true;
    listSurveys()
      .then((rows) => {
        if (active) setSurveys(rows);
      })
      .catch((err: unknown) => {
        if (active) setError(errorMessage(err));
      });
    return () => {
      active = false;
    };
  }, []);

  const handleCreate = (): void => {
    setCreating(true);
    setError(null);
    createSurvey(STARTER_DEFINITION)
      .then((created) =>
        navigate(`/admin/surveys/${created.survey_id}/versions/${created.version}`),
      )
      .catch((err: unknown) => {
        setError(errorMessage(err));
        setCreating(false);
      });
  };

  if (error) return <div role="alert">Error: {error}</div>;
  if (surveys === null) return <div role="status">Loading…</div>;

  return (
    <section>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h1>Surveys</h1>
        <button type="button" onClick={handleCreate} disabled={creating}>
          {creating ? 'Creating…' : 'New survey'}
        </button>
      </div>
      {surveys.length === 0 ? (
        <p>No surveys yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Survey</th>
              <th>Version</th>
              <th>Status</th>
              <th>Published</th>
            </tr>
          </thead>
          <tbody>
            {surveys.map((s) => (
              <tr key={`${s.survey_id}:${s.version}`}>
                <td>
                  <Link to={`/admin/surveys/${s.survey_id}/versions/${s.version}`}>
                    {s.survey_id}
                  </Link>
                </td>
                <td>{s.version}</td>
                <td>{s.status}</td>
                <td>{s.published_at ? new Date(s.published_at).toLocaleString() : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
