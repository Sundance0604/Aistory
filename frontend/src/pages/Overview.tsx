import { Activity, CalendarDays, MessageSquareText, Orbit } from 'lucide-react'
import { compact, dateLabel } from '../api'
import { ActivityHeatmap } from '../components/ActivityHeatmap'
import { MetricCard } from '../components/MetricCard'
import { MiniBars } from '../components/MiniBars'
import type { Conversation, Day, Summary } from '../types'

export function Overview({ summary, days, conversations, provider, accountId, onConversation }: { summary: Summary; days: Day[]; conversations: Conversation[]; provider: string; accountId: string; onConversation: (id: string) => void }) {
  const isWechat = provider === 'wechat'
  return (
    <div className="page-stack">
      <header className="page-header">
        <div><span className="eyebrow">你的本地数字历史档案</span><h1>概览</h1></div>
        <p>从可见对话文本计算。数据保留在本机。</p>
      </header>
      <div className="metric-grid">
        {isWechat ? <>
          <MetricCard label="聊天" value={compact(summary.conversations)} note={`${dateLabel(summary.first_activity)} 起`} icon={<Activity size={18} />} />
          <MetricCard label="我发送" value={compact(summary.outbound_messages)} note="主动发送的消息" icon={<MessageSquareText size={18} />} />
          <MetricCard label="收到" value={compact(summary.inbound_messages)} note="收到的聊天消息" icon={<Orbit size={18} />} />
          <MetricCard label="总消息" value={compact(summary.total_messages)} note="发送与收到" icon={<MessageSquareText size={18} />} />
        </> : <>
          <MetricCard label="估算可见 Token" value={compact(summary.total_visible_tokens)} note={`${compact(summary.prompt_visible_tokens)} 输入 · ${compact(summary.response_visible_tokens)} 输出`} icon={<Orbit size={18} />} />
          <MetricCard label="提示" value={compact(summary.prompts)} note="主动分支中的用户回合" icon={<MessageSquareText size={18} />} />
          <MetricCard label="对话" value={compact(summary.conversations)} note={`${dateLabel(summary.first_activity)} 起`} icon={<Activity size={18} />} />
        </>}
        <MetricCard label="活跃天数" value={compact(summary.active_days)} note={`最近 ${dateLabel(summary.latest_activity)}`} icon={<CalendarDays size={18} />} />
      </div>
      <ActivityHeatmap data={days} provider={provider} accountId={accountId} onConversation={onConversation} />
      <div className="two-column">
        <section className="panel">
          <div className="panel-heading"><div><span className="eyebrow">完整历史</span><h2>活动趋势</h2></div></div>
          <MiniBars data={days} />
        </section>
        <section className="panel">
          <div className="panel-heading"><div><span className="eyebrow">排名</span><h2>{isWechat ? '消息最多' : '最多可见 Token'}</h2></div></div>
          <div className="compact-list">
            {conversations.slice(0, 5).map((item, index) => (
              <button onClick={() => onConversation(item.id)} key={item.id}>
                <span className="rank">{String(index + 1).padStart(2, '0')}</span>
                <span className="grow"><b>{item.title}</b><small>{isWechat ? `${compact(item.outbound_messages)} 我发送` : `${compact(item.prompts)} 个提示`}</small></span>
                <strong>{compact(isWechat ? item.total_messages : item.total_visible_tokens)}</strong>
              </button>
            ))}
          </div>
        </section>
      </div>
    </div>
  )
}
