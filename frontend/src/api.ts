// Relative URLs; the Vite dev server proxies /surveys and /auth to the API
// (vite.config.ts). Cookies (the auth session) ride same-origin automatically.
const API_BASE = '';

export interface SurveyDetail {
  survey_id: string;
  version: number;
  status: string;
  definition_hash: string | null;
  definition_json: Record<string, unknown>;
}

// Metadata-only row from the admin list endpoint — no definition_json.
export interface SurveySummary {
  survey_id: string;
  version: number;
  status: string;
  definition_hash: string | null;
  published_at: string | null;
  created_at: string;
}

export interface SubmitBody {
  definition_hash: string;
  payload: Record<string, unknown>;
  shown_questions: string[];
}

export interface SubmitResult {
  raw_response_id: number;
  respondent_id: string;
  submitted_at: string;
}

export interface User {
  id: number;
  email: string;
  role: string;
  disabled: boolean;
  created_at: string;
}

/** An HTTP error carrying the status code, so callers can branch on 401 etc. */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

// AuthContext registers a handler here so a 401 from *any* call (e.g. a session
// that expired mid-session) clears the cached user and bounces to login, rather
// than each view having to special-case it.
type UnauthorizedHandler = () => void;
let unauthorizedHandler: UnauthorizedHandler | null = null;

export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler;
}

// FastAPI's HTTPException renders {"detail": "..."}; prefer that human-readable
// reason (the publish gate's 422 messages, 409 conflicts) over a synthetic one.
async function errorDetail(res: Response): Promise<string | null> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    return typeof body.detail === 'string' ? body.detail : null;
  } catch {
    return null;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    if (res.status === 401) unauthorizedHandler?.();
    const detail = await errorDetail(res);
    throw new ApiError(
      res.status,
      detail ?? `${init?.method ?? 'GET'} ${path} failed (${res.status})`,
    );
  }
  // 204 (logout) has no body.
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function jsonInit(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  };
}

// --- Respondent-facing (unchanged behavior) --------------------------------

export async function fetchSurvey(surveyId: string, version: number): Promise<SurveyDetail> {
  return request<SurveyDetail>(`/surveys/${surveyId}/versions/${version}`);
}

export async function submitResponse(
  surveyId: string,
  version: number,
  body: SubmitBody,
): Promise<SubmitResult> {
  return request<SubmitResult>(
    `/surveys/${surveyId}/versions/${version}/responses`,
    jsonInit('POST', body),
  );
}

// --- Auth ------------------------------------------------------------------

export async function login(email: string, password: string): Promise<User> {
  return request<User>('/auth/login', jsonInit('POST', { email, password }));
}

export async function logout(): Promise<void> {
  await request<void>('/auth/logout', jsonInit('POST'));
}

export async function fetchCurrentUser(): Promise<User> {
  return request<User>('/auth/me');
}

// --- Authoring (admin) -----------------------------------------------------

export async function listSurveys(): Promise<SurveySummary[]> {
  return request<SurveySummary[]>('/surveys');
}

export async function createSurvey(
  definitionJson: Record<string, unknown>,
): Promise<SurveySummary> {
  return request<SurveySummary>('/surveys', jsonInit('POST', { definition_json: definitionJson }));
}

export async function editSurvey(
  surveyId: string,
  version: number,
  definitionJson: Record<string, unknown>,
): Promise<SurveySummary> {
  return request<SurveySummary>(
    `/surveys/${surveyId}/versions/${version}`,
    jsonInit('PUT', { definition_json: definitionJson }),
  );
}

export async function publishSurvey(surveyId: string, version: number): Promise<SurveySummary> {
  return request<SurveySummary>(
    `/surveys/${surveyId}/versions/${version}/publish`,
    jsonInit('POST'),
  );
}

// --- GDPR / erasure (admin) ------------------------------------------------

// A row from the pii.withdrawals erasure audit.
export interface WithdrawalAudit {
  id: number;
  respondent_id: string;
  requested_at: string;
  reason: string | null;
}

// Outcome of a withdrawal trigger; counts are zero on the idempotent repeat path.
export interface WithdrawalResult {
  respondent_id: string;
  requested_at: string;
  already_withdrawn: boolean;
  raw_rows_tombstoned: number;
  responses_purged: number;
  pii_rows_deleted: number;
}

export async function listWithdrawals(): Promise<WithdrawalAudit[]> {
  return request<WithdrawalAudit[]>('/admin/withdrawals');
}

export async function triggerWithdrawal(
  respondentId: string,
  reason?: string,
): Promise<WithdrawalResult> {
  return request<WithdrawalResult>(
    `/respondents/${respondentId}/withdrawal`,
    jsonInit('POST', { reason: reason ?? null }),
  );
}

// --- PII free-text review (reviewer) ---------------------------------------

export type ReviewStatus = 'pending' | 'promoted' | 'rejected';

// A high-risk free-text answer in the screening queue. value_text is the PII the
// reviewer screens; status is null while pending.
export interface FreeTextReviewItem {
  id: number;
  raw_response_id: number;
  respondent_id: string;
  survey_id: string;
  survey_version: number;
  question_name: string;
  value_text: string | null;
  created_at: string;
  status: string | null;
}

export interface FreeTextDecision {
  free_text_id: number;
  raw_response_id: number;
  question_name: string;
  status: string;
  reviewed_at: string;
}

export async function listFreeTextForReview(
  status: ReviewStatus = 'pending',
): Promise<FreeTextReviewItem[]> {
  return request<FreeTextReviewItem[]>(`/admin/pii/free-text?status=${status}`);
}

export async function promoteFreeText(id: number, note?: string): Promise<FreeTextDecision> {
  return request<FreeTextDecision>(
    `/admin/pii/free-text/${id}/promote`,
    jsonInit('POST', { note: note ?? null }),
  );
}

export async function rejectFreeText(id: number, note?: string): Promise<FreeTextDecision> {
  return request<FreeTextDecision>(
    `/admin/pii/free-text/${id}/reject`,
    jsonInit('POST', { note: note ?? null }),
  );
}
