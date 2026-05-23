import { useMemo } from 'react';
import { Model } from 'survey-core';
import { Survey } from 'survey-react-ui';

/**
 * Renders a survey definition with the live SurveyJS engine for authoring
 * preview. Deliberately NOT wired to onComplete/submit — previewing must never
 * write a response (contrast SurveyRunner, which does submit). Completing the
 * preview just shows SurveyJS's built-in completion page.
 */
export function SurveyPreview({ definition }: { definition: Record<string, unknown> }) {
  const model = useMemo(() => new Model(definition), [definition]);
  return <Survey model={model} />;
}
