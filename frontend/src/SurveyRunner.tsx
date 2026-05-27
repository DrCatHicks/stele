import { useEffect, useMemo, useState } from 'react';
import { Model } from 'survey-core';
import { Survey } from 'survey-react-ui';

import { ApiError, fetchSurvey, submitResponse, type SurveyDetail } from './api';
import { RespondentLayout } from './RespondentLayout';
import { Card, CardBody, LoadingState } from './ui';

interface SurveyRunnerProps {
  surveyId: string;
  version: number;
}

export function SurveyRunner({ surveyId, version }: SurveyRunnerProps) {
  const [detail, setDetail] = useState<SurveyDetail | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [submitted, setSubmitted] = useState(false);

  useEffect(() => {
    let active = true;
    // Reset to the loading state so a survey/version change can't leave the prior
    // survey, its completion screen, or a stale error showing while the new one loads.
    setDetail(null);
    setError(null);
    setSubmitted(false);
    fetchSurvey(surveyId, version)
      .then((loaded) => {
        if (active) setDetail(loaded);
      })
      .catch((err: unknown) => {
        if (active) setError(err);
      });
    return () => {
      active = false;
    };
  }, [surveyId, version]);

  const model = useMemo(() => {
    if (!detail) return null;
    const m = new Model(detail.definition_json);
    // We render our own completion screen below, so suppress SurveyJS's built-in
    // "thank you" page (avoids a flash of its page before ours mounts).
    m.showCompletedPage = false;
    return m;
  }, [detail]);

  useEffect(() => {
    if (!model || !detail) return;
    const handleComplete = (sender: Model): void => {
      // Capture the shown-set from the engine at submit time (invariant 3).
      const shownQuestions = sender
        .getAllQuestions()
        .filter((question) => question.isVisible)
        .map((question) => question.name);
      void submitResponse(surveyId, version, {
        definition_hash: detail.definition_hash ?? '',
        payload: sender.data as Record<string, unknown>,
        shown_questions: shownQuestions,
      })
        .then(() => setSubmitted(true))
        .catch((err: unknown) => setError(err));
    };
    model.onComplete.add(handleComplete);
    return () => {
      model.onComplete.remove(handleComplete);
    };
  }, [model, detail, surveyId, version]);

  if (error) {
    const notFound = error instanceof ApiError && error.status === 404;
    return (
      <RespondentLayout>
        <Card>
          <CardBody>
            <h1 className="text-lg font-semibold text-ink" role="alert">
              {notFound ? 'Survey unavailable' : 'Something went wrong'}
            </h1>
            <p className="mt-2 text-sm text-muted">
              {notFound
                ? 'This survey could not be found, or it has not been published. Check the link and try again.'
                : 'We could not load this survey. Please try again in a moment.'}
            </p>
          </CardBody>
        </Card>
      </RespondentLayout>
    );
  }

  if (submitted) {
    return (
      <RespondentLayout>
        <Card>
          <CardBody className="text-center">
            <div
              aria-hidden
              className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-success-bg text-2xl text-success"
            >
              ✓
            </div>
            <h1 className="text-lg font-semibold text-ink">Thank you</h1>
            <p className="mt-2 text-sm text-muted">Your response was recorded.</p>
          </CardBody>
        </Card>
      </RespondentLayout>
    );
  }

  if (!model) {
    return (
      <RespondentLayout>
        <LoadingState label="Loading survey…" />
      </RespondentLayout>
    );
  }

  return (
    <RespondentLayout>
      <Survey model={model} />
    </RespondentLayout>
  );
}
