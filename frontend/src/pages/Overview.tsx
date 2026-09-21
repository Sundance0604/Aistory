import { Activity, CalendarDays, MessageSquareText, Orbit } from 'lucide-react'
import { compact, dateLabel } from '../api'
import { ActivityHeatmap } from '../components/ActivityHeatmap'
import { MetricCard } from '../components/MetricCard'
import { MiniBars } from '../components/MiniBars'
import type { Conversation, Day, Summary } from '../types'

export function Overview({ summary, days, conversations, onConversation }: { summary: Summary; days: Day[]; conversations: Conversation[]; onConversation: (id: string) => void }) {
  return (
    <div className="page-stack">
      <header className="page-header">
        <div><span className="eyebrow">你的本地 ChatGPT 档案</span><h1>概览</h1></div>
        <p>从可见对话文本计算。数据保留在本机。</p>
      </header>
      <div className="metric-grid">
        <MetricCard label="估算可见 Token" value={compact(summary.total_visible_tokens)} note={`${compact(summary.prompt_visible_tokens)} 输入 · ${compact(summary.response_visible_tokens)} 输出`} icon={<Orbit size={18} />} />
        <MetricCard label="提示" value={compact(summary.prompts)} note="主动分支中的用户回合" icon={<MessageSquareText size={18} />} />
        <MetricCard label="对话" value={compact(summary.conversations)} note={`${dateLabel(summary.first_activity)} 起`} icon={<Activity size={18} />} />
        <MetricCard label="活跃天数" value={compact(summary.active_days)} note={`最近 ${dateLabel(summary.latest_activity)}`} icon={<CalendarDays size={18} />} />
      </div>
      <ActivityHeatmap data={days} />
      <div className="two-column">
        <section className="panel">
          <div className="panel-heading"><div><span className="eyebrow">近期</span><h2>90 天趋势</h2></div></div>
          <MiniBars data={days} />
        </section>
        <section className="panel">
          <div className="panel-heading"><div><span className="eyebrow">排名</span><h2>最多可见 Token</h2></div></div>
          <div className="compact-list">
            {conversations.slice(0, 5).map((item, index) => (
              <button onClick={() => onConversation(item.id)} key={item.id}>
                <span className="rank">{String(index + 1).padStart(2, '0')}</span>
                <span className="grow"><b>{item.title}</b><small>{compact(item.prompts)} 个提示</small></span>
                <strong>{compact(item.total_visible_tokens)}</strong>
              </button>
            ))}
          </div>
        </section>
      </div>
    </div>
  )
}
