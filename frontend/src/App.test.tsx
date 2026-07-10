import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import App from './App'

describe('Phase 0 shell', () => {
  it('identifies the repository as an engineering skeleton', () => {
    render(<App />)

    expect(
      screen.getByRole('heading', { name: 'RA-Agent Runtime' }),
    ).toBeInTheDocument()
    expect(screen.getByText('Phase 0 工程骨架')).toBeInTheDocument()
  })
})
