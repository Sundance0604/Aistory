import { useEffect, useState } from 'react'
import { Activity as ActivityIcon, Archive, ChartNoAxesColumn, LayoutDashboard, RefreshCw, Settings as SettingsIcon, Tags } from 'lucide-react'
import { api } from './api'
import { ActivityPage } from './pages/ActivityPage'
import { Conversations } from './pages/Conversations'
import { History } from './pages/History'
import { Lifecycle } from './pages/Lifecycle'
import { Overview } from './pages/Overview'
import { Settings } from './pages/Settings'
import { SyncPage } from './pages/SyncPage'
import { Topics } from './pages/Topics'
import type { Conversation, Day, Summary } from './types'

type Page = 'overview' | 'activity' | 'lifecycle' | 'history' | 'topics' | 'conversations' | 'sync' | 'settings'
const navigation: [Page, string, typeof LayoutDashboard][] = [
  ['overview', '概览', LayoutDashboard], ['activity', '活动', ActivityIcon], ['lifecycle', '生命周期', ChartNoAxesColumn], ['history', '纪录', ChartNoAxesColumn], ['topics', '主题', Tags], ['conversations', '对话', Archive], ['sync', '同步', RefreshCw], ['settings', '设置', SettingsIcon],
]

const emptySummary: Summary = { conversations: 0, prompts: 0, prompt_visible_tokens: 0, response_visible_tokens: 0, total_visible_tokens: 0, active_days: 0, first_activity: null, latest_activity: null }

export default function App() {
  const [page, setPage] = useState<Page>('overview')
  const [summary, setSummary] = useState<Summary>(emptySummary)
  const [days, setDays] = useState<Day[]>([])
  const [top, setTop] = useState<Conversation[]>([])
  const [conversationId, setConversationId] = useState<string>()
  const [error, setError] = useState('')
  const [provider, setProvider] = useState('')
  useEffect(() => {
    const query = `provider=${provider}`
    Promise.all([api<Summary>(`/api/summary?${query}`), api<Day[]>(`/api/activity/daily?${query}`), api<Conversation[]>(`/api/rankings/conversations?limit=10&${query}`)])
      .then(([s, d, c]) => { setSummary(s); setDays(d); setTop(c) })
      .catch((reason) => setError(reason.message))
  }, [provider])
  const openConversation = (id: string) => { setConversationId(id); setPage('conversations') }
  const content = page === 'overview' ? <Overview summary={summary} days={days} conversations={top} onConversation={openConversation} />
    : page === 'activity' ? <ActivityPage initial={days} provider={provider} onConversation={openConversation} />
    : page === 'lifecycle' ? <Lifecycle provider={provider} onConversation={openConversation} />
    : page === 'history' ? <History provider={provider} onConversation={openConversation} />
    : page === 'topics' ? <Topics provider={provider} />
    : page === 'conversations' ? <Conversations provider={provider} requestedId={conversationId} onClose={() => setConversationId(undefined)} />
    : page === 'sync' ? <SyncPage /> : <Settings />
  return (
    <div className={`app-shell theme-${provider || 'all'}`}>
      <aside className="sidebar">
        <div className="brand"><span>G</span><div><b>GPT Activity</b><small>LOCAL ARCHIVE</small></div></div>
        <nav>{navigation.map(([key, label, Icon]) => <button className={page === key ? 'active' : ''} onClick={() => { setPage(key); if (key !== 'conversations') setConversationId(undefined) }} key={key}><Icon size={18} /><span>{label}</span></button>)}</nav>
        <label className="provider-switch"><span>显示平台</span><select value={provider} onChange={event => setProvider(event.target.value)}><option value="">全部</option><option value="chatgpt">ChatGPT</option><option value="gemini">Gemini</option></select></label>
        <div className="local-status"><i /><span><b>仅本地</b><small>{summary.conversations} 个对话</small></span></div>
      </aside>
      <main>{error ? <div className="error-banner"><b>无法读取本地 API</b><span>{error}</span></div> : content}</main>
    </div>
  )
}
