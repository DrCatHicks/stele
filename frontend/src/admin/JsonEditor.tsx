import { json, jsonParseLinter } from '@codemirror/lang-json';
import { lintGutter, linter } from '@codemirror/lint';
import CodeMirror from '@uiw/react-codemirror';

interface JsonEditorProps {
  value: string;
  onChange: (value: string) => void;
  readOnly?: boolean;
  'aria-label'?: string;
}

// JSON syntax linting drives the inline error gutter — malformed JSON is flagged
// at its position, not just as a banner. Semantic publish-gate errors (dup names,
// dangling visibleIf) come back from the API as a 422 and are shown by the caller
// (the API doesn't return source positions to anchor them in the editor).
const EXTENSIONS = [json(), linter(jsonParseLinter()), lintGutter()];

/**
 * CodeMirror 6 JSON editor for authoring survey definitions — syntax highlight,
 * code folding, line numbers, and an inline lint gutter. Replaces the raw
 * textarea. A published survey is read-only (definitions are immutable).
 */
export function JsonEditor({
  value,
  onChange,
  readOnly = false,
  'aria-label': ariaLabel,
}: JsonEditorProps) {
  return (
    <div className="overflow-hidden rounded-md border border-border" aria-label={ariaLabel}>
      <CodeMirror
        value={value}
        onChange={onChange}
        editable={!readOnly}
        readOnly={readOnly}
        extensions={EXTENSIONS}
        height="420px"
        basicSetup={{ foldGutter: true, lineNumbers: true, highlightActiveLine: !readOnly }}
      />
    </div>
  );
}
