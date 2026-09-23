import React, { useEffect, useMemo, useState } from 'react'
import { api, compact, scopeQuery } from '../api'
import { MiniBars } from '../components/MiniBars'
import type { Day, DayConversation } from '../types'

type Granularity = 'daily' | 'weekly' | 'monthly'
type Metric = 'prompts' | 'outbound_messages' | 'inbound_messages' | 'total_messages' | 'prompt_visible_tokens' | 'response_visible_tokens' | 'total_visible_tokens' | 'active_conversations'

export function ActivityPage({ initial, provider, accountId, onConversation }: { initial: Day[]; provider: string; accountId: string; onConversation: (id: string) => void }) {
  const [granularity, setGranularity] = useState<Granularity>('daily')
  const [metric, setMetric] = useState<Metric>('total_visible_tokens')
  const [data, setData] = useState<Day[]>(initial)
  const [selected, setSelected] = useState<string>()
  const [details, setDetails] = useState<DayConversation[]>([])
  const isWechat = provider === 'wechat'
  useEffect(() => { setData(initial); setSelected(undefined); setDetails([]) }, [initial])
  useEffect(() => { setMetric(isWechat ? 'total_messages' : 'total_visible_tokens') }, [isWechat])
  const changeGranularity = async (value: Granularity) => {
    setGranularity(value)
    setSelected(undefined)
    setDetails([])
    setData(await api<Day[]>(`/api/activity/${value}?${scopeQuery(provider, accountId)}`))
  }
  const select = async (date: string) => {
    setSelected(date)
    if (granularity === 'daily') setDetails(await api<DayConversation[]>(`/api/activity/${date}/conversations?${scopeQuery(provider, accountId)}`))
  }
  const total = useMemo(() => data.reduce((sum, row) => sum + Number(row[metric]), 0), [data, metric])
  return <div className="page-stack">
    <header className="page-header"><div><span className="eyebrow">时间序列</span><h1>活动</h1></div><p>拖动或横向滚动可查看完整历史；点击某一天可下钻至对话。</p></header>
    <section className="panel">
      <div className="panel-heading wrap"><div><span className="eyebrow">当前视图总计</span><h2>{compact(total)}</h2></div><div className="controls">
        <div className="segmented">{(['daily','weekly','monthly'] as Granularity[]).map(value => <button className={granularity===value?'active':''} onClick={() => changeGranularity(value)} key={value}>{value==='daily'?'日':value==='weekly'?'周':'月'}</button>)}</div>
        <select value={metric} onChange={event => setMetric(event.target.value as Metric)}>{isWechat ? <><option value="outbound_messages">我发送</option><option value="inbound_messages">收到</option><option value="total_messages">总事件</option></> : <><option value="prompts">提示数</option><option value="prompt_visible_tokens">输入可见 Token</option><option value="response_visible_tokens">输出可见 Token</option><option value="total_visible_tokens">总可见 Token</option></>}<option value="active_conversations">活跃对话</option></select>
      </div></div>
      <MiniBars data={data} metric={metric} onSelect={select} />
      <div className="activity-table-scroll"><div className="activity-table"><span>周期</span><span>{isWechat ? '我发送' : '提示'}</span><span>{isWechat ? '收到' : '输入'}</span><span>{isWechat ? '总计' : '输出'}</span>{[...data].reverse().map(row => <button className={`table-row ${selected===row.date?'selected':''}`} onClick={() => select(row.date)} key={row.date}><b>{row.date}</b><span>{compact(isWechat ? row.outbound_messages : row.prompts)}</span><span>{compact(isWechat ? row.inbound_messages : row.prompt_visible_tokens)}</span><span>{compact(isWechat ? row.total_messages : row.response_visible_tokens)}</span></button>)}</div></div>
    </section>
    {selected && granularity === 'daily' && <section className="panel"><div className="panel-heading"><div><span className="eyebrow">{selected}</span><h2>当日对话</h2></div><span>{details.length} 个对话</span></div><div className="day-conversations">{details.map(row => <button onClick={() => onConversation(row.id)} key={row.id} style={{'--topic-color': row.provider === 'wechat' ? '#524765' : row.topic_color || '#888'} as React.CSSProperties}><span className="topic-dot"/><div><b>{row.title}</b><small>{row.account_display_name || row.account_name} · {row.provider === 'wechat' ? (row.conversation_type === 'group' ? '群聊' : '私聊') : `${row.provider.toUpperCase()} · ${row.dominant_topic || '未分类'}`}</small></div><strong>{row.provider === 'wechat' ? `${compact(row.outbound_messages)} 我发送 · ${compact(row.total_messages)} 消息` : `${compact(row.prompts)} 提示 · ${compact(row.total_visible_tokens)} Token`}</strong></button>)}</div></section>}
  </div>
}
