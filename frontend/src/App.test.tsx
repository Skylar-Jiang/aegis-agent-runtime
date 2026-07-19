import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import App from './App'

describe('App shell', () => {
  it('renders the Tasks page as home', () => {
    render(<App />)
    expect(screen.getByRole('heading', { name: 'Tasks' })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Enter task objective...')).toBeInTheDocument()
  })
})
