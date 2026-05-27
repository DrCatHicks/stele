import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { createSurvey, listSurveys, type SurveySummary } from '../api';
import {
  Alert,
  Badge,
  Button,
  Card,
  EmptyState,
  LoadingState,
  PageHeader,
  statusTone,
} from '../ui';

// A minimal valid starter so a freshly-created draft renders in the editor and
// preview; the author replaces it. One empty page keeps publish-validation honest
// (an author must add elements before publishing).
const STARTER_DEFINITION = { pages: [{ name: 'page1', elements: [] }] };

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

// Group versions under their survey, preserving the server's newest-first order
// (both across surveys and across versions within a survey).
function groupBySurvey(rows: SurveySummary[]): SurveySummary[][] {
  const groups = new Map<string, SurveySummary[]>();
  for (const row of rows) {
    const existing = groups.get(row.survey_id);
    if (existing) existing.push(row);
    else groups.set(row.survey_id, [row]);
  }
  return [...groups.values()];
}

function SurveyCard({ versions }: { versions: SurveySummary[] }) {
  const survey = versions[0];
  if (!survey) return null;
  const totalResponses = versions.reduce((sum, v) => sum + v.response_count, 0);

  return (
    <Card>
      <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
        <code className="truncate text-sm font-semibold text-ink">{survey.survey_id}</code>
        <span className="shrink-0 text-xs text-muted">
          {versions.length} version{versions.length === 1 ? '' : 's'} · {totalResponses} response
          {totalResponses === 1 ? '' : 's'}
        </span>
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wide text-faint">
            <th className="px-5 py-2 font-medium">Version</th>
            <th className="px-5 py-2 font-medium">Status</th>
            <th className="px-5 py-2 font-medium">Responses</th>
            <th className="px-5 py-2 font-medium">Published</th>
            <th className="px-5 py-2" />
          </tr>
        </thead>
        <tbody>
          {versions.map((v) => (
            <tr key={v.version} className="border-t border-border">
              <td className="px-5 py-2 font-medium text-ink">v{v.version}</td>
              <td className="px-5 py-2">
                <Badge tone={statusTone(v.status)}>{v.status}</Badge>
              </td>
              <td className="px-5 py-2 text-muted">{v.response_count}</td>
              <td className="px-5 py-2 text-muted">
                {v.published_at ? new Date(v.published_at).toLocaleDateString() : '—'}
              </td>
              <td className="px-5 py-2 text-right">
                <Link
                  to={`/admin/surveys/${v.survey_id}/versions/${v.version}`}
                  aria-label={`Open ${v.survey_id} v${v.version}`}
                  className="font-medium text-brand-dark hover:underline"
                >
                  Open
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
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

  const newButton = (
    <Button type="button" onClick={handleCreate} disabled={creating}>
      {creating ? 'Creating…' : 'New survey'}
    </Button>
  );

  return (
    <section>
      <PageHeader
        title="Surveys"
        subtitle="Draft, publish, and track responses across versions."
        actions={newButton}
      />
      {error ? <Alert tone="error">Error: {error}</Alert> : null}
      {surveys === null ? (
        <LoadingState />
      ) : surveys.length === 0 ? (
        <EmptyState>No surveys yet.</EmptyState>
      ) : (
        <div className="flex flex-col gap-4">
          {groupBySurvey(surveys).map((versions) => (
            <SurveyCard key={versions[0]?.survey_id} versions={versions} />
          ))}
        </div>
      )}
    </section>
  );
}
