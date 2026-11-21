import { useId, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode } from 'react'
import { Link, type LinkProps } from 'react-router-dom'
import type { LinkStatus } from '../utils/format'

type Variant = 'primary' | 'secondary' | 'danger' | 'ghost'

const base =
  'inline-flex items-center justify-center gap-2 rounded-md font-medium whitespace-nowrap transition-colors disabled:cursor-not-allowed disabled:opacity-50'
const sizes = { md: 'h-10 px-4 text-sm', sm: 'h-8 px-3 text-sm' }
const variants: Record<Variant, string> = {
  primary: 'bg-accent text-accent-ink hover:opacity-90',
  secondary: 'border border-line bg-surface text-ink hover:border-muted',
  danger: 'bg-danger text-accent-ink hover:opacity-90',
  ghost: 'text-muted hover:text-ink',
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: keyof typeof sizes
  loading?: boolean
}

export function Button({ variant = 'primary', size = 'md', loading, disabled, className = '', children, ...rest }: ButtonProps) {
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={`${base} ${sizes[size]} ${variants[variant]} ${className}`}
    >
      {loading && <Spinner small />}
      {children}
    </button>
  )
}

export function ButtonLink({ variant = 'primary', size = 'md', className = '', ...rest }: LinkProps & { variant?: Variant; size?: keyof typeof sizes }) {
  return <Link {...rest} className={`${base} ${sizes[size]} ${variants[variant]} ${className}`} />
}

interface FieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> {
  label: string
  error?: string | null
  hint?: string
  mono?: boolean
}

export function Field({ label, error, hint, mono, className = '', ...input }: FieldProps) {
  const id = useId()
  const describedBy = [error ? `${id}-err` : null, hint ? `${id}-hint` : null].filter(Boolean).join(' ') || undefined
  return (
    <div className={className}>
      <label htmlFor={id} className="mb-1.5 block text-sm font-medium">
        {label}
      </label>
      <input
        {...input}
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        className={`h-10 w-full rounded-md border bg-surface px-3 text-sm text-ink placeholder:text-muted/70 ${mono ? 'font-mono' : ''} ${
          error ? 'border-danger' : 'border-line hover:border-muted'
        }`}
      />
      {hint && !error && (
        <p id={`${id}-hint`} className="mt-1.5 text-xs text-muted">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-err`} role="alert" className="mt-1.5 text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  )
}

export function Spinner({ small }: { small?: boolean }) {
  const size = small ? 'h-3.5 w-3.5' : 'h-5 w-5'
  return (
    <span
      role="status"
      aria-label="Loading"
      className={`inline-block ${size} animate-spin rounded-full border-2 border-current border-t-transparent opacity-70`}
    />
  )
}

export function PageLoading({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 py-16 text-muted">
      <Spinner />
      <span>{label}…</span>
    </div>
  )
}

export function Notice({ tone = 'error', children }: { tone?: 'error' | 'info' | 'ok'; children: ReactNode }) {
  const tones = {
    error: 'bg-danger-soft text-danger',
    info: 'bg-accent-soft text-ink',
    ok: 'bg-ok-soft text-ok',
  }
  return (
    <div role={tone === 'error' ? 'alert' : 'status'} className={`rounded-md px-3 py-2.5 text-sm ${tones[tone]}`}>
      {children}
    </div>
  )
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="py-12">
      <Notice>{message}</Notice>
      {onRetry && (
        <Button variant="secondary" size="sm" className="mt-3" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  )
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="border-y border-line py-12">
      <h3 className="text-lg font-semibold">{title}</h3>
      {children && <p className="mt-1 max-w-prose text-muted">{children}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

const statusStyles: Record<LinkStatus, string> = {
  active: 'bg-ok-soft text-ok',
  disabled: 'bg-warn-soft text-amber',
  expired: 'bg-danger-soft text-danger',
}
const statusLabels: Record<LinkStatus, string> = { active: 'Active', disabled: 'Disabled', expired: 'Expired' }

export function StatusBadge({ status }: { status: LinkStatus }) {
  return <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${statusStyles[status]}`}>{statusLabels[status]}</span>
}

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <h1 className="text-3xl font-semibold">{title}</h1>
      {children}
    </div>
  )
}
