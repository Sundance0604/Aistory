import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { Topic, TopicTimeline } from '../types'

export function Topics({ provider, job, onJobChange, onCancel }: { provider: string; job: any; onJobChange: (job:any)=>void; onCancel: ()=>void }) {
  const [topics, setTopics] = useState<Topic[]>([])
  const [timeline, setTimeline] = useState<TopicTimeline[]>([])
  const [metric, setMetric] = useState<'prompt_percent' | 'token_percent'>('token_percent')
  const [status, setStatus] = useState('')
  useEffect(() => { api<Topic[]>(`/api/topics?provider=${provider}`).then(setTopics); api<TopicTimeline[]>(`/api/topics/timeline?provider=${provider}`).then(setTimeline) }, [provider])
  const periods = useMemo(() => [...new Set(timeline.map((item) => item.period))].slice(-12), [timeline])
  const active = job?.kind === 'topics' && ['running', 'cancelling'].includes(job.status)
  const analyze = async () => {
    try {
      const started = await api<any>('/api/topics/classify-new', { method: 'POST', body: '{}' })
      onJobChange(started)
      setStatus('已启动分析；切换页面后仍可在右下角查看或中止。')
    } catch (reason) {
      setStatus(reason instanceof Error ? reason.message : String(reason))
    }
  }
  return (
    <div className="page-stack">
      <header className="page-header"><div><span className="eyebrow">语义层 · 可选</span><h1>主题</h1></div><div className="button-row"><button className="primary" disabled={active} onClick={analyze}>分析新提示</button>{active&&<button className="cancel-button" disabled={job.status==='cancelling'} onClick={onCancel}>{job.status==='cancelling'?'正在停止…':'中止分析'}</button>}</div></header>
      <section className="panel">
        <div className="panel-heading"><div><span className="eyebrow">两级分类</span><h2>主题分布</h2></div><div className="segmented"><button className={metric === 'prompt_percent' ? 'active' : ''} onClick={() => setMetric('prompt_percent')}>按提示</button><button className={metric === 'token_percent' ? 'active' : ''} onClick={() => setMetric('token_percent')}>按 Token</button></div></div>
        {status && <p className="notice">{status}</p>}
        {topics.length ? <div className="topic-bars">{topics.map((topic) => <div key={topic.id}><header><b>{topic.name}</b><span>{topic[metric].toFixed(1)}%</span></header><i><span style={{ width: `${topic[metric]}%`, background: topic.color }} /></i></div>)}</div> : <div className="empty"><h3>还没有主题数据</h3><p>确定性统计不需要 API。只有这里的语义分类会发送精简后的提示上下文。</p></div>}
      </section>
      <section className="panel"><div className="panel-heading"><div><span className="eyebrow">最近 12 个月</span><h2>兴趣演变</h2></div></div><div className="timeline-list">{periods.map((period) => { const items = timeline.filter((item) => item.period === period).sort((a,b) => b.token_share-a.token_share).slice(0,4); const total = items.reduce((sum,item)=>sum+item.token_share,0)||1; return <div key={period}><b>{period}</b><span>{items.map((item) => <i key={item.topic_id} style={{ width: `${item.token_share/total*100}%`, background: item.topic_color }} title={`${item.topic}: ${Math.round(item.token_share)}`} />)}</span><small>{items.map((item)=>item.topic).join(' · ')}</small></div> })}</div></section>
    </div>
  )
}
