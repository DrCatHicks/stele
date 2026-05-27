import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SurveyListView } from './SurveyListView';

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  listSurveys: vi.fn(),
  createSurvey: vi.fn(),
}));

const { listSurveys, createSurvey } = await import('../api');
const mockedList = vi.mocked(listSurveys);
const mockedCreate = vi.mocked(createSurvey);

afterEach(() => {
  vi.clearAllMocks();
});

function renderList() {
  return render(
    <MemoryRouter initialEntries={['/admin']}>
      <Routes>
        <Route path="/admin" element={<SurveyListView />} />
        <Route
          path="/admin/surveys/:surveyId/versions/:version"
          element={<div>Editor opened</div>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe('SurveyListView', () => {
  it('lists every survey/version with status badges and response counts', async () => {
    mockedList.mockResolvedValue([
      {
        survey_id: 'aaa',
        version: 2,
        status: 'draft',
        definition_hash: null,
        published_at: null,
        created_at: 't2',
        response_count: 0,
      },
      {
        survey_id: 'aaa',
        version: 1,
        status: 'published',
        definition_hash: 'h',
        published_at: '2026-01-01T00:00:00Z',
        created_at: 't1',
        response_count: 5,
      },
    ]);
    renderList();

    expect(await screen.findByText('draft')).toBeInTheDocument();
    expect(screen.getByText('published')).toBeInTheDocument();
    // Both versions of the one survey are grouped, each with an Open link.
    expect(screen.getAllByRole('link', { name: /Open aaa/ })).toHaveLength(2);
    // The version summary line reports the survey's total live responses.
    expect(screen.getByText(/5 responses/)).toBeInTheDocument();
  });

  it('shows an empty state when there are no surveys', async () => {
    mockedList.mockResolvedValue([]);
    renderList();
    expect(await screen.findByText('No surveys yet.')).toBeInTheDocument();
  });

  it('creates a draft and navigates to its editor', async () => {
    mockedList.mockResolvedValue([]);
    mockedCreate.mockResolvedValue({
      survey_id: 'new-id',
      version: 1,
      status: 'draft',
      definition_hash: null,
      published_at: null,
      created_at: 't',
    });
    renderList();
    await screen.findByText('No surveys yet.');

    await userEvent.click(screen.getByRole('button', { name: 'New survey' }));

    expect(mockedCreate).toHaveBeenCalledOnce();
    expect(await screen.findByText('Editor opened')).toBeInTheDocument();
  });
});
