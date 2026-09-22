import React, { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { api, compact, dateLabel } from '../api'
import type { ConversationDetail } from '../types'

export function ConversationDrawer({ conversationId, onClose }: { conversationId?: string; onClose: () => void }) {
  const [detail, setDetail] = useState<ConversationDetail | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    setDetail(null)
    setError('')
    if (!conversationId) return
    api<ConversationDetail>(`/api/conversations/${encodeURIComponent(conversationId)}`)
      .then(setDetail)
      .catch((reason) => setError(reason.message))
  }, [conversationId])

  useEffect(() => {
    if (!conversationId) return
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [conversationId, onClose])

  if (!conversationId) return null
  return <div className="drawer-backdrop" onClick={onClose}>
    <aside className="drawer" aria-label="对话详情" onClick={(event) => event.stopPropagation()}>
      <button className="icon-button close" aria-label="关闭" onClick={onClose}><X /></button>
      {!detail && !error && <div className="empty"><p>正在读取对话…</p></div>}
      {error && <div className="empty error"><h3>无法读取对话</h3><p>{error}</p></div>}
      {detail && <>
        <span className="eyebrow">{detail.account_name} · {detail.provider.toUpperCase()} · {dateLabel(detail.created_at)}</span>
        <h2>{detail.title}</h2>
        <div className="drawer-metrics"><span><b>{compact(detail.prompts)}</b> 提示</span><span><b>{compact(detail.total_visible_tokens)}</b> Token</span></div>
        <div className="topic-chips">{detail.topics?.map(topic => <span style={{'--topic-color': topic.color} as React.CSSProperties} key={topic.id}>{topic.name}</span>)}</div>
        <div className="messages">{detail.messages.filter((message) => message.role === 'user' || message.visible_text).map((message) => <article className={message.role} key={message.id}><header><b>{message.role === 'user' ? '你' : detail.provider === 'gemini' ? 'Gemini' : 'ChatGPT'}</b><span>{compact(message.visible_tokens)} tokens</span></header><p>{message.visible_text || (message.has_attachment ? '［非文本输入/附件］' : '［无可见文本］')}</p></article>)}</div>
      </>}
    </aside>
  </div>
}
