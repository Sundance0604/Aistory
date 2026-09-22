import type { Day } from '../types'

export function MiniBars({ data, metric = 'total_visible_tokens', onSelect }: { data: Day[]; metric?: keyof Day; onSelect?: (date: string) => void }) {
  const rows = data
  const max = Math.max(...rows.map((row) => Number(row[metric]) || 0), 1)
  return (
    <div className="mini-bars-scroll"><div className="mini-bars" style={{ minWidth: `${Math.max(720, rows.length * 8)}px` }} aria-label="完整活动趋势">
      {rows.map((row) => (
        <button onClick={() => onSelect?.(row.date)} key={row.date} style={{ height: `${Math.max(2, (Number(row[metric]) / max) * 100)}%` }} title={`${row.date}: ${row[metric]}`} />
      ))}
    </div></div>
  )
}
