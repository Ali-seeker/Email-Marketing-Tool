import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StatusChip } from '@/design/StatusChip'

describe('StatusChip', () => {
  it('renders status text and icon for sent status', () => {
    render(<StatusChip status="sent" />)
    expect(screen.getByText('sent')).toBeInTheDocument()
  })

  it('renders status text and icon for bounced status', () => {
    render(<StatusChip status="bounced" />)
    expect(screen.getByText('bounced')).toBeInTheDocument()
  })

  it('renders custom label when provided', () => {
    render(<StatusChip status="interested" label="Positive Interest" />)
    expect(screen.getByText('Positive Interest')).toBeInTheDocument()
  })

  it('falls back gracefully to neutral chip for unknown status', () => {
    render(<StatusChip status="unknown_status_code" />)
    expect(screen.getByText('unknown status code')).toBeInTheDocument()
  })
})
