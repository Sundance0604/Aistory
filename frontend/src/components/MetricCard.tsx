import type { ReactNode } from 'react'

export function MetricCard({ label, value, note, icon }: { label: string; value: string; note?: string; icon?: ReactNode }) {
  return (
    <article className="metric-card">
      <div className="metric-label"><span>{label}</span>{icon}</div>
      <strong>{value}</strong>
      {note && <small>{note}</small>}
    </article>
  )
}
