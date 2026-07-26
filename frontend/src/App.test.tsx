import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import App from './App'

describe('App shell', () => {
  it('renders the agent workbench with task navigation and a composer', () => {
    render(<App />)
    expect(screen.getByRole('button', { name: 'New Task' })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Describe a task for the runtime…')).toBeInTheDocument()
    expect(screen.getByText('Safety inspector')).toBeInTheDocument()
  })
})
