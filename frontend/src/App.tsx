import { useEffect, useState } from 'react'
import { Activity as ActivityIcon, Archive, ChartNoAxesColumn, LayoutDashboard, RefreshCw, Settings as SettingsIcon, Tags } from 'lucide-react'
import { api } from './api'
import { ActivityPage } from './pages/ActivityPage'
import { Conversations } from './pages/Conversations'
import { History } from './pages/History'
import { Overview } from './pages/Overview'
import { Settings } from './pages/Settings'
import { SyncPage } from './pages/SyncPage'
import { Topics } from './pages/Topics'
import type { Conversation, Day, Summary } from './types'

type Page = 'overview' | 'activity' | 'history' | 'topics' | 'conversations' | 'sync' | 'settings'
const navigation: [Page, string, typeof LayoutDashboard][] = [
  ['overview', '概览', LayoutDashboard], ['activity', '活动', ActivityIcon], ['history', '纪录', ChartNoAxesColumn], ['topics', '主题', Tags], ['conversations', '对话', Archive], ['sync', '同步', RefreshCw], ['settings', '设置', SettingsIcon],
]

const emptySummary: Summary = { conversations: 0, prompts: 0, prompt_visible_tokens: 0, response_visible_tokens: 0, total_visible_tokens: 0, active_days: 0, first_activity: null, latest_activity: null }

export default function App() {
  const [page, setPage] = useState<Page>('overview')
  const [summary, setSummary] = useState<Summary>(emptySummary)
  const [days, setDays] = useState<Day[]>([])
  const [top, setTop] = useState<Conversation[]>([])
  const [conversationId, setConversationId] = useState<string>()
  const [error, setError] = useState('')
  useEffect(() => {
    Promise.all([api<Summary>('/api/summary'), api<Day[]>('/api/activity/daily'), api<Conversation[]>('/api/rankings/conversations?limit=10')])
      .then(([s, d, c]) => { setSummary(s); setDays(d); setTop(c) })
      .catch((reason) => setError(reason.message))
  }, [])
  const openConversation = (id: string) => { setConversationId(id); setPage('conversations') }
  const content = page === 'overview' ? <Overview summary={summary} days={days} conversations={top} onConversation={openConversation} />
    : page === 'activity' ? <ActivityPage initial={days} />
    : page === 'history' ? <History onConversation={openConversation} />
    : page === 'topics' ? <Topics />
    : page === 'conversations' ? <Conversations requestedId={conversationId} onClose={() => setConversationId(undefined)} />
    : page === 'sync' ? <SyncPage /> : <Settings />
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span>G</span><div><b>GPT Activity</b><small>LOCAL ARCHIVE</small></div></div>
        <nav>{navigation.map(([key, label, Icon]) => <button className={page === key ? 'active' : ''} onClick={() => { setPage(key); if (key !== 'conversations') setConversationId(undefined) }} key={key}><Icon size={18} /><span>{label}</span></button>)}</nav>
        <div className="local-status"><i /><span><b>仅本地</b><small>{summary.conversations} 个对话</small></span></div>
      </aside>
      <main>{error ? <div className="error-banner"><b>无法读取本地 API</b><span>{error}</span></div> : content}</main>
    </div>
  )
}
