import { Link } from 'react-router-dom'

export function Logo({ to = '/' }: { to?: string }) {
  return (
    <Link to={to} className="inline-flex items-center gap-2 text-lg font-semibold tracking-tight" aria-label="Brief home">
      <span aria-hidden="true" className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-accent">
        <span className="block h-[3px] w-3.5 rounded-full bg-accent-ink" />
      </span>
      brief
    </Link>
  )
}
