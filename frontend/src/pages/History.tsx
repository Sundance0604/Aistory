import { useEffect, useState } from 'react'
import { api, compact } from '../api'
import type { Conversation } from '../types'

const aiSorts = [
  ['total_visible_tokens', '总 Token'], ['prompt_visible_tokens', '输入 Token'], ['response_visible_tokens', '输出 Token'], ['prompts', '提示数'], ['turns', '最长对话'],
]
const wechatSorts = [['total_messages', '总消息'], ['outbound_messages', '我发送'], ['inbound_messages', '收到'], ['active_days', '活跃日'], ['calendar_span_days', '生命周期']]

export function History({ provider, onConversation }: { provider: string; onConversation: (id: string) => void }) {
  const [sort, setSort] = useState(provider === 'wechat' ? 'total_messages' : 'total_visible_tokens')
  const [rows, setRows] = useState<Conversation[]>([])
  const [records, setRecords] = useState<any>(null)
  useEffect(() => { setSort(provider === 'wechat' ? 'total_messages' : 'total_visible_tokens') }, [provider])
  useEffect(() => { api<Conversation[]>(`/api/rankings/conversations?sort=${sort}&limit=100&provider=${provider}`).then(setRows); api(`/api/records?provider=${provider}`).then(setRecords) }, [sort, provider])
  const isWechat = provider === 'wechat'
  const messageMode = isWechat
  const sorts = isWechat ? wechatSorts : aiSorts
  return (
    <div className="page-stack">
      <header className="page-header"><div><span className="eyebrow">历史排名</span><h1>纪录</h1></div><p>点击任意对话查看主动分支详情。</p></header>
      <section className="panel">
        <div className="segmented wide">{sorts.map(([value, label]) => <button className={sort === value ? 'active' : ''} onClick={() => setSort(value)} key={value}>{label}</button>)}</div>
        <div className="ranking-table">
          <div className="ranking-head"><span>#</span><span>对话</span><span>{isWechat ? '我发送' : '提示'}</span><span>{isWechat ? '收到' : '输入'}</span><span>{messageMode ? '活跃日' : '输出'}</span><span>{messageMode ? '总事件' : '总计'}</span></div>
          {rows.map((item, index) => <button className="ranking-row" onClick={() => onConversation(item.id)} key={item.id}><span>{index + 1}</span><span><b>{item.title}</b><small>{item.provider === 'wechat' ? `${item.conversation_type === 'group' ? '群聊' : '私聊'} · ${item.account_display_name || item.account_name}` : `${item.provider.toUpperCase()} · ${item.model_hint || '模型未知'}`}</small></span><span>{compact(messageMode ? item.outbound_messages : item.prompts)}</span><span>{compact(messageMode ? item.inbound_messages : item.prompt_visible_tokens)}</span><span>{compact(messageMode ? (item.active_days || 0) : item.response_visible_tokens)}</span><strong>{compact(messageMode ? item.total_messages : item.total_visible_tokens)}</strong></button>)}
        </div>
      </section>
      {records && <div className="two-column"><section className="panel record"><span className="eyebrow">{messageMode ? '交互最多的一天' : '最多提示的一天'}</span><h2>{(messageMode ? records.busiest_day_by_messages : records.busiest_day_by_prompts)?.date || '—'}</h2><p>{compact(messageMode ? records.busiest_day_by_messages?.total_messages || 0 : records.busiest_day_by_prompts?.prompts || 0)} {messageMode ? '个事件' : '个提示'}</p></section><section className="panel record"><span className="eyebrow">{messageMode ? '生命周期最长的对话' : '最多 Token 的一天'}</span><h2>{messageMode ? records.longest_lifecycle?.title || '—' : records.busiest_day_by_tokens?.date || '—'}</h2><p>{compact(messageMode ? records.longest_lifecycle?.calendar_span_days || 0 : records.busiest_day_by_tokens?.total_visible_tokens || 0)} {messageMode ? '天' : '可见 Token'}</p></section></div>}
    </div>
  )
}
