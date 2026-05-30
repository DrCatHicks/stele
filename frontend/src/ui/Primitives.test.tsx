import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Alert } from './Alert';
import { Card, CardBody } from './Card';
import { Field } from './Field';
import { PageHeader } from './PageHeader';

describe('UI primitives', () => {
  it('renders alert tones with the expected aria roles', () => {
    const { rerender } = render(<Alert tone="error">Error</Alert>);
    expect(screen.getByRole('alert')).toHaveTextContent('Error');

    rerender(<Alert tone="success">Saved</Alert>);
    expect(screen.getByRole('status')).toHaveTextContent('Saved');
  });

  it('renders card and card body content with provided attributes', () => {
    render(
      <Card data-testid="card" className="custom-card">
        <CardBody className="custom-body">Body</CardBody>
      </Card>,
    );

    const card = screen.getByTestId('card');
    expect(card).toHaveTextContent('Body');
    expect(card).toHaveClass('custom-card');
    expect(screen.getByText('Body')).toHaveClass('custom-body');
  });

  it('associates field labels with inputs and renders hints', () => {
    render(<Field id="email-field" label="Email" hint="Use your work email" placeholder="name@x.com" />);

    expect(screen.getByLabelText('Email')).toHaveAttribute('id', 'email-field');
    expect(screen.getByText('Use your work email')).toBeInTheDocument();
  });

  it('renders page headers with optional subtitle and actions', () => {
    render(
      <PageHeader
        title="Users"
        subtitle="Manage accounts"
        actions={<button type="button">Add user</button>}
      />,
    );

    expect(screen.getByRole('heading', { level: 1, name: 'Users' })).toBeInTheDocument();
    expect(screen.getByText('Manage accounts')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add user' })).toBeInTheDocument();
  });
});
