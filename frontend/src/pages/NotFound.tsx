import { ButtonLink } from '../components/ui'
import { Logo } from '../components/Logo'

export default function NotFound() {
  return (
    <div className="mx-auto max-w-xl px-4 pt-24">
      <Logo />
      <h1 className="mt-10 text-3xl font-semibold">That page doesn't exist</h1>
      <p className="mt-2 text-muted">Check the address, or head back to the start.</p>
      <ButtonLink to="/" className="mt-6">
        Go to the home page
      </ButtonLink>
    </div>
  )
}
