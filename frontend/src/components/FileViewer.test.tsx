import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { FileViewer } from './FileViewer'

describe('FileViewer — markdown', () => {
  it('renders a heading and body text from a markdown fixture', () => {
    render(<FileViewer format="markdown" content={'# Title\n\nbody text'} />)

    expect(screen.getByRole('heading', { name: 'Title' })).toBeInTheDocument()
    expect(screen.getByText('body text')).toBeInTheDocument()
  })

  it('renders raw HTML in the markdown source as escaped, inert text', () => {
    render(
      <FileViewer format="markdown" content={'<script>alert(1)</script>'} />,
    )

    expect(document.querySelector('script')).not.toBeInTheDocument()
    expect(screen.getByText('<script>alert(1)</script>')).toBeInTheDocument()
  })

  it('shows the empty state when content is undefined', () => {
    render(<FileViewer format="markdown" content={undefined} />)

    expect(screen.getByText('No content available.')).toBeInTheDocument()
  })

  it('shows the empty state when content is null', () => {
    render(<FileViewer format="markdown" content={null} />)

    expect(screen.getByText('No content available.')).toBeInTheDocument()
  })

  it('shows the empty state when content is whitespace-only', () => {
    render(<FileViewer format="markdown" content={'   \n  '} />)

    expect(screen.getByText('No content available.')).toBeInTheDocument()
  })
})

describe('FileViewer — json', () => {
  it('renders nested object keys and values', () => {
    render(
      <FileViewer
        format="json"
        content={{ importance: { age: 0.4, fare: 0.6 } }}
      />,
    )

    expect(screen.getByText('importance')).toBeInTheDocument()
    expect(screen.getByText('age')).toBeInTheDocument()
    expect(screen.getByText('0.4')).toBeInTheDocument()
    expect(screen.getByText('fare')).toBeInTheDocument()
    expect(screen.getByText('0.6')).toBeInTheDocument()
  })

  it('renders each element of an array fixture', () => {
    render(<FileViewer format="json" content={['alpha', 'beta', 'gamma']} />)

    expect(screen.getByText('alpha')).toBeInTheDocument()
    expect(screen.getByText('beta')).toBeInTheDocument()
    expect(screen.getByText('gamma')).toBeInTheDocument()
  })

  it('renders a literal "null" for a null leaf value', () => {
    render(<FileViewer format="json" content={{ score: null }} />)

    expect(screen.getByText('null')).toBeInTheDocument()
  })

  it('shows the empty state when content is undefined', () => {
    render(<FileViewer format="json" content={undefined} />)

    expect(screen.getByText('No content available.')).toBeInTheDocument()
  })
})
