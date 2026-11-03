'use client'

import React, { type ReactNode, useId } from 'react'
import {
  ArrowUpDown,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  ChevronUp,
  Inbox,
  RotateCcw,
  Search,
  TriangleAlert,
  X,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { Skeleton } from '@/components/ui/skeleton'

// -----------------------------------------------------------------------------
// Types & Interfaces
// -----------------------------------------------------------------------------

export type SortDirection = 'asc' | 'desc'

export interface ColumnSortState {
  key: string
  direction: SortDirection
}

export interface DataTableColumn<TData> {
  /** Unique identifier for the column */
  id: string
  /** Column header label or custom renderer */
  header: ReactNode | ((context: { column: DataTableColumn<TData>; sort?: ColumnSortState | null }) => ReactNode)
  /** Key of TData to read default value from */
  accessorKey?: keyof TData & string
  /** Custom cell renderer */
  cell?: (context: { row: TData; index: number; value: unknown }) => ReactNode
  /** Enable column sorting */
  sortable?: boolean
  /** Sort key identifier passed to onSortChange (defaults to id or accessorKey) */
  sortKey?: string
  /** Alignment of column text and headers */
  align?: 'left' | 'center' | 'right'
  /** Additional cell className */
  className?: string
  /** Additional header cell className */
  headerClassName?: string
  /** Responsive visibility: hide on small mobile screens */
  hideOnMobile?: boolean
  /** Min-width or fixed width constraint (e.g. 'w-32', 'min-w-[120px]') */
  width?: string
}

export interface TableSearchConfig {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  disabled?: boolean
  ariaLabel?: string
}

export interface TablePaginationConfig {
  page: number // 1-based index
  pageSize: number
  totalCount: number
  pageSizeOptions?: number[]
  onPageChange: (page: number) => void
  onPageSizeChange?: (pageSize: number) => void
  disabled?: boolean
}

export interface TableEmptyStateConfig {
  icon?: React.ElementType
  title?: ReactNode
  description?: ReactNode
  action?: ReactNode
}

export interface TableErrorStateConfig {
  title?: ReactNode
  description?: ReactNode
  onRetry?: () => void
}

export interface AdminDataTableProps<TData> {
  /** Data rows to display */
  data: TData[]
  /** Column definitions */
  columns: DataTableColumn<TData>[]
  /** Unique key extractor for each row */
  getRowId?: (row: TData, index: number) => string | number

  /** Loading state flag */
  isLoading?: boolean
  /** Number of skeleton rows to display when isLoading is true (defaults to 5) */
  skeletonRowCount?: number

  /** Optional error state */
  error?: string | Error | null
  /** Callback to retry loading on error */
  onRetry?: () => void

  /** Table header title or summary count */
  title?: ReactNode
  /** Subtitle or operational note */
  subtitle?: ReactNode
  /** Header icon */
  icon?: React.ElementType

  /** Search configuration (controlled) */
  search?: TableSearchConfig

  /** Filter controls slot rendered in toolbar */
  filters?: ReactNode
  /** Action buttons slot rendered on right of toolbar */
  actions?: ReactNode

  /** Current active sort state */
  sort?: ColumnSortState | null
  /** Callback triggered when a sortable column header is clicked */
  onSortChange?: (sort: ColumnSortState | null) => void

  /** Server-side or controlled pagination configuration */
  pagination?: TablePaginationConfig

  /** Optional callback when a row is clicked */
  onRowClick?: (row: TData, index: number) => void
  /** ID of currently selected row */
  selectedRowId?: string | number | null

  /** Custom empty state when data array is empty */
  emptyState?: TableEmptyStateConfig

  /** Styling options */
  className?: string
  /** Minimum width for the inner table container (defaults to 'min-w-full') */
  minWidth?: string
  /** Render compact row padding */
  compact?: boolean
}

// -----------------------------------------------------------------------------
// Sub-components: Search Input
// -----------------------------------------------------------------------------

export function DataTableSearch({
  value,
  onChange,
  placeholder = 'Search records…',
  disabled = false,
  ariaLabel = 'Search table records',
}: TableSearchConfig) {
  const inputId = useId()

  return (
    <div className="relative w-full sm:w-auto sm:max-w-xs min-w-[180px]">
      <label htmlFor={inputId} className="sr-only">
        {ariaLabel}
      </label>
      <Search
        className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
        aria-hidden="true"
      />
      <input
        id={inputId}
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        className={cn(
          'h-9 w-full rounded-xl border border-border bg-muted/60 pl-9 pr-8 text-xs text-foreground placeholder:text-muted-foreground',
          'transition-colors outline-none focus:border-ring focus:bg-background focus:ring-1 focus:ring-ring',
          'disabled:cursor-not-allowed disabled:opacity-50',
        )}
      />
      {value && !disabled && (
        <button
          type="button"
          onClick={() => onChange('')}
          aria-label="Clear search"
          className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded-md p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
        >
          <X className="size-3.5" aria-hidden="true" />
        </button>
      )}
    </div>
  )
}

// -----------------------------------------------------------------------------
// Sub-components: Pagination Bar
// -----------------------------------------------------------------------------

export function DataTablePagination({
  page,
  pageSize,
  totalCount,
  pageSizeOptions = [10, 25, 50, 100],
  onPageChange,
  onPageSizeChange,
  disabled = false,
}: TablePaginationConfig) {
  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize))
  const startItem = totalCount === 0 ? 0 : (page - 1) * pageSize + 1
  const endItem = totalCount === 0 ? 0 : Math.min(totalCount, page * pageSize)

  const canPrev = page > 1 && !disabled
  const canNext = page < totalPages && !disabled

  return (
    <div className="flex flex-col gap-3 border-t border-border px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
      {/* Range summary & page size selector */}
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        <span>
          Showing <strong className="font-semibold text-foreground">{startItem}</strong>–
          <strong className="font-semibold text-foreground">{endItem}</strong> of{' '}
          <strong className="font-semibold text-foreground">{totalCount}</strong> items
        </span>

        {onPageSizeChange && (
          <div className="flex items-center gap-1.5 pl-2 border-l border-border">
            <span>Rows:</span>
            <select
              value={pageSize}
              onChange={(e) => {
                const nextSize = Number(e.target.value)
                onPageSizeChange(nextSize)
                onPageChange(1)
              }}
              disabled={disabled}
              aria-label="Rows per page"
              className={cn(
                'rounded-lg border border-border bg-muted/60 px-2 py-1 text-xs font-medium text-foreground',
                'transition-colors outline-none focus:border-ring focus:bg-background focus:ring-1 focus:ring-ring',
                'disabled:cursor-not-allowed disabled:opacity-50 cursor-pointer',
              )}
            >
              {pageSizeOptions.map((opt) => (
                <option key={opt} value={opt}>
                  {opt}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {/* Page controls */}
      <div className="flex items-center justify-between sm:justify-end gap-2">
        <span className="text-xs text-muted-foreground mr-1">
          Page <strong className="font-semibold text-foreground">{page}</strong> of{' '}
          <strong className="font-semibold text-foreground">{totalPages}</strong>
        </span>

        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => onPageChange(1)}
            disabled={!canPrev}
            aria-label="Go to first page"
            title="First page"
            className={cn(
              'grid size-8 place-items-center rounded-lg border border-border bg-card text-foreground transition-colors',
              'hover:bg-muted focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring',
              'disabled:pointer-events-none disabled:opacity-40',
            )}
          >
            <ChevronsLeft className="size-4" aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={() => onPageChange(page - 1)}
            disabled={!canPrev}
            aria-label="Go to previous page"
            title="Previous page"
            className={cn(
              'grid size-8 place-items-center rounded-lg border border-border bg-card text-foreground transition-colors',
              'hover:bg-muted focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring',
              'disabled:pointer-events-none disabled:opacity-40',
            )}
          >
            <ChevronLeft className="size-4" aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={() => onPageChange(page + 1)}
            disabled={!canNext}
            aria-label="Go to next page"
            title="Next page"
            className={cn(
              'grid size-8 place-items-center rounded-lg border border-border bg-card text-foreground transition-colors',
              'hover:bg-muted focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring',
              'disabled:pointer-events-none disabled:opacity-40',
            )}
          >
            <ChevronRight className="size-4" aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={() => onPageChange(totalPages)}
            disabled={!canNext}
            aria-label="Go to last page"
            title="Last page"
            className={cn(
              'grid size-8 place-items-center rounded-lg border border-border bg-card text-foreground transition-colors',
              'hover:bg-muted focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring',
              'disabled:pointer-events-none disabled:opacity-40',
            )}
          >
            <ChevronsRight className="size-4" aria-hidden="true" />
          </button>
        </div>
      </div>
    </div>
  )
}

// -----------------------------------------------------------------------------
// Sub-components: Empty State
// -----------------------------------------------------------------------------

export function DataTableEmpty({
  icon: Icon = Inbox,
  title = 'No records found',
  description = 'There are no items matching the current view criteria.',
  action,
}: TableEmptyStateConfig) {
  return (
    <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
      <div className="grid size-12 place-items-center rounded-2xl border border-border bg-muted/40 mb-3 text-muted-foreground">
        <Icon className="size-6 stroke-[1.5]" aria-hidden="true" />
      </div>
      <h4 className="text-sm font-semibold text-foreground mb-1">{title}</h4>
      {description && <p className="text-xs text-muted-foreground max-w-sm mb-4 leading-relaxed">{description}</p>}
      {action && <div>{action}</div>}
    </div>
  )
}

// -----------------------------------------------------------------------------
// Sub-components: Error State
// -----------------------------------------------------------------------------

export function DataTableError({
  title = 'Unable to load table data',
  description,
  onRetry,
}: TableErrorStateConfig) {
  return (
    <div className="flex items-center justify-between gap-4 p-4 border-b border-border bg-rose-500/10 text-rose-700 dark:text-rose-400">
      <div className="flex items-center gap-2.5 min-w-0">
        <TriangleAlert className="size-4 shrink-0 text-rose-600 dark:text-rose-400" aria-hidden="true" />
        <div className="min-w-0">
          <p className="text-xs font-semibold">{title}</p>
          {description && <p className="text-xs opacity-90 truncate">{description}</p>}
        </div>
      </div>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="flex shrink-0 items-center gap-1.5 rounded-lg border border-rose-500/30 bg-rose-500/20 px-2.5 py-1 text-xs font-medium text-rose-700 dark:text-rose-300 hover:bg-rose-500/30 focus:outline-none focus:ring-1 focus:ring-rose-500"
        >
          <RotateCcw className="size-3" aria-hidden="true" />
          Retry
        </button>
      )}
    </div>
  )
}

// -----------------------------------------------------------------------------
// Sub-components: Skeleton Row
// -----------------------------------------------------------------------------

const SKELETON_WIDTHS = ['w-3/4', 'w-1/2', 'w-2/3', 'w-4/5', 'w-1/3', 'w-3/5']

function DataTableSkeletonRow<TData>({
  columns,
  rowIndex,
  compact,
}: {
  columns: DataTableColumn<TData>[]
  rowIndex: number
  compact?: boolean
}) {
  return (
    <tr className="border-b border-border">
      {columns.map((col, colIndex) => {
        const widthClass = SKELETON_WIDTHS[(rowIndex + colIndex) % SKELETON_WIDTHS.length]
        return (
          <td
            key={col.id}
            className={cn(
              compact ? 'px-2.5 py-2 sm:px-3 sm:py-2.5' : 'p-3 sm:p-4',
              col.hideOnMobile && 'hidden sm:table-cell',
              col.className,
            )}
          >
            <Skeleton
              className={cn(
                'h-4 rounded-md',
                widthClass,
                col.align === 'right' && 'ml-auto',
                col.align === 'center' && 'mx-auto',
              )}
            />
          </td>
        )
      })}
    </tr>
  )
}

// -----------------------------------------------------------------------------
// Main Component: AdminDataTable
// -----------------------------------------------------------------------------

export function AdminDataTable<TData>({
  data,
  columns,
  getRowId,
  isLoading = false,
  skeletonRowCount = 5,
  error = null,
  onRetry,
  title,
  subtitle,
  icon: HeaderIcon,
  search,
  filters,
  actions,
  sort,
  onSortChange,
  pagination,
  onRowClick,
  selectedRowId,
  emptyState,
  className,
  minWidth = 'min-w-full',
  compact = false,
}: AdminDataTableProps<TData>) {
  const hasToolbar = Boolean(title || search || filters || actions)

  const handleHeaderSort = (column: DataTableColumn<TData>) => {
    if (!column.sortable || !onSortChange) return

    const key = column.sortKey || column.accessorKey || column.id
    if (!sort || sort.key !== key) {
      onSortChange({ key, direction: 'asc' })
    } else if (sort.direction === 'asc') {
      onSortChange({ key, direction: 'desc' })
    } else {
      // Toggle back to neutral unsorted
      onSortChange(null)
    }
  }

  const errorMessage = error instanceof Error ? error.message : error

  return (
    <div className={cn('space-y-3', className)}>
      {/* Optional Top Toolbar */}
      {hasToolbar && (
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          {/* Left section: Title or Search & Filters */}
          <div className="flex flex-1 flex-col sm:flex-row sm:flex-wrap sm:items-center gap-2.5 sm:gap-3">
            {title && (
              <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                {HeaderIcon && <HeaderIcon className="size-4 text-foreground shrink-0" aria-hidden="true" />}
                <div>
                  <div className="flex items-center gap-2">
                    {title}
                  </div>
                  {subtitle && <p className="text-xs font-normal text-muted-foreground">{subtitle}</p>}
                </div>
              </div>
            )}
            {search && <DataTableSearch {...search} />}
            {filters && <div className="flex flex-wrap items-center gap-2">{filters}</div>}
          </div>

          {/* Right section: Actions */}
          {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
        </div>
      )}

      {/* Main Table Card */}
      <section className="overflow-hidden rounded-2xl border border-border bg-card shadow-xs">
        {/* Error Notification Banner */}
        {errorMessage && (
          <DataTableError description={errorMessage} onRetry={onRetry} />
        )}

        {/* Scrollable Table Area */}
        <div className="overflow-x-auto">
          <table className={cn('w-full text-sm', minWidth)}>
            <thead className="border-b border-border bg-muted/60 text-xs uppercase tracking-wider text-muted-foreground">
              <tr>
                {columns.map((column) => {
                  const sortKey = column.sortKey || column.accessorKey || column.id
                  const isSorted = sort?.key === sortKey
                  const sortDir = isSorted ? sort.direction : null

                  return (
                    <th
                      key={column.id}
                      scope="col"
                      aria-sort={
                        column.sortable
                          ? isSorted
                            ? sortDir === 'asc'
                              ? 'ascending'
                              : 'descending'
                            : 'none'
                          : undefined
                      }
                      className={cn(
                        'text-left font-medium',
                        compact ? 'px-2.5 py-2 sm:px-3 sm:py-2.5' : 'p-3 sm:p-4',
                        column.align === 'right' && 'text-right',
                        column.align === 'center' && 'text-center',
                        column.hideOnMobile && 'hidden sm:table-cell',
                        column.width,
                        column.headerClassName,
                      )}
                    >
                      {column.sortable ? (
                        <button
                          type="button"
                          onClick={() => handleHeaderSort(column)}
                          className={cn(
                            'group inline-flex items-center gap-1.5 font-medium transition-colors hover:text-foreground focus:outline-none focus-visible:ring-1 focus-visible:ring-ring rounded-sm',
                            isSorted && 'text-foreground font-semibold',
                            column.align === 'right' && 'ml-auto flex-row-reverse',
                            column.align === 'center' && 'mx-auto',
                          )}
                        >
                          <span>
                            {typeof column.header === 'function'
                              ? column.header({ column, sort })
                              : column.header}
                          </span>
                          <span className="shrink-0">
                            {isSorted ? (
                              sortDir === 'asc' ? (
                                <ChevronUp className="size-3.5 text-foreground" aria-hidden="true" />
                              ) : (
                                <ChevronDown className="size-3.5 text-foreground" aria-hidden="true" />
                              )
                            ) : (
                              <ArrowUpDown
                                className="size-3 text-muted-foreground/50 opacity-60 group-hover:opacity-100 transition-opacity"
                                aria-hidden="true"
                              />
                            )}
                          </span>
                        </button>
                      ) : typeof column.header === 'function' ? (
                        column.header({ column, sort })
                      ) : (
                        column.header
                      )}
                    </th>
                  )
                })}
              </tr>
            </thead>

            <tbody className="divide-y divide-border">
              {isLoading ? (
                Array.from({ length: skeletonRowCount }).map((_, rIdx) => (
                  <DataTableSkeletonRow
                    key={`skeleton-${rIdx}`}
                    columns={columns}
                    rowIndex={rIdx}
                    compact={compact}
                  />
                ))
              ) : data.length === 0 ? (
                <tr>
                  <td colSpan={columns.length} className="p-0">
                    <DataTableEmpty
                      icon={emptyState?.icon}
                      title={
                        emptyState?.title ??
                        (search?.value ? 'No matching records' : 'No records found')
                      }
                      description={
                        emptyState?.description ??
                        (search?.value
                          ? `No items found matching "${search.value}". Check your search term or reset filters.`
                          : 'There are no items recorded in this section yet.')
                      }
                      action={
                        emptyState?.action ??
                        (search?.value ? (
                          <button
                            type="button"
                            onClick={() => search.onChange('')}
                            className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-accent px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted focus:outline-none focus:ring-1 focus:ring-ring"
                          >
                            <X className="size-3" aria-hidden="true" />
                            Clear search
                          </button>
                        ) : null)
                      }
                    />
                  </td>
                </tr>
              ) : (
                data.map((row, index) => {
                  const rowId = getRowId ? getRowId(row, index) : index
                  const isSelected = selectedRowId !== undefined && selectedRowId !== null && selectedRowId === rowId
                  const isClickable = Boolean(onRowClick)

                  return (
                    <tr
                      key={rowId}
                      tabIndex={isClickable ? 0 : undefined}
                      role={isClickable ? 'button' : undefined}
                      onClick={() => onRowClick?.(row, index)}
                      onKeyDown={(e) => {
                        if (isClickable && (e.key === 'Enter' || e.key === ' ')) {
                          e.preventDefault()
                          onRowClick?.(row, index)
                        }
                      }}
                      className={cn(
                        'transition-colors',
                        isClickable && 'cursor-pointer hover:bg-muted/60 focus:bg-muted/60 focus:outline-none',
                        !isClickable && 'hover:bg-muted/40',
                        isSelected && 'bg-accent/40 hover:bg-accent/60',
                      )}
                    >
                      {columns.map((column) => {
                        const cellValue = column.accessorKey ? (row as Record<string, unknown>)[column.accessorKey] : undefined

                        return (
                          <td
                            key={column.id}
                            className={cn(
                              compact ? 'px-2.5 py-2 sm:px-3 sm:py-2.5' : 'p-3 sm:p-4',
                              column.align === 'right' && 'text-right',
                              column.align === 'center' && 'text-center',
                              column.hideOnMobile && 'hidden sm:table-cell',
                              column.className,
                            )}
                          >
                            {column.cell
                              ? column.cell({ row, index, value: cellValue })
                              : (cellValue as ReactNode) ?? '—'}
                          </td>
                        )
                      })}
                    </tr>
                  )
                })
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination Bar */}
        {pagination && <DataTablePagination {...pagination} />}
      </section>
    </div>
  )
}
