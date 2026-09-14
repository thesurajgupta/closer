import type { ReactNode } from 'react'

export const CATEGORY_ICON: Record<string, string> = {
  WARRANTY: '🛠',
  BILLING: '₹',
  REFUND: '↩',
  APPOINTMENT: '🗓',
  DOCUMENT_REQUEST: '📄',
  RENEWAL: '🔁',
  DELIVERY: '📦',
  OTHER: '✉',
}

export function statusTone(status: string): 'calm' | 'attention' | 'alert' | 'wait' | '' {
  switch (status) {
    case 'COMPLETED':
      return 'calm'
    case 'HUMAN_DECISION':
      return 'attention'
    case 'FAILED':
    case 'EXPIRED':
      return 'alert'
    case 'WAITING':
      return 'wait'
    default:
      return ''
  }
}

export function Pill({ tone = '', children }: { tone?: string; children: ReactNode }) {
  return <span className={`pill ${tone}`}>{children}</span>
}

export function Section({
  title,
  hint,
  children,
}: {
  title: string
  hint?: ReactNode
  children: ReactNode
}) {
  return (
    <>
      <div className="section-head">
        <h2>{title}</h2>
        {hint ? <span className="hint">{hint}</span> : null}
      </div>
      {children}
    </>
  )
}

export function Switch({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button
      className="switch"
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={() => onChange(!on)}
    />
  )
}

export function Segmented({
  value,
  options,
  onChange,
}: {
  value: string
  options: { value: string; label: string }[]
  onChange: (v: string) => void
}) {
  return (
    <div className="seg">
      {options.map((o) => (
        <button
          key={o.value}
          className={o.value}
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="card card-pad empty">
      <div className="big">{title}</div>
      <div>{body}</div>
    </div>
  )
}
