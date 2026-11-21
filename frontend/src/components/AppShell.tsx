import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { Logo } from './Logo'
import { ThemeToggle } from './ThemeToggle'
import { Button } from './ui'

const navClass = ({ isActive }: { isActive: boolean }) =>
  `border-b-2 py-4 text-sm font-medium ${isActive ? 'border-accent text-ink' : 'border-transparent text-muted hover:text-ink'}`

export function AppShell() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  return (
    <div className="min-h-screen">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-6 px-4 sm:px-6 md:flex-nowrap">
          <Logo to="/dashboard" />
          <div className="flex items-center gap-3 py-3 md:order-3">
            <span className="hidden max-w-48 truncate text-sm text-muted md:inline" title={user?.email}>
              {user?.email}
            </span>
            <ThemeToggle />
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                logout()
                navigate('/')
              }}
            >
              Sign out
            </Button>
          </div>
          <nav aria-label="Main" className="flex w-full gap-5 border-t border-line md:order-2 md:w-auto md:flex-1 md:border-t-0">
            <NavLink to="/dashboard" className={navClass}>
              Dashboard
            </NavLink>
            <NavLink to="/links" className={navClass}>
              Links
            </NavLink>
            <NavLink to="/settings" className={navClass}>
              Settings
            </NavLink>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-10 sm:px-6">
        <Outlet />
      </main>
    </div>
  )
}
