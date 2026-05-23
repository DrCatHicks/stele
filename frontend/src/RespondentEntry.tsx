import { useSearchParams } from 'react-router-dom';

import { SurveyRunner } from './SurveyRunner';

/**
 * Public respondent entry. Reads ?survey=<id>&version=<n> from the URL, exactly
 * as the pre-router app did, and renders the runner. Behavior is unchanged — the
 * router just hosts it at "/".
 */
export function RespondentEntry() {
  const [params] = useSearchParams();
  const surveyId = params.get('survey') ?? '';
  const version = Number(params.get('version') ?? '1');

  if (!surveyId) {
    return (
      <p>Provide ?survey=&lt;id&gt;&amp;version=&lt;n&gt; in the URL to load a published survey.</p>
    );
  }
  return <SurveyRunner surveyId={surveyId} version={version} />;
}
