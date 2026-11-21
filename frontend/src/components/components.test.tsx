import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../context/AuthContext'
import { ThemeProvider, useTheme } from '../context/ThemeContext'
import { CreateLinkForm, validateLinkForm } from './CreateLinkForm'
import { ProtectedRoute } from './ProtectedRoute'
import { validateCredentials } from '../pages/AuthPages'
import { ApiError, urlApi, authApi } from '../services/api'

function wrap(ui: React.ReactNode, route = '/') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[route]}>
        <ThemeProvider>
          <AuthProvider>{ui}</AuthProvider>
        </ThemeProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => vi.restoreAllMocks())

describe('validateLinkForm', () => {
  const ok = { url: 'https://example.com', alias: '', expires: '' }
  it('accepts a minimal valid form', () => expect(validateLinkForm(ok)).toEqual({}))
  it('flags empty and non-http URLs', () => {
    expect(validateLinkForm({ ...ok, url: '' }).original_url).toMatch(/Enter the URL/)
    expect(validateLinkForm({ ...ok, url: 'javascript:alert(1)' }).original_url).toMatch(/http/)
  })
  it('flags bad aliases and past expiry', () => {
    expect(validateLinkForm({ ...ok, alias: 'a b' }).custom_alias).toBeTruthy()
    expect(validateLinkForm({ ...ok, expires: '2000-01-01T00:00' }).expires_at).toBeTruthy()
  })
})

describe('validateCredentials', () => {
  it('requires a valid email and, on register, an 8+ character password', () => {
    expect(validateCredentials('login', 'nope', 'x').email).toBeTruthy()
    expect(validateCredentials('login', 'a@b.co', 'x')).toEqual({})
    expect(validateCredentials('register', 'a@b.co', 'short').password).toBeTruthy()
    expect(validateCredentials('register', 'a@b.co', 'long-enough')).toEqual({})
  })
})

describe('CreateLinkForm', () => {
  it('blocks invalid input client-side without calling the API', async () => {
    const create = vi.spyOn(urlApi, 'create')
    wrap(<CreateLinkForm />)
    await userEvent.type(screen.getByLabelText('Destination URL'), 'not-a-url')
    await userEvent.click(screen.getByRole('button', { name: 'Shorten link' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/http/)
    expect(create).not.toHaveBeenCalled()
  })

  it('creates a link and shows the short URL with a copy button', async () => {
    vi.spyOn(urlApi, 'create').mockResolvedValue({
      short_code: 'aB3xK9q',
      short_url: 'http://localhost:8000/aB3xK9q',
      original_url: 'https://example.com/long',
      created_at: new Date().toISOString(),
      expires_at: null,
      is_active: true,
      is_expired: false,
      total_clicks: 0,
      is_owned: false,
    })
    wrap(<CreateLinkForm variant="hero" />)
    await userEvent.type(screen.getByLabelText('Destination URL'), 'https://example.com/long')
    await userEvent.click(screen.getByRole('button', { name: 'Shorten link' }))
    expect(await screen.findByTestId('short-link')).toHaveTextContent('localhost:8000/aB3xK9q')
    expect(screen.getByRole('button', { name: 'Copy link' })).toBeInTheDocument()
    expect(urlApi.create).toHaveBeenCalledWith({ original_url: 'https://example.com/long' })
  })

  it('shows an alias conflict on the alias field', async () => {
    vi.spyOn(urlApi, 'create').mockRejectedValue(new ApiError('That alias is already in use.', { status: 409, code: 'alias_conflict' }))
    wrap(<CreateLinkForm />)
    await userEvent.type(screen.getByLabelText('Destination URL'), 'https://example.com')
    await userEvent.click(screen.getByRole('button', { name: 'Custom alias and expiry' }))
    await userEvent.type(screen.getByLabelText('Custom alias (optional)'), 'taken-one')
    await userEvent.click(screen.getByRole('button', { name: 'Shorten link' }))
    expect(await screen.findByText(/already taken/)).toBeInTheDocument()
  })

  it('tells the user how long to wait when rate limited', async () => {
    vi.spyOn(urlApi, 'create').mockRejectedValue(new ApiError('Too many requests.', { status: 429, code: 'rate_limited', retryAfter: 30 }))
    wrap(<CreateLinkForm />)
    await userEvent.type(screen.getByLabelText('Destination URL'), 'https://example.com')
    await userEvent.click(screen.getByRole('button', { name: 'Shorten link' }))
    expect(await screen.findByText('Too many requests. Try again in 30 seconds.')).toBeInTheDocument()
  })
})

describe('ProtectedRoute', () => {
  const tree = (
    <Routes>
      <Route path="/login" element={<p>login page</p>} />
      <Route element={<ProtectedRoute />}>
        <Route path="/dashboard" element={<p>secret dashboard</p>} />
      </Route>
    </Routes>
  )

  it('redirects anonymous visitors to the login page', async () => {
    wrap(tree, '/dashboard')
    expect(await screen.findByText('login page')).toBeInTheDocument()
    expect(screen.queryByText('secret dashboard')).not.toBeInTheDocument()
  })

  it('restores a saved session, then renders the page', async () => {
    localStorage.setItem('brief.session', JSON.stringify({ token: 't', expiresAt: Date.now() + 60_000 }))
    vi.spyOn(authApi, 'me').mockResolvedValue({ id: 1, email: 'a@b.co', created_at: new Date().toISOString() })
    wrap(tree, '/dashboard')
    expect(await screen.findByText('secret dashboard')).toBeInTheDocument()
  })

  it('discards an expired saved session without calling the API', async () => {
    localStorage.setItem('brief.session', JSON.stringify({ token: 't', expiresAt: Date.now() - 1000 }))
    const me = vi.spyOn(authApi, 'me')
    wrap(tree, '/dashboard')
    expect(await screen.findByText('login page')).toBeInTheDocument()
    expect(me).not.toHaveBeenCalled()
    expect(localStorage.getItem('brief.session')).toBeNull()
  })

  it('drops the session when the API rejects the token', async () => {
    localStorage.setItem('brief.session', JSON.stringify({ token: 'bad', expiresAt: Date.now() + 60_000 }))
    vi.spyOn(authApi, 'me').mockRejectedValue(new ApiError('Invalid token.', { status: 401, code: 'invalid_token' }))
    wrap(tree, '/dashboard')
    expect(await screen.findByText('login page')).toBeInTheDocument()
    await waitFor(() => expect(localStorage.getItem('brief.session')).toBeNull())
  })
})

describe('ThemeProvider', () => {
  function Probe() {
    const { resolved, preference, setPreference } = useTheme()
    return (
      <button onClick={() => setPreference('dark')}>
        {preference}:{resolved}
      </button>
    )
  }
  it('applies and persists the chosen theme', async () => {
    wrap(<Probe />)
    expect(screen.getByRole('button')).toHaveTextContent('system:light')
    await userEvent.click(screen.getByRole('button'))
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(localStorage.getItem('brief.theme')).toBe('dark')
  })
})
