import { useEffect, useMemo, useState } from 'react'
import { api, compact, scopeQuery } from '../api'
import type { Conversation } from '../types'

type Metric = 'prompts' | 'total_messages' | 'outbound_messages' | 'inbound_messages' | 'total_visible_tokens' | 'active_days' | 'estimated_active_seconds' | 'session_count'
const labels: Record<Metric, string> = { prompts: '提示数', total_messages: '总消息', outbound_messages: '我发送', inbound_messages: '收到', total_visible_tokens: '总 Token', active_days: '活跃日', estimated_active_seconds: '估算活跃时间', session_count: '会话段数' }

export function Lifecycle({ provider, accountId, onConversation }: { provider: string; accountId: string; onConversation: (id: string) => void }) {
  const [rows, setRows] = useState<Conversation[]>([])
  const [metric, setMetric] = useState<Metric>('prompts')
  useEffect(() => { api<Conversation[]>(`/api/lifecycle?${scopeQuery(provider, accountId)}`).then(setRows) }, [provider, accountId])
  useEffect(() => { setMetric(provider === 'wechat' ? 'total_messages' : 'prompts') }, [provider])
  const availableMetrics: Metric[] = provider === 'wechat' ? ['total_messages','outbound_messages','inbound_messages','active_days','estimated_active_seconds','session_count'] : ['prompts','total_visible_tokens','active_days','estimated_active_seconds','session_count']
  const points = useMemo(() => {
    const maxX = Math.max(...rows.map(row => row.calendar_span_days || 0), 1)
    const maxY = Math.max(...rows.map(row => Number(row[metric]) || 0), 1)
    return rows.map(row => ({ row, x: 42 + ((row.calendar_span_days || 0) / maxX) * 850, y: 330 - (Number(row[metric]) / maxY) * 285 }))
  }, [rows, metric])
  return <div className="page-stack">
    <header className="page-header"><div><span className="eyebrow">对话生命周期</span><h1>生命周期</h1></div><p>横轴是第一条到最后一条活动的日历跨度；微信颜色区分私聊与群聊，形状区分平台。</p></header>
    <section className="panel"><div className="panel-heading"><div><span className="eyebrow">创建至最后活动</span><h2>{rows.length} 个对话</h2></div><select value={metric} onChange={event => setMetric(event.target.value as Metric)}>{availableMetrics.map(value => <option value={value} key={value}>{labels[value]}</option>)}</select></div>
      <div className="lifecycle-scroll"><svg className="lifecycle-chart" viewBox="0 0 940 370" role="img" aria-label={`对话生命周期与${labels[metric]}`}>
        <line x1="42" y1="330" x2="915" y2="330"/><line x1="42" y1="25" x2="42" y2="330"/>
        <text x="760" y="360">日历跨度（天）</text><text x="48" y="18">{labels[metric]}</text>
        {points.map(({row,x,y}) => { const color = row.provider === 'wechat' ? '#524765' : row.topics?.[0]?.color || '#777'; return row.provider === 'gemini'
          ? <rect key={row.id} x={x-5} y={y-5} width="10" height="10" rx="2" fill={color} onClick={() => onConversation(row.id)}><title>{row.title} · {compact(Number(row[metric]) || 0)}</title></rect>
          : row.provider === 'wechat' ? <polygon key={row.id} points={`${x},${y-6} ${x+6},${y} ${x},${y+6} ${x-6},${y}`} fill={color} onClick={() => onConversation(row.id)}><title>{row.title} · {compact(Number(row[metric]) || 0)}</title></polygon>
          : <circle key={row.id} cx={x} cy={y} r="5" fill={color} onClick={() => onConversation(row.id)}><title>{row.title} · {compact(Number(row[metric]) || 0)}</title></circle> })}
      </svg></div>
      <div className="chart-legend"><span><i className="shape circle"/> ChatGPT</span><span><i className="shape square"/> Gemini</span><span>◆ WeChat · 罗马紫</span></div>
    </section>
  </div>
}
