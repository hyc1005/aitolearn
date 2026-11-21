import type { ReactNode } from 'react'

const shapes: Record<string, ReactNode> = {
  overview: <><rect className="nav-fill" x="2" y="3" width="12" height="10" rx="2" /><path d="M2 6.5h12M6.5 6.5V13m3.7-6.5V13M2 9.8h12" /></>,
  chat: <><path className="nav-fill" d="M7 6h5a2 2 0 0 1 2 2v5l-2.5-1.5H9a2 2 0 0 1-2-2Z" /><path className="nav-fill" d="M4 2.5h5a2 2 0 0 1 2 2v2a2 2 0 0 1-2 2H5L2 10V4.5a2 2 0 0 1 2-2Z" /><path d="M4.5 5.5h4" /></>,
  models: <><path className="nav-fill" d="M4.5 5.5h7V8a3.5 3.5 0 0 1-7 0Z" /><path d="M6 5.5v-3m4 3v-3m-2 9v2M3.5 5.5h9" /></>,
  agent: <><path d="M8 5.5v2.7m-4.7 2V8.2h9.4v2" /><rect className="nav-fill" x="6.1" y="2" width="3.8" height="3.6" rx="1" /><rect className="nav-fill" x="1.5" y="10.3" width="3.6" height="3.6" rx="1" /><rect className="nav-fill" x="10.9" y="10.3" width="3.6" height="3.6" rx="1" /></>,
  import: <><path className="nav-fill" d="M3 2.5h6l3 3v7A1.5 1.5 0 0 1 10.5 14H4.5A1.5 1.5 0 0 1 3 12.5Z" /><path d="M9 2.5v3h3M5 8h5l-1.8 2v1.6l-1.4.6V10Z" /></>,
  materials: <path className="nav-fill" d="M8 4c-2-1.5-4-1.7-6-1v9.3c2-.6 4-.4 6 1 2-1.4 4-1.6 6-1V3c-2-.7-4-.5-6 1Zm0 0v9" />,
  student: <><path d="m8 1.8 6 4.5-2.2 7.2H4.2L2 6.3Z" /><path className="nav-fill" d="m8 4 3.6 3-1.6 4.8-4.4-1-1-3.5Z" /></>,
  drafts: <><rect className="nav-fill" x="3" y="3" width="10" height="11" rx="2" /><path d="M6 2h4v2.5H6zM5.4 8l1.2 1.2 2.2-2.4M5.5 11.6h5" /></>,
  settings: <><path className="nav-fill" d="m6.6 2-.5 1.6-1.1.7-1.7-.4-1.3 2.3 1.2 1.2v1.2L2 9.8l1.3 2.3 1.7-.4 1.1.7.5 1.6h2.8l.5-1.6 1.1-.7 1.7.4 1.3-2.3-1.2-1.2V7.4L14 6.2l-1.3-2.3-1.7.4-1.1-.7L9.4 2Z" /><circle cx="8" cy="8" r="2" /></>,
  toggle: <><rect className="nav-fill" x="2" y="3" width="12" height="10" rx="2" /><path d="M6 3v10m5-7-2 2 2 2" /></>,
}

export function NavIcon({ name, flipped }: { name: string; flipped?: boolean }) {
  return <svg className={flipped ? 'nav-icon nav-icon-flipped' : 'nav-icon'} viewBox="0 0 16 16"
    aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" strokeLinejoin="round">
    {shapes[name]}
  </svg>
}
