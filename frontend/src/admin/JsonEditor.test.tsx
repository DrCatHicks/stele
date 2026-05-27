import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { JsonEditor } from './JsonEditor';

// Smoke test only: confirm the CodeMirror-backed editor mounts in jsdom without
// throwing and wires the label + editor surface. Editing behaviour is exercised
// through SurveyEditorView (where JsonEditor is stubbed) — CodeMirror's
// contenteditable doesn't support userEvent typing under jsdom.
describe('JsonEditor', () => {
  it('mounts a CodeMirror editor with the labelled container', () => {
    const { container } = render(
      <JsonEditor value={'{"a": 1}'} onChange={vi.fn()} aria-label="Definition JSON" />,
    );
    expect(container.querySelector('[aria-label="Definition JSON"]')).toBeInTheDocument();
    expect(container.querySelector('.cm-editor')).toBeInTheDocument();
  });

  it('renders read-only without throwing', () => {
    const { container } = render(
      <JsonEditor value={'{"a": 1}'} onChange={vi.fn()} readOnly aria-label="Definition JSON" />,
    );
    expect(container.querySelector('.cm-editor')).toBeInTheDocument();
  });
});
