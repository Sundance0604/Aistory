import { useEffect, useState } from 'react'
import { api, compact } from '../api'
import type { Conversation } from '../types'

const sorts = [
  ['total_visible_tokens', '总 Token'], ['prompt_visible_tokens', '输入 Token'], ['response_visible_tokens', '输出 Token'], ['prompts', '提示数'], ['turns', '最长对话'],
]

export function History({ provider, onConversation }: { provider: string; onConversation: (id: string) => void }) {
  const [sort, setSort] = useState('total_visible_tokens')
  const [rows, setRows] = useState<Conversation[]>([])
  const [records, setRecords] = useState<any>(null)
  useEffect(() => { api<Conversation[]>(`/api/rankings/conversations?sort=${sort}&limit=100&provider=${provider}`).then(setRows); api(`/api/records?provider=${provider}`).then(setRecords) }, [sort, provider])
  return (
    <div className="page-stack">
      <header className="page-header"><div><span className="eyebrow">历史排名</span><h1>纪录</h1></div><p>点击任意对话查看主动分支详情。</p></header>
      <section className="panel">
        <div className="segmented wide">{sorts.map(([value, label]) => <button className={sort === value ? 'active' : ''} onClick={() => setSort(value)} key={value}>{label}</button>)}</div>
        <div className="ranking-table">
          <div className="ranking-head"><span>#</span><span>对话</span><span>提示</span><span>输入</span><span>输出</span><span>总计</span></div>
          {rows.map((item, index) => <button className="ranking-row" onClick={() => onConversation(item.id)} key={item.id}><span>{index + 1}</span><span><b>{item.title}</b><small>{item.provider.toUpperCase()} · {item.model_hint || '模型未知'}</small></span><span>{compact(item.prompts)}</span><span>{compact(item.prompt_visible_tokens)}</span><span>{compact(item.response_visible_tokens)}</span><strong>{compact(item.total_visible_tokens)}</strong></button>)}
        </div>
      </section>
      {records && <div className="two-column"><section className="panel record"><span className="eyebrow">最多提示的一天</span><h2>{records.busiest_day_by_prompts?.date || '—'}</h2><p>{compact(records.busiest_day_by_prompts?.prompts || 0)} 个提示</p></section><section className="panel record"><span className="eyebrow">最多 Token 的一天</span><h2>{records.busiest_day_by_tokens?.date || '—'}</h2><p>{compact(records.busiest_day_by_tokens?.total_visible_tokens || 0)} 可见 Token</p></section></div>}
    </div>
  )
}
