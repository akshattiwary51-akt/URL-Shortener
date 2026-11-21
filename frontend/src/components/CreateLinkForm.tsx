import { useState, type FormEvent } from 'react'
import { useAuth } from '../context/AuthContext'
import { useCreateUrl } from '../hooks/useUrls'
import { describeError, toApiError } from '../services/api'
import type { ShortUrl } from '../types/api'
import { ALIAS_PATTERN, isHttpUrl, localInputMin, localInputToIso } from '../utils/format'
import { Button, Field, Notice } from './ui'
import { ShortLinkResult } from './ShortLinkResult'

interface FieldErrors {
  original_url?: string
  custom_alias?: string
  expires_at?: string
}

/** Client-side checks mirror the server rules so most mistakes are caught before a request. */
export function validateLinkForm(values: { url: string; alias: string; expires: string }, now: Date = new Date()): FieldErrors {
  const errors: FieldErrors = {}
  if (!values.url.trim()) errors.original_url = 'Enter the URL you want to shorten.'
  else if (!isHttpUrl(values.url)) errors.original_url = 'Enter a full web address starting with http:// or https://.'
  if (values.alias && !ALIAS_PATTERN.test(values.alias)) {
    errors.custom_alias = 'Use 3–32 letters, numbers, hyphens or underscores.'
  }
  if (values.expires && new Date(values.expires).getTime() <= now.getTime()) {
    errors.expires_at = 'Choose a date and time in the future.'
  }
  return errors
}

export function CreateLinkForm({ variant = 'panel', onCreated }: { variant?: 'hero' | 'panel'; onCreated?: (url: ShortUrl) => void }) {
  const { status } = useAuth()
  const create = useCreateUrl()
  const [url, setUrl] = useState('')
  const [alias, setAlias] = useState('')
  const [expires, setExpires] = useState('')
  const [showOptions, setShowOptions] = useState(false)
  const [errors, setErrors] = useState<FieldErrors>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [result, setResult] = useState<ShortUrl | null>(null)


  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const found = validateLinkForm({ url, alias, expires })
    setErrors(found)
    if (Object.keys(found).length) {
      if (found.custom_alias || found.expires_at) setShowOptions(true)
      return
    }
    try {
      const created = await create.mutateAsync({
        original_url: url.trim(),
        ...(alias ? { custom_alias: alias } : {}),
        ...(expires ? { expires_at: localInputToIso(expires) } : {}),
      })
      setResult(created)
      setUrl('')
      setAlias('')
      setExpires('')
      onCreated?.(created)
    } catch (err) {
      const apiErr = toApiError(err)
      if (apiErr.code === 'alias_conflict') {
        setShowOptions(true)
        setErrors({ custom_alias: 'That alias is already taken. Try another.' })
      } else if (Object.keys(apiErr.fieldErrors).length) {
        setShowOptions(true)
        setErrors(apiErr.fieldErrors as FieldErrors)
      } else if (apiErr.status === 401) {
        setFormError('Sign in to create short links.')
      } else {
        setFormError(describeError(apiErr))
      }
    }
  }

  return (
    <div>
      <form onSubmit={onSubmit} noValidate aria-label="Shorten a link">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
          <Field
            label="Destination URL"
            type="url"
            inputMode="url"
            autoComplete="off"
            placeholder="https://example.com/a/very/long/address"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            error={errors.original_url}
            className="flex-1"
          />
          <Button type="submit" loading={create.isPending} className="sm:mt-[29px]">
            Shorten link
          </Button>
        </div>

        <button
          type="button"
          onClick={() => setShowOptions((s) => !s)}
          aria-expanded={showOptions}
          className="mt-3 text-sm font-medium text-muted hover:text-ink"
        >
          {showOptions ? 'Hide options' : 'Custom alias and expiry'}
        </button>

        {showOptions && (
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <Field
              label="Custom alias (optional)"
              mono
              autoComplete="off"
              placeholder="spring-sale"
              value={alias}
              onChange={(e) => setAlias(e.target.value)}
              error={errors.custom_alias}
              hint="Letters, numbers, hyphens and underscores. Case matters."
            />
            <Field
              label="Expires (optional)"
              type="datetime-local"
              min={localInputMin()}
              value={expires}
              onChange={(e) => setExpires(e.target.value)}
              error={errors.expires_at}
              hint="After this time the link stops redirecting."
            />
          </div>
        )}
      </form>

      {formError && (
        <div className="mt-4">
          <Notice>{formError}</Notice>
        </div>
      )}
      {result && <ShortLinkResult url={result} variant={variant} signedIn={status === 'authenticated'} />}
    </div>
  )
}
