import { useState, type FormEvent } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { Logo } from '../components/Logo'
import { ThemeToggle } from '../components/ThemeToggle'
import { Button, Field, Notice } from '../components/ui'
import { useAuth } from '../context/AuthContext'
import { describeError, toApiError } from '../services/api'
import { EMAIL_PATTERN } from '../utils/format'

type Mode = 'login' | 'register'

export function validateCredentials(mode: Mode, email: string, password: string): { email?: string; password?: string } {
  const errors: { email?: string; password?: string } = {}
  if (!EMAIL_PATTERN.test(email.trim())) errors.email = 'Enter a valid email address.'
  if (!password) errors.password = 'Enter your password.'
  else if (mode === 'register' && password.length < 8) errors.password = 'Use at least 8 characters.'
  return errors
}

function AuthPage({ mode }: { mode: Mode }) {
  const { status, login, register } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from ?? '/dashboard'
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [errors, setErrors] = useState<{ email?: string; password?: string }>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (status === 'authenticated') return <Navigate to={from} replace />

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const found = validateCredentials(mode, email, password)
    setErrors(found)
    if (Object.keys(found).length) return
    setBusy(true)
    try {
      await (mode === 'login' ? login(email.trim(), password) : register(email.trim(), password))
      navigate(from, { replace: true })
    } catch (err) {
      const apiErr = toApiError(err)
      if (apiErr.code === 'email_taken') setErrors({ email: 'An account with this email already exists. Sign in instead.' })
      else if (apiErr.fieldErrors.email || apiErr.fieldErrors.password) setErrors(apiErr.fieldErrors)
      else setFormError(describeError(apiErr))
    } finally {
      setBusy(false)
    }
  }

  const isLogin = mode === 'login'
  return (
    <div className="min-h-screen">
      <header className="mx-auto flex max-w-5xl items-center justify-between px-4 py-5 sm:px-6">
        <Logo />
        <ThemeToggle />
      </header>
      <main className="mx-auto max-w-sm px-4 pt-12 sm:pt-20">
        <h1 className="text-3xl font-semibold">{isLogin ? 'Sign in' : 'Create your account'}</h1>
        <p className="mt-2 text-muted">{isLogin ? 'Welcome back. Your links and analytics are waiting.' : 'Track clicks and manage every link in one place.'}</p>
        <form onSubmit={onSubmit} noValidate className="mt-8 space-y-4">
          {formError && <Notice>{formError}</Notice>}
          <Field
            label="Email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            error={errors.email}
          />
          <Field
            label="Password"
            type="password"
            autoComplete={isLogin ? 'current-password' : 'new-password'}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            error={errors.password}
            hint={isLogin ? undefined : 'At least 8 characters.'}
          />
          <Button type="submit" loading={busy} className="w-full">
            {isLogin ? 'Sign in' : 'Create account'}
          </Button>
        </form>
        <p className="mt-6 text-sm text-muted">
          {isLogin ? (
            <>
              New to Brief?{' '}
              <Link to="/register" state={location.state} className="font-medium text-accent hover:underline">
                Create an account
              </Link>
            </>
          ) : (
            <>
              Already have an account?{' '}
              <Link to="/login" state={location.state} className="font-medium text-accent hover:underline">
                Sign in
              </Link>
            </>
          )}
        </p>
      </main>
    </div>
  )
}

export const LoginPage = () => <AuthPage mode="login" />
export const RegisterPage = () => <AuthPage mode="register" />
