import type { Day } from '../types'

export function MiniBars({ data, metric = 'total_visible_tokens' }: { data: Day[]; metric?: keyof Day }) {
  const rows = data.slice(-90)
  const max = Math.max(...rows.map((row) => Number(row[metric]) || 0), 1)
  return (
    <div className="mini-bars" aria-label="最近 90 天趋势">
      {rows.map((row) => (
        <span key={row.date} style={{ height: `${Math.max(2, (Number(row[metric]) / max) * 100)}%` }} title={`${row.date}: ${row[metric]}`} />
      ))}
    </div>
  )
}
