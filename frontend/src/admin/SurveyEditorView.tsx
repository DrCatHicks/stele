import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';

import { editSurvey, fetchSurvey, publishSurvey, type SurveyDetail } from '../api';
import { SurveyPreview } from './SurveyPreview';

// ApiError extends Error and now carries the API's `detail` (e.g. the publish
// gate's 422 reason), so the generic message is the human-readable one.
function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function SurveyEditorView() {
  const { surveyId, version: versionParam } = useParams();
  const version = Number(versionParam);

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

  // Parse the textarea once for both preview and save; a parse error disables
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

  if (error && detail === null) return <div role="alert">Error: {error}</div>;
  if (detail === null) return <div role="status">Loading…</div>;

  return (
    <section>
      <h1>
        {surveyId} · v{version} <small>({detail.status})</small>
      </h1>
      {error ? (
        <p role="alert" style={{ color: 'crimson' }}>
          {error}
        </p>
      ) : null}
      {notice ? <p role="status">{notice}</p> : null}

      <label style={{ display: 'block' }}>
        Definition JSON
        <textarea
          aria-label="Definition JSON"
          value={draftText}
          onChange={(e) => setDraftText(e.target.value)}
          readOnly={isPublished}
          spellCheck={false}
          rows={20}
          style={{ width: '100%', fontFamily: 'monospace' }}
        />
      </label>
      {!parsed.ok ? (
        <p role="alert" style={{ color: 'crimson' }}>
          Invalid JSON: {parsed.message}
        </p>
      ) : null}

      <div style={{ display: 'flex', gap: '0.5rem', margin: '0.5rem 0' }}>
        <button type="button" onClick={handleSave} disabled={busy || isPublished || !parsed.ok}>
          Save draft
        </button>
        <button type="button" onClick={handlePublish} disabled={busy || isPublished}>
          Publish
        </button>
        <button type="button" onClick={() => setShowPreview((v) => !v)} disabled={!parsed.ok}>
          {showPreview ? 'Hide preview' : 'Preview'}
        </button>
      </div>
      {isPublished ? (
        <p>
          <em>Published surveys are immutable. Create a new draft version to make changes.</em>
        </p>
      ) : null}

      {showPreview && parsed.ok ? (
        <div style={{ borderTop: '1px solid #ddd', marginTop: '1rem', paddingTop: '1rem' }}>
          <h2>Preview</h2>
          <SurveyPreview definition={parsed.value} />
        </div>
      ) : null}
    </section>
  );
}
