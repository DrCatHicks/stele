import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { RespondentLayout } from './RespondentLayout';

describe('RespondentLayout', () => {
  it('renders the Stele header and page content', () => {
    render(
      <RespondentLayout>
        <p>Survey content</p>
      </RespondentLayout>,
    );

    expect(screen.getByText('Stele')).toBeInTheDocument();
    expect(screen.getByText('Survey content')).toBeInTheDocument();
  });
});
