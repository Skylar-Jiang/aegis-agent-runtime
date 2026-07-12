import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import App from './App'

describe('Phase 1 shell', () => {
  it('identifies the current runtime foundation phase', () => {
    render(<App />)

    expect(
      screen.getByRole('heading', { name: 'RA-Agent Runtime' }),
    ).toBeInTheDocument()
    expect(screen.getByText('Phase 1 Runtime 基础闭环')).toBeInTheDocument()
  })
})
