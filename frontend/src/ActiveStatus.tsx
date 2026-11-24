export function ActiveStatus({ label }: { label: string }) {
  return <span className="active-status" role="status">
    {label}<span className="active-status-dots" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span>
  </span>
}
