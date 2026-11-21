import { Link } from 'react-router-dom'
import { CreateLinkForm } from '../components/CreateLinkForm'
import { Logo } from '../components/Logo'
import { ThemeToggle } from '../components/ThemeToggle'
import { ButtonLink } from '../components/ui'
import { useAuth } from '../context/AuthContext'

const features = [
  {
    title: 'Redirects that answer immediately',
    body: 'Popular links are served from memory, so visitors reach the destination without waiting on a database.',
  },
  {
    title: 'Analytics without IP addresses',
    body: 'See clicks by day, browser, device, referrer and country. Visitor IPs are never stored, only a keyed hash that cannot be reversed.',
  },
  {
    title: 'Aliases and expiry dates',
    body: 'Pick a readable alias like spring-sale, or let Brief generate a seven-character code. Set a date after which the link stops working.',
  },
]

export default function Landing() {
  const { status } = useAuth()
  const signedIn = status === 'authenticated'
  return (
    <div className="min-h-screen">
      <header className="mx-auto flex max-w-5xl items-center justify-between px-4 py-5 sm:px-6">
        <Logo />
        <nav aria-label="Account" className="flex items-center gap-2">
          <ThemeToggle />
          {signedIn ? (
            <ButtonLink to="/dashboard" size="sm">
              Open dashboard
            </ButtonLink>
          ) : (
            <>
              <ButtonLink to="/login" variant="ghost" size="sm">
                Sign in
              </ButtonLink>
              <ButtonLink to="/register" size="sm">
                Create account
              </ButtonLink>
            </>
          )}
        </nav>
      </header>

      <main className="mx-auto max-w-5xl px-4 sm:px-6">
        <section className="pb-20 pt-12 sm:pt-20">
          <h1 className="max-w-3xl text-4xl font-semibold leading-[1.08] sm:text-6xl">Make long links short. See what they do.</h1>
          <p className="mt-5 max-w-xl text-lg text-muted">
            Paste a web address to get a short link you can share anywhere. Create an account to follow its clicks.
          </p>
          <div className="mt-10 max-w-3xl">
            <CreateLinkForm variant="hero" />
          </div>
        </section>

        <section aria-label="Features" className="border-t border-line">
          <ul className="grid md:grid-cols-3 md:divide-x md:divide-line">
            {features.map((f, i) => (
              <li key={f.title} className={`py-8 md:px-8 md:first:pl-0 md:last:pr-0 ${i < features.length - 1 ? 'border-b border-line md:border-b-0' : ''}`}>
                <h2 className="text-lg font-semibold">{f.title}</h2>
                <p className="mt-2 text-muted">{f.body}</p>
              </li>
            ))}
          </ul>
        </section>
      </main>

      <footer className="mx-auto max-w-5xl border-t border-line px-4 py-8 text-sm text-muted sm:px-6">
        <div className="flex flex-wrap justify-between gap-3">
          <span>Brief short links</span>
          <span>
            <Link to="/login" className="hover:text-ink">
              Sign in
            </Link>
            {' · '}
            <Link to="/register" className="hover:text-ink">
              Create account
            </Link>
          </span>
        </div>
      </footer>
    </div>
  )
}
