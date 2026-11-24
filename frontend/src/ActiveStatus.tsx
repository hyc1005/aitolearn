import { useEffect, useState } from 'react'

export function ActiveStatus({ label, started }: { label: string; started?: number }) {
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    if (!started) return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [started])
  return <span className="active-status" role="status">
    {label}{started && <span aria-hidden="true"> · 已用 {Math.max(0,Math.floor((now - started) / 1000))} 秒</span>}<span className="active-status-dots" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span>
  </span>
}
