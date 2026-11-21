import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { authApi, setAuthToken, setUnauthorizedHandler } from '../services/api'
import type { User } from '../types/api'

/**
 * Session handling.
 *
 * The API issues a short-lived bearer JWT (no refresh token, no cookie). It is kept in
 * localStorage so a reload keeps you signed in. Trade-off: any XSS could read it, which is why the
 * app renders no untrusted HTML and the API serves a strict CSP. Tokens expire server-side and are
 * dropped client-side as soon as they expire or the API rejects them.
 */
const STORAGE_KEY = 'brief.session'

interface StoredSession {
  token: string
  expiresAt: number
}

function loadSession(): StoredSession | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const s = JSON.parse(raw) as StoredSession
    if (typeof s.token !== 'string' || typeof s.expiresAt !== 'number' || s.expiresAt <= Date.now()) {
      localStorage.removeItem(STORAGE_KEY)
      return null
    }
    return s
  } catch {
    return null
  }
}

function saveSession(s: StoredSession | null) {
  try {
    if (s) localStorage.setItem(STORAGE_KEY, JSON.stringify(s))
    else localStorage.removeItem(STORAGE_KEY)
  } catch {
    /* storage unavailable: the session just won't survive a reload */
  }
}

type Status = 'loading' | 'authenticated' | 'anonymous'

interface AuthValue {
  user: User | null
  status: Status
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [user, setUser] = useState<User | null>(null)
  const [status, setStatus] = useState<Status>(() => (loadSession() ? 'loading' : 'anonymous'))

  const logout = useCallback(() => {
    setAuthToken(null)
    saveSession(null)
    setUser(null)
    setStatus('anonymous')
    queryClient.clear() // never show one account's cached data to the next
  }, [queryClient])

  useEffect(() => {
    setUnauthorizedHandler(logout)
    return () => setUnauthorizedHandler(null)
  }, [logout])

  // Restore a saved session by asking the API who we are.
  useEffect(() => {
    const session = loadSession()
    if (!session) return
    let cancelled = false
    setAuthToken(session.token)
    authApi
      .me()
      .then((u) => {
        if (cancelled) return
        setUser(u)
        setStatus('authenticated')
      })
      .catch(() => {
        if (cancelled) return
        setAuthToken(null)
        saveSession(null)
        setStatus('anonymous')
      })
    return () => {
      cancelled = true
    }
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const res = await authApi.login(email, password)
    setAuthToken(res.access_token)
    saveSession({ token: res.access_token, expiresAt: Date.now() + res.expires_in * 1000 })
    setUser(res.user)
    setStatus('authenticated')
  }, [])

  const register = useCallback(
    async (email: string, password: string) => {
      await authApi.register(email, password)
      await login(email, password)
    },
    [login],
  )

  const value = useMemo(() => ({ user, status, login, register, logout }), [user, status, login, register, logout])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}
