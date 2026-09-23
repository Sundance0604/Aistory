import { useEffect, useState } from 'react'
import { Activity as ActivityIcon, Archive, ChartNoAxesColumn, CheckCircle2, Clock3, LayoutDashboard, LoaderCircle, RefreshCw, Settings as SettingsIcon, Tags, X } from 'lucide-react'
import { api, scopeQuery } from './api'
import { ActivityPage } from './pages/ActivityPage'
import { ConversationDrawer } from './components/ConversationDrawer'
import { Conversations } from './pages/Conversations'
import { History } from './pages/History'
import { Lifecycle } from './pages/Lifecycle'
import { Overview } from './pages/Overview'
import { Settings } from './pages/Settings'
import { SyncPage } from './pages/SyncPage'
import { Topics } from './pages/Topics'
import { UsageTime } from './pages/UsageTime'
import type { Account, Conversation, Day, Summary } from './types'

type Page = 'overview' | 'activity' | 'usage' | 'lifecycle' | 'history' | 'topics' | 'conversations' | 'sync' | 'settings'
const navigation: [Page, string, typeof LayoutDashboard][] = [
  ['overview', '概览', LayoutDashboard], ['activity', '活动', ActivityIcon], ['usage', '时长', Clock3], ['lifecycle', '生命周期', ChartNoAxesColumn], ['history', '纪录', ChartNoAxesColumn], ['topics', '主题', Tags], ['conversations', '对话', Archive], ['sync', '同步', RefreshCw], ['settings', '设置', SettingsIcon],
]

const emptySummary: Summary = { conversations: 0, prompts: 0, outbound_messages: 0, inbound_messages: 0, total_messages: 0, prompt_visible_tokens: 0, response_visible_tokens: 0, total_visible_tokens: 0, active_days: 0, first_activity: null, latest_activity: null }

export default function App() {
  const [page, setPage] = useState<Page>('overview')
  const [summary, setSummary] = useState<Summary>(emptySummary)
  const [days, setDays] = useState<Day[]>([])
  const [top, setTop] = useState<Conversation[]>([])
  const [conversationId, setConversationId] = useState<string>()
  const [error, setError] = useState('')
  const [scope, setScope] = useState('')
  const [providers, setProviders] = useState<{id:string;label:string;supports_topics:boolean}[]>([])
  const [accounts, setAccounts] = useState<Account[]>([])
  const [job, setJob] = useState<any>({ status: 'idle' })
  const [jobNoticeDismissed, setJobNoticeDismissed] = useState(false)
  const accountId = scope.startsWith('wechat:') ? scope.slice('wechat:'.length) : ''
  const provider = accountId ? 'wechat' : scope
  useEffect(() => {
    const query = scopeQuery(provider, accountId)
    Promise.all([api<Summary>(`/api/summary?${query}`), api<Day[]>(`/api/activity/daily?${query}`), api<Conversation[]>(`/api/rankings/conversations?limit=10&sort=${provider === 'wechat' ? 'total_messages' : 'total_visible_tokens'}&${query}`)])
      .then(([s, d, c]) => { setSummary(s); setDays(d); setTop(c) })
      .catch((reason) => setError(reason.message))
  }, [provider, accountId])
  useEffect(() => {
    api<{id:string;label:string;supports_topics:boolean}[]>('/api/providers').then(setProviders).catch(() => undefined)
    api<Account[]>('/api/accounts').then(setAccounts).catch(() => undefined)
  }, [])
  useEffect(() => {
    const refreshJob = () => api<any>('/api/jobs/status').then(setJob).catch(() => undefined)
    refreshJob()
    const timer = setInterval(refreshJob, 1200)
    return () => clearInterval(timer)
  }, [])
  useEffect(() => { if (job.kind === 'topics' && job.status === 'running') setJobNoticeDismissed(false) }, [job.kind, job.status])
  const cancelTopics = async () => {
    const response = await api<any>('/api/jobs/cancel', { method: 'POST', body: '{}' })
    setJob((current:any) => ({ ...current, ...response }))
  }
  const openConversation = (id: string) => setConversationId(id)
  const content = page === 'overview' ? <Overview summary={summary} days={days} conversations={top} provider={provider} accountId={accountId} onConversation={openConversation} />
    : page === 'activity' ? <ActivityPage initial={days} provider={provider} accountId={accountId} onConversation={openConversation} />
    : page === 'usage' ? <UsageTime provider={provider} accountId={accountId} />
    : page === 'lifecycle' ? <Lifecycle provider={provider} accountId={accountId} onConversation={openConversation} />
    : page === 'history' ? <History provider={provider} accountId={accountId} onConversation={openConversation} />
    : page === 'topics' ? <Topics provider={provider} accountId={accountId} job={job} onJobChange={setJob} onCancel={cancelTopics} />
    : page === 'conversations' ? <Conversations provider={provider} scopeAccountId={accountId} onConversation={openConversation} />
    : page === 'sync' ? <SyncPage onOpenSettings={() => setPage('settings')} /> : <Settings />
  return (
    <div className={`app-shell theme-${provider || 'all'}`}>
      <aside className="sidebar">
        <div className="brand"><span>A</span><div><b>Aistory</b><small>LOCAL DIGITAL HISTORY</small></div></div>
        <nav>{navigation.map(([key, label, Icon]) => <button className={page === key ? 'active' : ''} onClick={() => { setPage(key); setConversationId(undefined) }} key={key}><Icon size={18} /><span>{label}</span></button>)}</nav>
        <label className="provider-switch"><span>显示平台 / 账号</span><select value={scope} onChange={event => setScope(event.target.value)}><optgroup label="AI"><option value="">全部 AI</option>{providers.filter(item => item.id !== 'wechat').map(item => <option value={item.id} key={item.id}>{item.label}</option>)}</optgroup><optgroup label="微信"><option value="wechat">全部微信</option>{accounts.filter(item => item.provider === 'wechat').map(item => <option value={`wechat:${item.id}`} key={item.id}>{item.display_name || item.name}</option>)}</optgroup></select></label>
        <div className="local-status"><i /><span><b>仅本地</b><small>{summary.conversations} 个对话</small></span></div>
      </aside>
      <main>{error ? <div className="error-banner"><b>无法读取本地 API</b><span>{error}</span></div> : content}</main>
      {job.kind === 'topics' && !jobNoticeDismissed && ['running','cancelling','cancelled'].includes(job.status) && <div className={`global-job-progress ${job.status}`} role="status" aria-live="polite"><header>{job.status==='cancelled'?<CheckCircle2 size={17}/>:<LoaderCircle className="spinning" size={17}/>}<b>{job.status==='running'?'正在分析主题':job.status==='cancelling'?'正在停止…':'分析已中止'}</b><span>{job.progress?.total == null ? '准备中' : `${job.progress.processed || 0} / ${job.progress.total}`}</span>{job.status==='cancelled'&&<button className="job-close" aria-label="关闭" onClick={()=>setJobNoticeDismissed(true)}><X size={14}/></button>}</header><div><i style={{width:`${job.progress?.percent || 0}%`}} /></div><small>{job.status==='cancelled'?`已完成 ${job.result?.classified || 0} / ${job.result?.queued || job.progress?.total || 0} · 剩余 ${job.result?.remaining || 0} 条`:`已分类 ${job.progress?.classified || 0}${job.progress?.failed ? ` · 失败 ${job.progress.failed}` : ''} · 可继续浏览其他页面`}</small>{job.status==='running'&&<button className="job-cancel" onClick={cancelTopics}>中止分析</button>}{job.status==='cancelling'&&<button className="job-cancel" disabled>正在停止…</button>}</div>}
      <ConversationDrawer conversationId={conversationId} onClose={() => setConversationId(undefined)} />
    </div>
  )
}
