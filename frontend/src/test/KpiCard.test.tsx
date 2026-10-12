import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { KpiCard } from '@/design/KpiCard'

describe('KpiCard', () => {
  it('renders label and value', () => {
    render(<KpiCard label="Sent Today" value="28 / 30" />)
    expect(screen.getByText('Sent Today')).toBeInTheDocument()
    expect(screen.getByText('28 / 30')).toBeInTheDocument()
  })

  it('renders positive delta correctly', () => {
    render(
      <KpiCard
        label="Reply Rate"
        value="12.4%"
        delta={{ value: '+2.1%', isPositive: true }}
      />
    )
    expect(screen.getByText('+2.1%')).toBeInTheDocument()
    expect(screen.getByText('vs prev period')).toBeInTheDocument()
  })

  it('renders progress bar when progressBar is provided', () => {
    render(
      <KpiCard
        label="Daily Quota"
        value="24"
        progressBar={{ current: 24, max: 30 }}
      />
    )
    const bar = screen.getByRole('progressbar')
    expect(bar).toBeInTheDocument()
    expect(bar).toHaveAttribute('aria-valuenow', '80')
  })

  it('renders sparkline SVG when sparklineData is provided', () => {
    const { container } = render(
      <KpiCard label="Activity" value="142" sparklineData={[10, 20, 15, 30, 25]} />
    )
    expect(container.querySelector('svg polyline')).toBeInTheDocument()
  })
})
