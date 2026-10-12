import React, { useRef } from 'react'
import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  useReactTable,
  type SortingState,
  getSortedRowModel,
  type RowSelectionState,
  type VisibilityState,
} from '@tanstack/react-table'
import { useVirtualizer } from '@tanstack/react-virtual'
import {
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  Columns3,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react'
import { cn } from '@/lib/utils'

interface DataTableProps<TData, TValue> {
  columns: ColumnDef<TData, TValue>[]
  data: TData[]
  totalRows?: number
  page?: number
  pageSize?: number
  onPageChange?: (newPage: number) => void
  onRowClick?: (row: TData) => void
  onSelectionChange?: (selectedRows: TData[]) => void
  enableSelection?: boolean
  virtualized?: boolean
  isLoading?: boolean
  className?: string
}

export function DataTable<TData, TValue>({
  columns,
  data,
  totalRows,
  page = 1,
  pageSize = 50,
  onPageChange,
  onRowClick,
  onSelectionChange,
  enableSelection = false,
  virtualized = false,
  isLoading = false,
  className,
}: DataTableProps<TData, TValue>) {
  const [sorting, setSorting] = React.useState<SortingState>([])
  const [rowSelection, setRowSelection] = React.useState<RowSelectionState>({})
  const [columnVisibility, setColumnVisibility] = React.useState<VisibilityState>({})
  const [isColumnDropdownOpen, setIsColumnDropdownOpen] = React.useState(false)

  // Prepend selection checkbox column if enabled
  const finalColumns = React.useMemo<ColumnDef<TData, TValue>[]>(() => {
    if (!enableSelection) return columns

    const selectCol: ColumnDef<TData, TValue> = {
      id: 'select',
      header: ({ table }) => (
        <input
          type="checkbox"
          className="rounded border-border text-primary focus:ring-primary w-4 h-4 cursor-pointer"
          checked={table.getIsAllPageRowsSelected()}
          onChange={(e) => table.toggleAllPageRowsSelected(e.target.checked)}
          aria-label="Select all rows"
        />
      ),
      cell: ({ row }) => (
        <input
          type="checkbox"
          className="rounded border-border text-primary focus:ring-primary w-4 h-4 cursor-pointer"
          checked={row.getIsSelected()}
          onChange={(e) => {
            e.stopPropagation()
            row.toggleSelected(e.target.checked)
          }}
          aria-label="Select row"
        />
      ),
      size: 40,
      enableSorting: false,
    }

    return [selectCol, ...columns]
  }, [columns, enableSelection])

  const table = useReactTable({
    data,
    columns: finalColumns,
    state: {
      sorting,
      rowSelection,
      columnVisibility,
    },
    enableRowSelection: true,
    onRowSelectionChange: (updater) => {
      setRowSelection(updater)
      if (onSelectionChange) {
        setTimeout(() => {
          const selected = table.getSelectedRowModel().rows.map((r) => r.original)
          onSelectionChange(selected)
        }, 0)
      }
    },
    onSortingChange: setSorting,
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })

  const rows = table.getRowModel().rows
  const tableContainerRef = useRef<HTMLDivElement>(null)

  // Virtualizer for 5,000+ rows
  const rowVirtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => tableContainerRef.current,
    estimateSize: () => 40, // 40px compact row height
    overscan: 10,
    enabled: virtualized && rows.length > 50,
  })

  const totalPages = totalRows ? Math.ceil(totalRows / pageSize) : 1

  return (
    <div className={cn('flex flex-col border border-border rounded-lg bg-card overflow-hidden', className)}>
      {/* Top Action Toolbar */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border bg-card/60 text-xs">
        <div className="flex items-center gap-2 text-muted-foreground tabular-nums">
          {enableSelection && Object.keys(rowSelection).length > 0 ? (
            <span className="font-medium text-foreground">
              {Object.keys(rowSelection).length} of {rows.length} row(s) selected
            </span>
          ) : (
            <span>
              Showing {rows.length} {totalRows ? `of ${totalRows.toLocaleString()}` : ''} items
            </span>
          )}
        </div>

        {/* Column Visibility Menu */}
        <div className="relative">
          <button
            type="button"
            onClick={() => setIsColumnDropdownOpen((prev) => !prev)}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs border border-border rounded-md hover:bg-muted transition-colors font-medium text-muted-foreground hover:text-foreground"
          >
            <Columns3 className="w-3.5 h-3.5" aria-hidden="true" />
            Columns
          </button>

          {isColumnDropdownOpen && (
            <div className="absolute right-0 mt-1 w-44 bg-popover border border-border rounded-md shadow-md py-1 z-30">
              <div className="px-2 py-1 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                Toggle Columns
              </div>
              {table
                .getAllLeafColumns()
                .filter((col) => col.id !== 'select')
                .map((col) => {
                  return (
                    <label
                      key={col.id}
                      className="flex items-center gap-2 px-2.5 py-1 text-xs hover:bg-muted cursor-pointer capitalize"
                    >
                      <input
                        type="checkbox"
                        checked={col.getIsVisible()}
                        onChange={col.getToggleVisibilityHandler()}
                        className="rounded border-border text-primary w-3.5 h-3.5"
                      />
                      <span>{col.id.replace(/_/g, ' ')}</span>
                    </label>
                  )
                })}
            </div>
          )}
        </div>
      </div>

      {/* Table Container */}
      <div
        ref={tableContainerRef}
        className={cn(
          'overflow-auto relative',
          virtualized ? 'max-h-[600px]' : ''
        )}
      >
        <table className="w-full text-left text-xs border-collapse">
          <thead className="bg-muted/70 text-muted-foreground sticky top-0 z-10 border-b border-border backdrop-blur-xs">
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id} className="h-9">
                {headerGroup.headers.map((header) => {
                  const canSort = header.column.getCanSort()
                  const sortDir = header.column.getIsSorted()

                  return (
                    <th
                      key={header.id}
                      style={{ width: header.getSize() !== 150 ? header.getSize() : undefined }}
                      className={cn(
                        'px-3 font-semibold select-none',
                        canSort && 'cursor-pointer hover:text-foreground'
                      )}
                      onClick={header.column.getToggleSortingHandler()}
                    >
                      <div className="flex items-center gap-1.5">
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {canSort && (
                          <span className="shrink-0 text-muted-foreground">
                            {sortDir === 'asc' ? (
                              <ArrowUp className="w-3 h-3 text-primary" />
                            ) : sortDir === 'desc' ? (
                              <ArrowDown className="w-3 h-3 text-primary" />
                            ) : (
                              <ArrowUpDown className="w-3 h-3 opacity-40 hover:opacity-100" />
                            )}
                          </span>
                        )}
                      </div>
                    </th>
                  )
                })}
              </tr>
            ))}
          </thead>

          <tbody className="divide-y divide-border/60">
            {isLoading ? (
              Array.from({ length: 6 }).map((_, idx) => (
                <tr key={idx} className="h-10 animate-pulse bg-muted/20">
                  <td colSpan={finalColumns.length} className="px-3" />
                </tr>
              ))
            ) : rows.length === 0 ? (
              <tr>
                <td
                  colSpan={finalColumns.length}
                  className="h-28 text-center text-muted-foreground text-xs"
                >
                  No matching records found.
                </td>
              </tr>
            ) : virtualized && rows.length > 50 ? (
              // Virtualized rendering
              <tr style={{ height: `${rowVirtualizer.getTotalSize()}px` }}>
                <td colSpan={finalColumns.length} className="p-0 border-0">
                  <div className="relative w-full" style={{ height: `${rowVirtualizer.getTotalSize()}px` }}>
                    {rowVirtualizer.getVirtualItems().map((virtualRow) => {
                      const row = rows[virtualRow.index]
                      const isSelected = row.getIsSelected()

                      return (
                        <div
                          key={row.id}
                          onClick={() => onRowClick?.(row.original)}
                          style={{
                            position: 'absolute',
                            top: 0,
                            left: 0,
                            width: '100%',
                            height: `${virtualRow.size}px`,
                            transform: `translateY(${virtualRow.start}px)`,
                          }}
                          className={cn(
                            'flex items-center border-b border-border/60 transition-colors',
                            isSelected ? 'bg-primary/5' : 'hover:bg-muted/40',
                            onRowClick && 'cursor-pointer'
                          )}
                        >
                          {row.getVisibleCells().map((cell) => (
                            <div
                              key={cell.id}
                              style={{ width: cell.column.getSize() !== 150 ? cell.column.getSize() : undefined }}
                              className="px-3 truncate text-xs flex-1"
                            >
                              {flexRender(cell.column.columnDef.cell, cell.getContext())}
                            </div>
                          ))}
                        </div>
                      )
                    })}
                  </div>
                </td>
              </tr>
            ) : (
              // Standard rendering (dense 40px rows)
              rows.map((row) => {
                const isSelected = row.getIsSelected()
                return (
                  <tr
                    key={row.id}
                    onClick={() => onRowClick?.(row.original)}
                    className={cn(
                      'h-10 transition-colors',
                      isSelected ? 'bg-primary/5' : 'hover:bg-muted/40',
                      onRowClick && 'cursor-pointer'
                    )}
                  >
                    {row.getVisibleCells().map((cell) => (
                      <td
                        key={cell.id}
                        style={{ width: cell.column.getSize() !== 150 ? cell.column.getSize() : undefined }}
                        className="px-3 truncate"
                      >
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination Footer */}
      {totalPages > 1 && onPageChange && (
        <div className="flex items-center justify-between px-3 py-2 border-t border-border bg-card/60 text-xs">
          <span className="text-muted-foreground tabular-nums">
            Page {page} of {totalPages}
          </span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => onPageChange(page - 1)}
              disabled={page <= 1}
              className="p-1 rounded-md border border-border hover:bg-muted disabled:opacity-40 disabled:pointer-events-none transition-colors"
              aria-label="Previous page"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <button
              type="button"
              onClick={() => onPageChange(page + 1)}
              disabled={page >= totalPages}
              className="p-1 rounded-md border border-border hover:bg-muted disabled:opacity-40 disabled:pointer-events-none transition-colors"
              aria-label="Next page"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
