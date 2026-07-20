import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ApprovalsPage } from './ApprovalsPage';

describe('ApprovalsPage', () => {
  it('requires an explicit approver identity before a decision can be submitted', () => {
    render(<ApprovalsPage />);

    fireEvent.change(screen.getByPlaceholderText('Approval ID...'), {
      target: { value: 'approval-1' },
    });

    expect(screen.getByRole('button', { name: 'Grant' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Deny' })).toBeDisabled();
  });
});
