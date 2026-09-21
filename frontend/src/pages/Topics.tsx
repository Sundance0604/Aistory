import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { Topic, TopicTimeline } from '../types'

export function Topics() {
  const [topics, setTopics] = useState<Topic[]>([])
  const [timeline, setTimeline] = useState<TopicTimeline[]>([])
  const [metric, setMetric] = useState<'prompt_percent' | 'token_percent'>('token_percent')
  const [status, setStatus] = useState('')
  useEffect(() => { api<Topic[]>('/api/topics').then(setTopics); api<TopicTimeline[]>('/api/topics/timeline').then(setTimeline) }, [])
  const periods = useMemo(() => [...new Set(timeline.map((item) => item.period))].slice(-12), [timeline])
  const analyze = async () => { setStatus('已启动分析'); await api('/api/topics/classify-new', { method: 'POST', body: '{}' }) }
  return (
    <div className="page-stack">
      <header className="page-header"><div><span className="eyebrow">语义层 · 可选</span><h1>主题</h1></div><button className="primary" onClick={analyze}>分析新提示</button></header>
      <section className="panel">
        <div className="panel-heading"><div><span className="eyebrow">两级分类</span><h2>主题分布</h2></div><div className="segmented"><button className={metric === 'prompt_percent' ? 'active' : ''} onClick={() => setMetric('prompt_percent')}>按提示</button><button className={metric === 'token_percent' ? 'active' : ''} onClick={() => setMetric('token_percent')}>按 Token</button></div></div>
        {status && <p className="notice">{status}</p>}
        {topics.length ? <div className="topic-bars">{topics.map((topic) => <div key={topic.id}><header><b>{topic.name}</b><span>{topic[metric].toFixed(1)}%</span></header><i><span style={{ width: `${topic[metric]}%` }} /></i></div>)}</div> : <div className="empty"><h3>还没有主题数据</h3><p>确定性统计不需要 API。只有这里的语义分类会发送精简后的提示上下文。</p></div>}
      </section>
      <section className="panel"><div className="panel-heading"><div><span className="eyebrow">最近 12 个月</span><h2>兴趣演变</h2></div></div><div className="timeline-list">{periods.map((period) => { const items = timeline.filter((item) => item.period === period).sort((a,b) => b.token_share-a.token_share).slice(0,4); const total = items.reduce((sum,item)=>sum+item.token_share,0)||1; return <div key={period}><b>{period}</b><span>{items.map((item) => <i key={item.topic_id} style={{ width: `${item.token_share/total*100}%` }} title={`${item.topic}: ${Math.round(item.token_share)}`} />)}</span><small>{items.map((item)=>item.topic).join(' · ')}</small></div> })}</div></section>
    </div>
  )
}
