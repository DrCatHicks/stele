import { useEffect, useMemo, useState } from 'react';
import { Model } from 'survey-core';
import { Survey } from 'survey-react-ui';

import { Alert, Button } from '../ui';

interface Snapshot {
  data: Record<string, unknown>;
  shown: string[];
}

/**
 * Interactive authoring preview. Runs the survey with the live SurveyJS engine so
 * an author can walk every branch and complete a test response, and surfaces the
 * shown-set + captured payload that the *real* runner would submit — the routing
 * self-test the publish gate's oracle can't show interactively.
 *
 * Deliberately CLIENT-SIDE ONLY: it never calls submitResponse / the API, so no
 * synthetic row ever reaches the append-only raw_responses (invariant 1). Contrast
 * SurveyRunner, which submits. The "no network on completion" guarantee is pinned
 * by SurveyPreview.test.
 */
export function SurveyPreview({ definition }: { definition: Record<string, unknown> }) {
  const model = useMemo(() => {
    const m = new Model(definition);
    // We render our own preview summary, not SurveyJS's completion page.
    m.showCompletedPage = false;
    return m;
  }, [definition]);

  const [completed, setCompleted] = useState(false);
  const [snapshot, setSnapshot] = useState<Snapshot>({ data: {}, shown: [] });

  useEffect(() => {
    const capture = (sender: Model): void => {
      const shown = sender
        .getAllQuestions()
        .filter((q) => q.isVisible)
        .map((q) => q.name);
      setSnapshot({ data: { ...(sender.data as Record<string, unknown>) }, shown });
    };
    const handleComplete = (sender: Model): void => {
      capture(sender);
      setCompleted(true);
    };
    model.onValueChanged.add(capture);
    model.onComplete.add(handleComplete);
    capture(model);
    return () => {
      model.onValueChanged.remove(capture);
      model.onComplete.remove(handleComplete);
    };
  }, [model]);

  const restart = (): void => {
    model.clear(true, true); // clear data, return to first page
    setCompleted(false);
    setSnapshot({ data: {}, shown: [] });
  };

  return (
    <div className="flex flex-col gap-3">
      <Alert tone="info">Preview — responses are not recorded.</Alert>

      {completed ? (
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold text-ink">Preview complete — not submitted</h3>
            <Button type="button" variant="secondary" size="sm" onClick={restart}>
              Restart preview
            </Button>
          </div>
          <p className="text-sm text-muted">
            This is what the respondent runner would have submitted. Nothing was sent.
          </p>
        </div>
      ) : (
        <Survey model={model} />
      )}

      <details
        className="rounded-md border border-border bg-canvas px-3 py-2 text-sm"
        open={completed}
      >
        <summary className="cursor-pointer font-medium text-ink">
          Captured so far ({snapshot.shown.length} shown)
        </summary>
        <div className="mt-2 flex flex-col gap-2">
          <div>
            <span className="text-xs font-semibold uppercase tracking-wide text-faint">
              Shown questions
            </span>
            <p className="text-muted">{snapshot.shown.join(', ') || '—'}</p>
          </div>
          <div>
            <span className="text-xs font-semibold uppercase tracking-wide text-faint">
              Payload
            </span>
            <pre className="overflow-x-auto rounded bg-surface p-2 text-xs text-ink">
              {JSON.stringify(snapshot.data, null, 2)}
            </pre>
          </div>
        </div>
      </details>
    </div>
  );
}
