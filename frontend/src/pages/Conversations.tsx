import React, { useEffect, useState } from 'react'
import { Search } from 'lucide-react'
import { api, compact, dateLabel } from '../api'
import type { Account, Conversation, Topic } from '../types'

export function Conversations({ provider, onConversation }: { provider: string; onConversation: (id: string) => void }) {
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('updated_at')
  const [accountId, setAccountId] = useState('')
  const [accounts, setAccounts] = useState<Account[]>([])
  const [topics, setTopics] = useState<Topic[]>([])
  const [topicId, setTopicId] = useState('')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [rows, setRows] = useState<Conversation[]>([])
  const [loadError, setLoadError] = useState('')
  useEffect(() => { api<Account[]>('/api/accounts').then(setAccounts) }, [])
  useEffect(() => { api<Topic[]>(`/api/topics?level=1&provider=${encodeURIComponent(provider)}`).then(setTopics) }, [provider])
  useEffect(() => {
    const timer = setTimeout(() => {
      const params = new URLSearchParams({ sort, limit: '500' })
      if (search.trim()) params.set('search', search.trim())
      if (accountId) params.set('account_id', accountId)
      if (provider) params.set('provider', provider)
      if (topicId) params.set('topic_id', topicId)
      setLoadError('')
      api<Conversation[]>(`/api/conversations?${params}`).then(setRows).catch((reason) => setLoadError(reason instanceof Error ? reason.message : String(reason)))
    }, 150)
    return () => clearTimeout(timer)
  }, [search, sort, accountId, provider, topicId])
  const filteredRows = rows.filter((item) => {
    const day = (item.updated_at || item.created_at || '').slice(0, 10)
    return (!fromDate || day >= fromDate) && (!toDate || day <= toDate)
  })
  return (
    <div className="page-stack">
      <header className="page-header"><div><span className="eyebrow">本地档案</span><h1>对话</h1></div><p>{filteredRows.length} 条匹配结果</p></header>
      <section className="panel toolbar"><label className="search"><Search size={17} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索标题或对话内容…" /></label><select aria-label="主题" value={topicId} onChange={(event) => setTopicId(event.target.value)}><option value="">全部主题</option>{topics.map((topic) => <option value={topic.id} key={topic.id}>{topic.name}</option>)}</select><select aria-label="账号" value={accountId} onChange={(event) => setAccountId(event.target.value)}><option value="">全部账号</option>{accounts.map((account) => <option value={account.id} key={account.id}>{account.name}</option>)}</select><div className="date-filter"><label>从<input aria-label="开始日期" type="date" value={fromDate} onChange={(event) => setFromDate(event.target.value)} /></label><label>至<input aria-label="结束日期" type="date" value={toDate} onChange={(event) => setToDate(event.target.value)} /></label></div><select value={sort} onChange={(event) => setSort(event.target.value)}><option value="updated_at">最近更新</option><option value="created_at">创建日期</option><option value="prompts">提示数</option><option value="total_visible_tokens">可见 Token</option></select></section>
      {loadError && <p className="error filter-error">筛选失败：{loadError}</p>}
      <section className="conversation-grid">{filteredRows.map((item) => <button className="conversation-card" onClick={() => onConversation(item.id)} key={item.id}><div><span>{dateLabel(item.updated_at)}</span><span>{item.account_name} · {item.provider === 'gemini' ? 'Gemini' : (item.model_hint || 'ChatGPT')}</span></div><h3>{item.title}</h3><div className="topic-chips">{item.topics?.map(topic => <span style={{'--topic-color': topic.color} as React.CSSProperties} key={topic.id}>{topic.name}</span>)}</div><footer><b>{compact(item.total_visible_tokens)} <small>Token</small></b><b>{compact(item.prompts)} <small>提示</small></b></footer></button>)}</section>
    </div>
  )
}
