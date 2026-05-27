import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';

import { resolveShortCode, type ShortCodeResolved } from './api';
import { RespondentLayout } from './RespondentLayout';
import { SurveyRunner } from './SurveyRunner';
import { Card, CardBody, LoadingState } from './ui';

/**
 * Public short-link entry. Resolves /s/:code to a survey + its latest published
 * version, then hands off to the runner. An unknown code or a survey with nothing
 * published both come back as 404 — shown as one friendly "not available" card
 * rather than a raw error.
 */
export function ShortCodeEntry() {
  const { code = '' } = useParams<{ code: string }>();
  const [resolved, setResolved] = useState<ShortCodeResolved | null>(null);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    let active = true;
    setResolved(null);
    setNotFound(false);
    resolveShortCode(code)
      .then((r) => {
        if (active) setResolved(r);
      })
      .catch(() => {
        // 404 (and any other failure) → the link isn't open. We don't surface the
        // distinction between "unknown code" and "nothing published" — the backend
        // deliberately collapses them.
        if (active) setNotFound(true);
      });
    return () => {
      active = false;
    };
  }, [code]);

  if (notFound) {
    return (
      <RespondentLayout>
        <Card>
          <CardBody>
            <h1 className="text-lg font-semibold text-ink">Survey not available</h1>
            <p className="mt-2 text-sm text-muted">
              This link isn’t active. It may have been mistyped, or the survey isn’t open yet. Check
              the link you were given.
            </p>
          </CardBody>
        </Card>
      </RespondentLayout>
    );
  }

  if (!resolved) {
    return (
      <RespondentLayout>
        <LoadingState />
      </RespondentLayout>
    );
  }

  return <SurveyRunner surveyId={resolved.survey_id} version={resolved.version} />;
}
