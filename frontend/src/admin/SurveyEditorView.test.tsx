import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api';
import { SurveyEditorView } from './SurveyEditorView';

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  fetchSurvey: vi.fn(),
  editSurvey: vi.fn(),
  publishSurvey: vi.fn(),
}));

const { fetchSurvey, editSurvey, publishSurvey } = await import('../api');
const mockedFetch = vi.mocked(fetchSurvey);
const mockedEdit = vi.mocked(editSurvey);
const mockedPublish = vi.mocked(publishSurvey);

const DRAFT_DETAIL = {
  survey_id: 's',
  version: 1,
  status: 'draft',
  definition_hash: null,
  definition_json: { pages: [{ name: 'p1', elements: [] }] },
};

afterEach(() => {
  vi.clearAllMocks();
});

function renderEditor() {
  return render(
    <MemoryRouter initialEntries={['/admin/surveys/s/versions/1']}>
      <Routes>
        <Route path="/admin/surveys/:surveyId/versions/:version" element={<SurveyEditorView />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('SurveyEditorView', () => {
  it('loads the definition into the editor textarea', async () => {
    mockedFetch.mockResolvedValue(DRAFT_DETAIL);
    renderEditor();
    const textarea = await screen.findByLabelText<HTMLTextAreaElement>('Definition JSON');
    expect(JSON.parse(textarea.value)).toEqual(DRAFT_DETAIL.definition_json);
  });

  it('disables Save and flags invalid JSON', async () => {
    mockedFetch.mockResolvedValue(DRAFT_DETAIL);
    renderEditor();
    const textarea = await screen.findByLabelText('Definition JSON');

    await userEvent.clear(textarea);
    // Braces are userEvent.type key-syntax; a brace-free string is still invalid JSON.
    await userEvent.type(textarea, 'not valid json');

    expect(await screen.findByText(/Invalid JSON/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save draft' })).toBeDisabled();
  });

  it('saves a valid edited draft', async () => {
    mockedFetch.mockResolvedValue(DRAFT_DETAIL);
    mockedEdit.mockResolvedValue({
      survey_id: 's',
      version: 1,
      status: 'draft',
      definition_hash: null,
      published_at: null,
      created_at: 't',
    });
    renderEditor();
    await screen.findByLabelText('Definition JSON');

    await userEvent.click(screen.getByRole('button', { name: 'Save draft' }));

    await waitFor(() => expect(mockedEdit).toHaveBeenCalledOnce());
    expect(await screen.findByText('Saved.')).toBeInTheDocument();
  });

  it('publishes and surfaces the publish-gate error verbatim', async () => {
    mockedFetch.mockResolvedValue(DRAFT_DETAIL);
    mockedPublish.mockRejectedValue(new ApiError(422, 'PUT /surveys failed (422)'));
    renderEditor();
    await screen.findByLabelText('Definition JSON');

    await userEvent.click(screen.getByRole('button', { name: 'Publish' }));

    await waitFor(() => expect(mockedPublish).toHaveBeenCalledOnce());
    expect(await screen.findByRole('alert')).toHaveTextContent('422');
  });

  it('locks a published survey read-only', async () => {
    mockedFetch.mockResolvedValue({ ...DRAFT_DETAIL, status: 'published', definition_hash: 'h' });
    renderEditor();
    const textarea = await screen.findByLabelText('Definition JSON');

    expect(textarea).toHaveAttribute('readonly');
    expect(screen.getByRole('button', { name: 'Save draft' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Publish' })).toBeDisabled();
    expect(screen.getByText(/immutable/)).toBeInTheDocument();
  });
});
