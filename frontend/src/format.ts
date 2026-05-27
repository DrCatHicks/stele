// Timestamps from the API are UTC ISO strings. Format them in UTC explicitly so
// the displayed day can't shift for near-midnight values and is identical across
// clients regardless of their locale/timezone — important for the erasure audit.

const DATE = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'UTC',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});

const DATE_TIME = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'UTC',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

/** Stable UTC date, e.g. "2026-01-31". */
export function formatDate(iso: string): string {
  return DATE.format(new Date(iso));
}

/** Stable UTC date + time with an explicit zone label, e.g. "2026-01-31, 14:05 UTC". */
export function formatDateTime(iso: string): string {
  return `${DATE_TIME.format(new Date(iso))} UTC`;
}
