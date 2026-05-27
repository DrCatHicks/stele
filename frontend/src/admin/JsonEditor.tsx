import { json, jsonParseLinter } from '@codemirror/lang-json';
import { lintGutter, linter } from '@codemirror/lint';
import CodeMirror, { EditorView } from '@uiw/react-codemirror';
import { useMemo } from 'react';

interface JsonEditorProps {
  value: string;
  onChange: (value: string) => void;
  readOnly?: boolean;
  'aria-label'?: string;
}

/**
 * CodeMirror 6 JSON editor for authoring survey definitions — syntax highlight,
 * code folding, line numbers, and an inline lint gutter (malformed JSON flagged at
 * its position, not just as a banner; semantic publish-gate errors come back from
 * the API as a 422 and are shown by the caller, which has no source positions to
 * anchor them). A published survey is read-only (definitions are immutable).
 */
export function JsonEditor({
  value,
  onChange,
  readOnly = false,
  'aria-label': ariaLabel,
}: JsonEditorProps) {
  // Name the actual editable surface (CodeMirror's contenteditable), not just the
  // wrapper, so screen readers and getByLabelText resolve the editor itself.
  const extensions = useMemo(
    () => [
      json(),
      linter(jsonParseLinter()),
      lintGutter(),
      EditorView.contentAttributes.of({ 'aria-label': ariaLabel ?? 'JSON editor' }),
    ],
    [ariaLabel],
  );

  return (
    <div className="overflow-hidden rounded-md border border-border">
      <CodeMirror
        value={value}
        onChange={onChange}
        editable={!readOnly}
        readOnly={readOnly}
        extensions={extensions}
        height="420px"
        basicSetup={{ foldGutter: true, lineNumbers: true, highlightActiveLine: !readOnly }}
      />
    </div>
  );
}
