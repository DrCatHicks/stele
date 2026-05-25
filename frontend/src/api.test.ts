import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  ApiError,
  fetchSurvey,
  listSurveys,
  login,
  publishSurvey,
  setUnauthorizedHandler,
  submitResponse,
} from './api';

afterEach(() => {
  vi.unstubAllGlobals();
  setUnauthorizedHandler(null);
});

function okJson(value: unknown): Response {
  return { ok: true, status: 200, json: () => Promise.resolve(value) } as unknown as Response;
}

describe('fetchSurvey', () => {
  it('requests the version endpoint and returns the detail', async () => {
    const detail = {
      survey_id: 's',
      version: 1,
      status: 'published',
      definition_hash: 'h',
      definition_json: { pages: [] },
    };
    const fetchMock = vi.fn().mockResolvedValue(okJson(detail));
    vi.stubGlobal('fetch', fetchMock);

    const result = await fetchSurvey('s', 1);

    expect(fetchMock).toHaveBeenCalledWith('/api/surveys/s/versions/1', undefined);
    expect(result).toEqual(detail);
  });

  it('throws an ApiError carrying the status on a non-ok response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 404 } as unknown as Response),
    );
    await expect(fetchSurvey('s', 1)).rejects.toMatchObject({ status: 404 });
    await expect(fetchSurvey('s', 1)).rejects.toBeInstanceOf(ApiError);
  });
});

describe('submitResponse', () => {
  it('posts the JSON body to the responses endpoint', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(okJson({ raw_response_id: 1, respondent_id: 'r', submitted_at: 't' }));
    vi.stubGlobal('fetch', fetchMock);

    const body = { definition_hash: 'h', payload: { q1: 'a' }, shown_questions: ['q1'] };
    const result = await submitResponse('s', 1, body);

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/surveys/s/versions/1/responses',
      expect.objectContaining({ method: 'POST', body: JSON.stringify(body) }),
    );
    expect(result.raw_response_id).toBe(1);
  });
});

describe('auth + authoring calls', () => {
  it('login posts credentials and returns the user', async () => {
    const user = { id: 1, email: 'a@b.c', role: 'admin', disabled: false, created_at: 't' };
    const fetchMock = vi.fn().mockResolvedValue(okJson(user));
    vi.stubGlobal('fetch', fetchMock);

    const result = await login('a@b.c', 'pw');

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/auth/login',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ email: 'a@b.c', password: 'pw' }),
      }),
    );
    expect(result).toEqual(user);
  });

  it('listSurveys requests the collection endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(okJson([]));
    vi.stubGlobal('fetch', fetchMock);

    await listSurveys();

    expect(fetchMock).toHaveBeenCalledWith('/api/surveys', undefined);
  });

  it('publishSurvey posts to the publish endpoint', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(okJson({ survey_id: 's', version: 1, status: 'published' }));
    vi.stubGlobal('fetch', fetchMock);

    await publishSurvey('s', 1);

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/surveys/s/versions/1/publish',
      expect.objectContaining({ method: 'POST' }),
    );
  });
});

describe('unauthorized handler', () => {
  it('fires on a 401 so the session-expiry path can clear the user', async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 401 } as unknown as Response),
    );

    await expect(listSurveys()).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it('does not fire on a non-401 error', async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 500 } as unknown as Response),
    );

    await expect(listSurveys()).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });
});
