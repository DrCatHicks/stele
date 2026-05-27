import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';

import { ApiError, resolveShortCode, type ShortCodeResolved } from './api';
import { RespondentLayout } from './RespondentLayout';
import { SurveyRunner } from './SurveyRunner';
import { Button, Card, CardBody, LoadingState } from './ui';

/**
 * Public short-link entry. Resolves /s/:code to a survey + its latest published
 * version, then hands off to the runner.
 *
 * A 404 means the link isn't open (unknown code, or the survey has nothing
 * published — the backend deliberately collapses the two so codes can't be
 * probed). Any other failure (network, 5xx) is an operational fault, not a bad
 * link, so it gets a distinct, retryable error state rather than being
 * mislabelled "not available".
 */
export function ShortCodeEntry() {
  const { code = '' } = useParams<{ code: string }>();
  const [resolved, setResolved] = useState<ShortCodeResolved | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Bumped by the Retry button to re-run resolution after a transient failure.
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    setResolved(null);
    setNotFound(false);
    setLoadError(null);
    resolveShortCode(code)
      .then((r) => {
        if (active) setResolved(r);
      })
      .catch((err: unknown) => {
        if (!active) return;
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else {
          setLoadError(err instanceof Error ? err.message : String(err));
        }
      });
    return () => {
      active = false;
    };
  }, [code, attempt]);

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

  if (loadError !== null) {
    return (
      <RespondentLayout>
        <Card>
          <CardBody>
            <h1 className="text-lg font-semibold text-ink">Something went wrong</h1>
            <p className="mt-2 text-sm text-muted">
              We couldn’t load this survey. This is likely a temporary problem — please try again.
            </p>
            <div className="mt-4">
              <Button type="button" onClick={() => setAttempt((n) => n + 1)}>
                Try again
              </Button>
            </div>
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
