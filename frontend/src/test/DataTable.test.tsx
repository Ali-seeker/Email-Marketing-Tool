import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { DataTable } from '@/design/DataTable'
import type { ColumnDef } from '@tanstack/react-table'

interface TestUser {
  id: number
  name: string
  email: string
}

const columns: ColumnDef<TestUser>[] = [
  {
    accessorKey: 'name',
    header: 'Name',
  },
  {
    accessorKey: 'email',
    header: 'Email',
  },
]

const testData: TestUser[] = [
  { id: 1, name: 'Alice Smith', email: 'alice@example.com' },
  { id: 2, name: 'Bob Jones', email: 'bob@example.com' },
]

describe('DataTable', () => {
  it('renders table headers and rows', () => {
    render(<DataTable columns={columns} data={testData} />)
    expect(screen.getByText('Name')).toBeInTheDocument()
    expect(screen.getByText('Email')).toBeInTheDocument()
    expect(screen.getByText('Alice Smith')).toBeInTheDocument()
    expect(screen.getByText('bob@example.com')).toBeInTheDocument()
  })

  it('triggers onRowClick when row is clicked', () => {
    const handleRowClick = vi.fn()
    render(<DataTable columns={columns} data={testData} onRowClick={handleRowClick} />)

    const cell = screen.getByText('Alice Smith')
    fireEvent.click(cell)
    expect(handleRowClick).toHaveBeenCalledTimes(1)
    expect(handleRowClick).toHaveBeenCalledWith(testData[0])
  })

  it('renders empty message when no rows are available', () => {
    render(<DataTable columns={columns} data={[]} />)
    expect(screen.getByText('No matching records found.')).toBeInTheDocument()
  })
})
