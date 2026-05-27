import { useSearchParams } from 'react-router-dom';

import { RespondentLayout } from './RespondentLayout';
import { SurveyRunner } from './SurveyRunner';
import { Card, CardBody } from './ui';

/**
 * Public respondent entry. Reads ?survey=<id>&version=<n> from the URL and
 * renders the runner; with no survey id it shows a friendly prompt instead of a
 * bare sentence.
 */
export function RespondentEntry() {
  const [params] = useSearchParams();
  const surveyId = params.get('survey') ?? '';
  const version = Number(params.get('version') ?? '1');

  if (!surveyId) {
    return (
      <RespondentLayout>
        <Card>
          <CardBody>
            <h1 className="text-lg font-semibold text-ink">No survey selected</h1>
            <p className="mt-2 text-sm text-muted">
              Open a survey using the link you were given. A valid link includes a survey
              identifier, for example <code className="text-ink">?survey=…&version=1</code>.
            </p>
          </CardBody>
        </Card>
      </RespondentLayout>
    );
  }
  return <SurveyRunner surveyId={surveyId} version={version} />;
}
