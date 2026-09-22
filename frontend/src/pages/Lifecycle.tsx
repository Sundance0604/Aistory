import { useEffect, useMemo, useState } from 'react'
import { api, compact } from '../api'
import type { Conversation } from '../types'

type Metric = 'prompts' | 'total_visible_tokens' | 'active_days' | 'estimated_active_seconds' | 'session_count'
const labels: Record<Metric, string> = { prompts: '提示数', total_visible_tokens: '总 Token', active_days: '活跃日', estimated_active_seconds: '估算活跃时间', session_count: '会话段数' }

export function Lifecycle({ provider, onConversation }: { provider: string; onConversation: (id: string) => void }) {
  const [rows, setRows] = useState<Conversation[]>([])
  const [metric, setMetric] = useState<Metric>('prompts')
  useEffect(() => { api<Conversation[]>(`/api/lifecycle?provider=${provider}`).then(setRows) }, [provider])
  const points = useMemo(() => {
    const maxX = Math.max(...rows.map(row => row.calendar_span_days || 0), 1)
    const maxY = Math.max(...rows.map(row => Number(row[metric]) || 0), 1)
    return rows.map(row => ({ row, x: 42 + ((row.calendar_span_days || 0) / maxX) * 850, y: 330 - (Number(row[metric]) / maxY) * 285 }))
  }, [rows, metric])
  return <div className="page-stack">
    <header className="page-header"><div><span className="eyebrow">对话生命周期</span><h1>生命周期</h1></div><p>横轴是日历跨度，不代表连续使用时长；颜色代表主题，形状代表平台。</p></header>
    <section className="panel"><div className="panel-heading"><div><span className="eyebrow">创建至最后活动</span><h2>{rows.length} 个对话</h2></div><select value={metric} onChange={event => setMetric(event.target.value as Metric)}>{Object.entries(labels).map(([value,label]) => <option value={value} key={value}>{label}</option>)}</select></div>
      <div className="lifecycle-scroll"><svg className="lifecycle-chart" viewBox="0 0 940 370" role="img" aria-label={`对话生命周期与${labels[metric]}`}>
        <line x1="42" y1="330" x2="915" y2="330"/><line x1="42" y1="25" x2="42" y2="330"/>
        <text x="760" y="360">日历跨度（天）</text><text x="48" y="18">{labels[metric]}</text>
        {points.map(({row,x,y}) => row.provider === 'gemini'
          ? <rect key={row.id} x={x-5} y={y-5} width="10" height="10" rx="2" fill={row.topics?.[0]?.color || '#91A9BC'} onClick={() => onConversation(row.id)}><title>{row.title} · {compact(Number(row[metric]) || 0)}</title></rect>
          : <circle key={row.id} cx={x} cy={y} r="5" fill={row.topics?.[0]?.color || '#777'} onClick={() => onConversation(row.id)}><title>{row.title} · {compact(Number(row[metric]) || 0)}</title></circle>)}
      </svg></div>
      <div className="chart-legend"><span><i className="shape circle"/> ChatGPT</span><span><i className="shape square"/> Gemini</span><span>颜色＝主主题</span></div>
    </section>
  </div>
}
