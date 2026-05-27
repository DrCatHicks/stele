import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';

import { editSurvey, fetchSurvey, publishSurvey, type SurveyDetail } from '../api';
import { Alert, Badge, Button, Card, CardBody, LoadingState, PageHeader, statusTone } from '../ui';
import { JsonEditor } from './JsonEditor';
import { SurveyPreview } from './SurveyPreview';
import { STARTER_TEMPLATES } from './starterTemplates';

// ApiError extends Error and now carries the API's `detail` (e.g. the publish
// gate's 422 reason), so the generic message is the human-readable one.
function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function SurveyEditorView() {
  const { surveyId, version: versionParam } = useParams();
  const version = Number(versionParam);
  const paramsInvalid = !surveyId || Number.isNaN(version);

  const [detail, setDetail] = useState<SurveyDetail | null>(null);
  const [draftText, setDraftText] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showPreview, setShowPreview] = useState(false);

  useEffect(() => {
    if (!surveyId || Number.isNaN(version)) return;
    let active = true;
    fetchSurvey(surveyId, version)
      .then((loaded) => {
        if (!active) return;
        setDetail(loaded);
        setDraftText(JSON.stringify(loaded.definition_json, null, 2));
      })
      .catch((err: unknown) => {
        if (active) setError(errorMessage(err));
      });
    return () => {
      active = false;
    };
  }, [surveyId, version]);

  // Parse the editor text once for both preview and save; a parse error disables
  // both and is shown inline rather than sent to the API.
  const parsed = useMemo<
    { ok: true; value: Record<string, unknown> } | { ok: false; message: string }
  >(() => {
    try {
      const value = JSON.parse(draftText) as Record<string, unknown>;
      return { ok: true, value };
    } catch (err) {
      return { ok: false, message: errorMessage(err) };
    }
  }, [draftText]);

  const isPublished = detail?.status === 'published';

  const handleSave = (): void => {
    if (!surveyId || !parsed.ok) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    editSurvey(surveyId, version, parsed.value)
      .then(() => setNotice('Saved.'))
      .catch((err: unknown) => setError(errorMessage(err)))
      .finally(() => setBusy(false));
  };

  const handlePublish = (): void => {
    if (!surveyId) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    publishSurvey(surveyId, version)
      .then((published) => {
        setNotice(`Published (hash ${published.definition_hash ?? ''}).`);
        setDetail((prev) => (prev ? { ...prev, status: published.status } : prev));
      })
      .catch((err: unknown) => setError(errorMessage(err)))
      .finally(() => setBusy(false));
  };

  const applyTemplate = (id: string): void => {
    const template = STARTER_TEMPLATES.find((t) => t.id === id);
    if (!template) return;
    setDraftText(JSON.stringify(template.definition, null, 2));
    setNotice(null);
    setError(null);
  };

  // Guard a malformed URL (e.g. a non-numeric version) up front, so it shows an
  // error instead of hanging forever on the detail===null loading state.
  if (paramsInvalid) return <Alert tone="error">Invalid survey URL.</Alert>;
  if (error && detail === null) return <Alert tone="error">Error: {error}</Alert>;
  if (detail === null) return <LoadingState />;

  return (
    <section>
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            <code className="text-base">{surveyId}</code>
            <span className="text-muted">· v{version}</span>
            <Badge tone={statusTone(detail.status)}>{detail.status}</Badge>
          </span>
        }
        actions={
          <>
            <Button
              type="button"
              variant="secondary"
              onClick={() => setShowPreview((v) => !v)}
              disabled={!parsed.ok}
            >
              {showPreview ? 'Hide preview' : 'Preview'}
            </Button>
            <Button
              type="button"
              variant="secondary"
              onClick={handleSave}
              disabled={busy || isPublished || !parsed.ok}
            >
              Save draft
            </Button>
            <Button type="button" onClick={handlePublish} disabled={busy || isPublished}>
              Publish
            </Button>
          </>
        }
      />

      {error ? <Alert tone="error">{error}</Alert> : null}
      {notice ? <Alert tone="success">{notice}</Alert> : null}
      {isPublished ? (
        <Alert tone="info">
          Published surveys are immutable. Create a new draft version to make changes.
        </Alert>
      ) : null}

      <Card className="mt-4">
        <CardBody className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm font-medium text-ink">Definition JSON</span>
            {!isPublished ? (
              <label className="flex items-center gap-2 text-sm text-muted">
                Start from template
                <select
                  aria-label="Start from template"
                  defaultValue=""
                  onChange={(e) => {
                    applyTemplate(e.target.value);
                    e.target.selectedIndex = 0;
                  }}
                  className="rounded-md border border-border bg-surface px-2 py-1 text-sm text-ink"
                >
                  <option value="" disabled>
                    Choose…
                  </option>
                  {STARTER_TEMPLATES.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
          </div>

          <JsonEditor
            aria-label="Definition JSON"
            value={draftText}
            onChange={setDraftText}
            readOnly={isPublished}
          />
          {!parsed.ok ? <Alert tone="error">Invalid JSON: {parsed.message}</Alert> : null}
        </CardBody>
      </Card>

      {showPreview && parsed.ok ? (
        <Card className="mt-4">
          <CardBody>
            <h2 className="mb-2 text-sm font-semibold text-ink">Preview</h2>
            <SurveyPreview definition={parsed.value} />
          </CardBody>
        </Card>
      ) : null}
    </section>
  );
}
