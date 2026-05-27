import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { Button } from './Button';

describe('Button', () => {
  it('renders its children and fires onClick', async () => {
    const onClick = vi.fn();
    render(
      <Button type="button" onClick={onClick}>
        Save draft
      </Button>,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Save draft' }));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('applies the variant styling and stays the requested type', () => {
    render(
      <Button type="submit" variant="danger">
        Erase
      </Button>,
    );
    const button = screen.getByRole('button', { name: 'Erase' });
    expect(button).toHaveAttribute('type', 'submit');
    expect(button.className).toContain('bg-danger');
  });

  it('does not fire when disabled', async () => {
    const onClick = vi.fn();
    render(
      <Button type="button" disabled onClick={onClick}>
        Publish
      </Button>,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Publish' }));
    expect(onClick).not.toHaveBeenCalled();
  });
});
