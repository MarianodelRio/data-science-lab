import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Layout } from './Layout'

describe('Layout', () => {
  it('renders a sidebar landmark', () => {
    render(<Layout />)
    expect(screen.getByRole('complementary')).toBeInTheDocument()
  })

  it('renders a tablist with the four expected tabs', () => {
    render(<Layout />)
    expect(screen.getByRole('tablist')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /pipeline/i })).toBeInTheDocument()
    expect(
      screen.getByRole('tab', { name: /experiments/i }),
    ).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /files/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /chat/i })).toBeInTheDocument()
  })

  it('shows the Pipeline panel by default', () => {
    render(<Layout />)
    const panel = screen.getByRole('tabpanel')
    expect(panel).toHaveTextContent(/pipeline view is not implemented yet/i)
  })

  it('switches the visible tabpanel when another tab is clicked', async () => {
    const user = userEvent.setup()
    render(<Layout />)

    await user.click(screen.getByRole('tab', { name: /experiments/i }))
    expect(screen.getByRole('tabpanel')).toHaveTextContent(
      /experiments table is not implemented yet/i,
    )

    await user.click(screen.getByRole('tab', { name: /files/i }))
    expect(screen.getByRole('tabpanel')).toHaveTextContent(
      /file viewer is not implemented yet/i,
    )

    await user.click(screen.getByRole('tab', { name: /chat/i }))
    expect(screen.getByRole('tabpanel')).toHaveTextContent(
      /chat is not implemented yet/i,
    )
  })
})
