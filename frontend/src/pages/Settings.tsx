import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Field, Notice, PageHeader } from '../components/ui'
import { useAuth } from '../context/AuthContext'
import { useTheme, type ThemePreference } from '../context/ThemeContext'
import { authApi, describeError, toApiError } from '../services/api'
import { formatDate } from '../utils/format'

function ChangePassword() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [errors, setErrors] = useState<{ current?: string; next?: string }>({})
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setMessage(null)
    const found: { current?: string; next?: string } = {}
    if (!current) found.current = 'Enter your current password.'
    if (next.length < 8) found.next = 'Use at least 8 characters.'
    setErrors(found)
    if (Object.keys(found).length) return
    setBusy(true)
    try {
      await authApi.changePassword(current, next)
      setCurrent('')
      setNext('')
      setMessage({ tone: 'ok', text: 'Password updated.' })
    } catch (err) {
      const apiErr = toApiError(err)
      if (apiErr.code === 'invalid_credentials') setErrors({ current: 'That password is incorrect.' })
      else setMessage({ tone: 'error', text: describeError(apiErr) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={onSubmit} noValidate className="max-w-sm space-y-4">
      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      <Field label="Current password" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} error={errors.current} />
      <Field label="New password" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} error={errors.next} hint="At least 8 characters." />
      <Button type="submit" loading={busy} variant="secondary">
        Update password
      </Button>
    </form>
  )
}

function DeleteAccount() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function onDelete() {
    setBusy(true)
    setError(null)
    try {
      await authApi.deleteAccount()
      logout()
      navigate('/', { replace: true })
    } catch (err) {
      setError(describeError(err))
      setBusy(false)
    }
  }

  if (!open) {
    return (
      <Button variant="secondary" onClick={() => setOpen(true)}>
        Delete account
      </Button>
    )
  }
  return (
    <div className="max-w-sm space-y-4">
      <Notice>This permanently deletes your account, every link you created and all of their analytics. Short links will stop working immediately.</Notice>
      {error && <Notice>{error}</Notice>}
      <Field label={`Type ${user?.email} to confirm`} value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
      <div className="flex gap-2">
        <Button variant="danger" disabled={typed !== user?.email} loading={busy} onClick={() => void onDelete()}>
          Delete everything
        </Button>
        <Button variant="secondary" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </div>
  )
}

const THEMES: { value: ThemePreference; label: string }[] = [
  { value: 'system', label: 'System' },
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
]

export default function Settings() {
  const { user } = useAuth()
  const { preference, setPreference } = useTheme()
  return (
    <>
      <PageHeader title="Settings" />
      <div className="max-w-3xl divide-y divide-line">
        <section className="pb-10" aria-labelledby="h-account">
          <h2 id="h-account" className="text-lg font-semibold">
            Account
          </h2>
          <dl className="mt-4 grid max-w-sm grid-cols-[7rem_1fr] gap-y-2 text-sm">
            <dt className="text-muted">Email</dt>
            <dd className="break-all">{user?.email}</dd>
            <dt className="text-muted">Member since</dt>
            <dd>{user ? formatDate(user.created_at) : ''}</dd>
          </dl>
        </section>

        <section className="py-10" aria-labelledby="h-theme">
          <h2 id="h-theme" className="text-lg font-semibold">
            Appearance
          </h2>
          <div role="radiogroup" aria-label="Theme" className="mt-4 inline-flex overflow-hidden rounded-md border border-line">
            {THEMES.map((t) => (
              <button
                key={t.value}
                type="button"
                role="radio"
                aria-checked={preference === t.value}
                onClick={() => setPreference(t.value)}
                className={`h-9 px-4 text-sm font-medium ${preference === t.value ? 'bg-accent text-accent-ink' : 'bg-surface text-muted hover:text-ink'}`}
              >
                {t.label}
              </button>
            ))}
          </div>
        </section>

        <section className="py-10" aria-labelledby="h-password">
          <h2 id="h-password" className="mb-4 text-lg font-semibold">
            Password
          </h2>
          <ChangePassword />
        </section>

        <section className="pt-10" aria-labelledby="h-delete">
          <h2 id="h-delete" className="mb-4 text-lg font-semibold">
            Delete account
          </h2>
          <DeleteAccount />
        </section>
      </div>
    </>
  )
}
