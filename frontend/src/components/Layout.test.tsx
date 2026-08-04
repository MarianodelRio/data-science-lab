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

  it('moves focus and selection with ArrowRight/ArrowLeft, wrapping at the ends', async () => {
    const user = userEvent.setup()
    render(<Layout />)

    const pipelineTab = screen.getByRole('tab', { name: /pipeline/i })
    const experimentsTab = screen.getByRole('tab', { name: /experiments/i })
    const chatTab = screen.getByRole('tab', { name: /chat/i })

    pipelineTab.focus()
    await user.keyboard('{ArrowRight}')

    expect(experimentsTab).toHaveFocus()
    expect(experimentsTab).toHaveAttribute('aria-selected', 'true')
    expect(experimentsTab).toHaveAttribute('tabindex', '0')
    expect(pipelineTab).toHaveAttribute('aria-selected', 'false')
    expect(pipelineTab).toHaveAttribute('tabindex', '-1')
    expect(screen.getByRole('tabpanel')).toHaveTextContent(
      /experiments table is not implemented yet/i,
    )

    await user.keyboard('{ArrowLeft}')
    expect(pipelineTab).toHaveFocus()
    expect(pipelineTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toHaveTextContent(
      /pipeline view is not implemented yet/i,
    )

    // Wraps backward from the first tab to the last.
    await user.keyboard('{ArrowLeft}')
    expect(chatTab).toHaveFocus()
    expect(chatTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toHaveTextContent(
      /chat is not implemented yet/i,
    )

    // Wraps forward from the last tab back to the first.
    await user.keyboard('{ArrowRight}')
    expect(pipelineTab).toHaveFocus()
    expect(pipelineTab).toHaveAttribute('aria-selected', 'true')
  })

  it('jumps to the first/last tab with Home/End', async () => {
    const user = userEvent.setup()
    render(<Layout />)

    const pipelineTab = screen.getByRole('tab', { name: /pipeline/i })
    const chatTab = screen.getByRole('tab', { name: /chat/i })

    pipelineTab.focus()
    await user.keyboard('{End}')
    expect(chatTab).toHaveFocus()
    expect(chatTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toHaveTextContent(
      /chat is not implemented yet/i,
    )

    await user.keyboard('{Home}')
    expect(pipelineTab).toHaveFocus()
    expect(pipelineTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toHaveTextContent(
      /pipeline view is not implemented yet/i,
    )
  })
})
