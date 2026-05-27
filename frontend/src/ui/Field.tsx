import { useId, type InputHTMLAttributes } from 'react';

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  hint?: string;
}

const INPUT_CLASSES =
  'w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-ink ' +
  'placeholder:text-faint focus:border-brand focus:outline-none ' +
  'focus:ring-2 focus:ring-brand/30 read-only:bg-canvas read-only:text-muted';

/**
 * A labelled text input. Associates label↔input by id (so `getByLabelText`
 * resolves) and exposes the input classes for reuse on bare inputs.
 */
export function Field({ label, hint, id, className = '', ...rest }: FieldProps) {
  const generated = useId();
  const inputId = id ?? generated;
  return (
    <div className={`flex flex-col gap-1 ${className}`}>
      <label htmlFor={inputId} className="text-sm font-medium text-ink">
        {label}
      </label>
      <input id={inputId} className={INPUT_CLASSES} {...rest} />
      {hint ? <p className="text-xs text-muted">{hint}</p> : null}
    </div>
  );
}

export { INPUT_CLASSES };
