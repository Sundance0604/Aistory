import { useEffect, useState } from 'react'
import { Search, X } from 'lucide-react'
import { api, compact, dateLabel } from '../api'
import type { Account, Conversation, ConversationDetail } from '../types'

export function Conversations({ requestedId, onClose }: { requestedId?: string; onClose?: () => void }) {
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('updated_at')
  const [accountId, setAccountId] = useState('')
  const [accounts, setAccounts] = useState<Account[]>([])
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [rows, setRows] = useState<Conversation[]>([])
  const [detail, setDetail] = useState<ConversationDetail | null>(null)
  useEffect(() => { api<Account[]>('/api/accounts').then(setAccounts) }, [])
  useEffect(() => { const timer = setTimeout(() => api<Conversation[]>(`/api/conversations?sort=${sort}&limit=200&search=${encodeURIComponent(search)}&account_id=${encodeURIComponent(accountId)}`).then(setRows), 150); return () => clearTimeout(timer) }, [search, sort, accountId])
  useEffect(() => { if (requestedId) api<ConversationDetail>(`/api/conversations/${requestedId}`).then(setDetail) }, [requestedId])
  const open = (id: string) => api<ConversationDetail>(`/api/conversations/${id}`).then(setDetail)
  const filteredRows = rows.filter((item) => {
    const day = (item.updated_at || item.created_at || '').slice(0, 10)
    return (!fromDate || day >= fromDate) && (!toDate || day <= toDate)
  })
  return (
    <div className="page-stack">
      <header className="page-header"><div><span className="eyebrow">本地档案</span><h1>对话</h1></div><p>{filteredRows.length} 条匹配结果</p></header>
      <section className="panel toolbar"><label className="search"><Search size={17} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索标题…" /></label><select aria-label="账号" value={accountId} onChange={(event) => setAccountId(event.target.value)}><option value="">全部账号</option>{accounts.map((account) => <option value={account.id} key={account.id}>{account.name}</option>)}</select><div className="date-filter"><label>从<input aria-label="开始日期" type="date" value={fromDate} onChange={(event) => setFromDate(event.target.value)} /></label><label>至<input aria-label="结束日期" type="date" value={toDate} onChange={(event) => setToDate(event.target.value)} /></label></div><select value={sort} onChange={(event) => setSort(event.target.value)}><option value="updated_at">最近更新</option><option value="created_at">创建日期</option><option value="prompts">提示数</option><option value="total_visible_tokens">可见 Token</option></select></section>
      <section className="conversation-grid">{filteredRows.map((item) => <button className="conversation-card" onClick={() => open(item.id)} key={item.id}><div><span>{dateLabel(item.updated_at)}</span><span>{item.account_name} · {item.model_hint || 'ChatGPT'}</span></div><h3>{item.title}</h3><footer><b>{compact(item.total_visible_tokens)} <small>Token</small></b><b>{compact(item.prompts)} <small>提示</small></b></footer></button>)}</section>
      {detail && <div className="drawer-backdrop" onClick={() => { setDetail(null); onClose?.() }}><aside className="drawer" onClick={(event) => event.stopPropagation()}><button className="icon-button close" onClick={() => { setDetail(null); onClose?.() }}><X /></button><span className="eyebrow">{detail.account_name} · {dateLabel(detail.created_at)}</span><h2>{detail.title}</h2><div className="drawer-metrics"><span><b>{compact(detail.prompts)}</b> 提示</span><span><b>{compact(detail.total_visible_tokens)}</b> Token</span></div><div className="messages">{detail.messages.filter((message) => message.role === 'user' || message.visible_text).map((message) => <article className={message.role} key={message.id}><header><b>{message.role === 'user' ? '你' : 'ChatGPT'}</b><span>{compact(message.visible_tokens)} tokens</span></header><p>{message.visible_text || (message.has_attachment ? '［附件提示］' : '［无可见文本］')}</p></article>)}</div></aside></div>}
    </div>
  )
}
