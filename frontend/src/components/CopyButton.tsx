import { useEffect, useRef, useState } from 'react'
import { copyText } from '../utils/format'
import { Button } from './ui'

export function CopyButton({ text, label = 'Copy', size = 'sm', variant = 'secondary' }: {
  text: string
  label?: string
  size?: 'sm' | 'md'
  variant?: 'primary' | 'secondary' | 'ghost'
}) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  useEffect(() => () => clearTimeout(timer.current), [])

  async function onClick() {
    const ok = await copyText(text)
    setState(ok ? 'copied' : 'failed')
    clearTimeout(timer.current)
    timer.current = setTimeout(() => setState('idle'), 1800)
  }

  return (
    <Button type="button" variant={variant} size={size} onClick={onClick} className="min-w-20">
      {state === 'copied' ? 'Copied' : state === 'failed' ? 'Copy failed' : label}
      <span className="sr-only" aria-live="polite">
        {state === 'copied' ? 'Link copied to clipboard' : ''}
      </span>
    </Button>
  )
}
