import { useEffect, useRef, useState } from 'react'

type Option = { id: string; label: string }
export function ChatMenu({ label, value, placeholder, options, disabled, change }: { label: string; value?: string; placeholder: string; options: Option[]; disabled: boolean; change: (id: string) => void }) {
  const [open, setOpen] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const root = useRef<HTMLDivElement>(null)
  const buttons = useRef<(HTMLButtonElement | null)[]>([])
  const initial = useRef(0)
  const current = options.find(o => o.id === value)?.label || placeholder
  const close = () => { setOpen(false); trigger.current?.focus() }
  useEffect(() => {
    if (!open) return
    buttons.current[initial.current]?.focus()
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false) }
    document.addEventListener('pointerdown', outside)
    return () => document.removeEventListener('pointerdown', outside)
  }, [open])
  return <div className="chat-menu" ref={root} onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget)) setOpen(false) }}>
    <button type="button" ref={trigger} aria-label={`${label}：${current}`} aria-haspopup="menu" aria-expanded={open}
      title={current} disabled={disabled || !options.length}
      onClick={() => { initial.current = Math.max(0, options.findIndex(o => o.id === value)); setOpen(!open) }}
      onKeyDown={e => {
        if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
        e.preventDefault(); initial.current = e.key === 'ArrowUp' ? options.length - 1 : 0; setOpen(true)
      }}>{current} ▾</button>
    {open && <div className="chat-menu-options" role="menu" aria-label={label} onKeyDown={e => {
      const index = buttons.current.indexOf(document.activeElement as HTMLButtonElement)
      const next = e.key === 'ArrowDown' ? (index + 1) % options.length : e.key === 'ArrowUp' ? (index - 1 + options.length) % options.length : e.key === 'Home' ? 0 : e.key === 'End' ? options.length - 1 : -1
      if (e.key === 'Escape') { e.preventDefault(); close() }
      if (next >= 0) { e.preventDefault(); buttons.current[next]?.focus() }
    }}>{options.map((o, index) => <button type="button" ref={el => { buttons.current[index] = el }} key={o.id}
      role="menuitemradio" aria-checked={o.id === value} tabIndex={-1}
      onClick={() => { close(); change(o.id) }}>{o.id === value && <span aria-hidden="true">✓ </span>}{o.label}</button>)}</div>}
  </div>
}
