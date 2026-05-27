import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Badge, statusTone } from './Badge';

describe('Badge', () => {
  it('renders its label', () => {
    render(<Badge tone="success">published</Badge>);
    expect(screen.getByText('published')).toBeInTheDocument();
  });
});

describe('statusTone', () => {
  it.each([
    ['published', 'success'],
    ['promoted', 'success'],
    ['rejected', 'danger'],
    ['pending', 'warning'],
    ['draft', 'brand'],
    ['anything-else', 'neutral'],
  ])('maps %s → %s tone', (status, tone) => {
    expect(statusTone(status)).toBe(tone);
  });
});
