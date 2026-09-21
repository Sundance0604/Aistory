import { useMemo, useState } from 'react'
import type { Day } from '../types'
import { number } from '../api'

type Metric = 'prompts' | 'total_visible_tokens' | 'active_conversations'

const labels: Record<Metric, string> = {
  prompts: '提示数',
  total_visible_tokens: '可见 Token',
  active_conversations: '活跃对话',
}

function dayKey(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

export function ActivityHeatmap({ data, onDay }: { data: Day[]; onDay?: (day: string) => void }) {
  const availableYears = useMemo(() => {
    const years = [...new Set(data.map((item) => Number(item.date.slice(0, 4))))].sort((a, b) => b - a)
    return years.length ? years : [new Date().getFullYear()]
  }, [data])
  const [year, setYear] = useState(availableYears[0])
  const [metric, setMetric] = useState<Metric>('prompts')
  const [selectedKey, setSelectedKey] = useState<string>()
  const byDate = useMemo(() => new Map(data.map((item) => [item.date, item])), [data])
  const layout = useMemo(() => {
    const start = new Date(year, 0, 1)
    start.setDate(start.getDate() - start.getDay())
    const end = new Date(year, 11, 31)
    end.setDate(end.getDate() + (6 - end.getDay()))
    const cells: { date: Date; key: string; week: number; day: number; item?: Day }[] = []
    const cursor = new Date(start)
    let index = 0
    while (cursor <= end) {
      const key = dayKey(cursor)
      cells.push({ date: new Date(cursor), key, week: Math.floor(index / 7), day: cursor.getDay(), item: byDate.get(key) })
      cursor.setDate(cursor.getDate() + 1)
      index += 1
    }
    const values = cells
      .filter((cell) => cell.date.getFullYear() === year)
      .map((cell) => Number(cell.item?.[metric] || 0))
      .filter(Boolean)
      .sort((a, b) => a - b)
    const cut = [0.25, 0.5, 0.75].map((q) => values[Math.max(0, Math.ceil(values.length * q) - 1)] || 0)
    const months = Array.from({ length: 12 }, (_, month) => {
      const first = new Date(year, month, 1)
      return { label: first.toLocaleDateString('zh-CN', { month: 'short' }), week: Math.floor((+first - +start) / 604800000) }
    })
    return { cells, cut, months }
  }, [year, metric, byDate])

  const level = (value: number) => {
    if (!value) return 0
    if (value <= layout.cut[0]) return 1
    if (value <= layout.cut[1]) return 2
    if (value <= layout.cut[2]) return 3
    return 4
  }
  const width = 42 + 54 * 13
  const selected = selectedKey ? byDate.get(selectedKey) : undefined

  return (
    <section className="panel heatmap-panel">
      <div className="panel-heading heatmap-heading">
        <div>
          <span className="eyebrow">一年中的节奏</span>
          <h2>活动热力图</h2>
        </div>
        <div className="controls">
          <div className="segmented">
            {(Object.keys(labels) as Metric[]).map((key) => (
              <button className={metric === key ? 'active' : ''} onClick={() => setMetric(key)} key={key}>{labels[key]}</button>
            ))}
          </div>
          <select value={year} onChange={(event) => setYear(Number(event.target.value))}>
            {availableYears.map((item) => <option value={item} key={item}>{item}</option>)}
          </select>
        </div>
      </div>
      <div className="heatmap-scroll">
        <svg className="heatmap" viewBox={`0 0 ${width} 132`} role="img" aria-label={`${year} ${labels[metric]}活动热力图`}>
          {layout.months.map((month) => <text x={40 + month.week * 13} y="14" key={month.label}>{month.label}</text>)}
          {[['一', 2], ['三', 4], ['五', 6]].map(([label, day]) => <text x="2" y={27 + Number(day) * 13} key={label}>{label}</text>)}
          {layout.cells.map((cell) => {
            const item = cell.item
            const value = Number(item?.[metric] || 0)
            const muted = cell.date.getFullYear() !== year
            const title = item
              ? `${cell.key}\n${number.format(item.prompts)} 个提示\n${number.format(item.prompt_visible_tokens)} 输入可见 Token\n${number.format(item.response_visible_tokens)} 输出可见 Token\n${number.format(item.total_visible_tokens)} 总可见 Token\n${number.format(item.active_conversations)} 个活跃对话`
              : `${cell.key}\n无活动`
            return (
              <rect
                key={cell.key}
                className={`heat-cell level-${muted ? 0 : level(value)}`}
                x={40 + cell.week * 13}
                y={22 + cell.day * 13}
                width="10"
                height="10"
                rx="2"
                role={!muted && item ? 'button' : undefined}
                tabIndex={!muted && item ? 0 : undefined}
                onClick={() => { if (!muted && item) { setSelectedKey(cell.key); onDay?.(cell.key) } }}
                onKeyDown={(event) => { if (!muted && item && (event.key === 'Enter' || event.key === ' ')) { setSelectedKey(cell.key); onDay?.(cell.key) } }}
              ><title>{title}</title></rect>
            )
          })}
        </svg>
      </div>
      <div className="legend"><span>少</span>{[0, 1, 2, 3, 4].map((item) => <i className={`level-${item}`} key={item} />)}<span>多</span><b>非零天按四分位分档</b></div>
      {selected && <div className="day-detail"><div><span className="eyebrow">所选日期</span><b>{selected.date}</b></div><div><strong>{number.format(selected.prompts)}</strong><span>提示</span></div><div><strong>{number.format(selected.total_visible_tokens)}</strong><span>总可见 Token</span></div><div><strong>{number.format(selected.prompt_visible_tokens)}</strong><span>输入</span></div><div><strong>{number.format(selected.response_visible_tokens)}</strong><span>输出</span></div></div>}
    </section>
  )
}
