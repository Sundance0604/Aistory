import { useEffect, useMemo, useState } from 'react'
import { api, compact, dateLabel, number } from '../api'

type Candidate = { model_family: string; component_or_state_count: number; feature_set: string; log_likelihood: number; aic: number; bic: number; selected_by_aic: number; selected_by_bic: number }
type Session = { model_family: 'gmm' | 'hmm'; session_index: number; start_at: string; end_at: string; event_count: number; session_span_seconds: number; estimated_usage_seconds: number }

function duration(seconds?: number | null) {
  if (seconds == null) return '—'
  const minutes = Math.round(seconds / 60)
  const hours = Math.floor(minutes / 60)
  return hours ? `${hours} 小时 ${minutes % 60} 分` : `${minutes} 分钟`
}

function shortDuration(seconds: number) {
  if (seconds < 60) return `${Math.round(seconds)} 秒`
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)} 小时`
  return `${(seconds / 86400).toFixed(1)} 天`
}

function Histogram({ rows }: { rows: any[] }) {
  const max = Math.max(...rows.map(row => row.count), 1)
  return <div className="usage-chart-scroll"><svg className="usage-histogram" viewBox="0 0 900 230" role="img" aria-label="提示间隔的对数分布">
    <line x1="38" y1="190" x2="880" y2="190" />
    {rows.map((row, index) => { const width = 842 / Math.max(rows.length, 1); const height = row.count / max * 160; return <g key={index}><rect x={38 + index * width} y={190 - height} width={Math.max(2, width - 2)} height={height}><title>{shortDuration(row.seconds_start)}–{shortDuration(row.seconds_end)}：{row.count}</title></rect></g> })}
    <text x="38" y="218">短间隔</text><text x="810" y="218">长间隔</text>
  </svg></div>
}

function Scatter({ rows, field, label }: { rows: any[]; field: 'previous_output_tokens' | 'next_input_tokens'; label: string }) {
  const points = rows.map(row => ({ x: Math.log1p(row[field]), y: Math.log1p(row.gap_seconds) }))
  const maxX = Math.max(...points.map(point => point.x), 1)
  const maxY = Math.max(...points.map(point => point.y), 1)
  return <div><h3>{label}</h3><svg className="usage-scatter" viewBox="0 0 420 220" role="img" aria-label={label}><line x1="35" y1="190" x2="405" y2="190"/><line x1="35" y1="15" x2="35" y2="190"/>{points.map((point, index) => <circle key={index} cx={35 + point.x / maxX * 365} cy={190 - point.y / maxY * 170} r="2"/>)}<text x="255" y="214">log1p(Token)</text><text x="40" y="14">log1p(gap)</text></svg></div>
}

function ScoreChart({ rows }: { rows: Candidate[] }) {
  if (!rows.length) return null
  const values = rows.flatMap(row => [row.aic, row.bic])
  const min = Math.min(...values), max = Math.max(...values), range = max - min || 1
  const point = (value: number, index: number) => `${70 + index * 190},${180 - (value - min) / range * 145}`
  return <svg className="score-chart" viewBox="0 0 720 220" role="img" aria-label="GMM AIC 与 BIC 候选比较"><line x1="55" y1="180" x2="680" y2="180"/><polyline className="aic-line" points={rows.map((row,index)=>point(row.aic,index)).join(' ')}/><polyline className="bic-line" points={rows.map((row,index)=>point(row.bic,index)).join(' ')}/>{rows.map((row,index)=><g key={row.component_or_state_count}><text x={66+index*190} y="205">K={row.component_or_state_count}</text><circle className="aic-dot" cx={70+index*190} cy={180-(row.aic-min)/range*145} r="4"/><circle className="bic-dot" cx={70+index*190} cy={180-(row.bic-min)/range*145} r="4"/></g>)}</svg>
}

function SessionTimeline({ sessions, first, last, label }: { sessions: Session[]; first?: string; last?: string; label: string }) {
  if (!first || !last || !sessions.length) return <div className="empty">没有可显示的 {label} session</div>
  const start = +new Date(first), span = Math.max(+new Date(last) - start, 1)
  return <div className="session-lane"><b>{label}</b><div>{sessions.slice(-400).map(row => { const left=(+new Date(row.start_at)-start)/span*100; const width=Math.max((+new Date(row.end_at)-+new Date(row.start_at))/span*100,.18); return <i key={row.session_index} style={{left:`${left}%`,width:`${width}%`}}><span>{dateLabel(row.start_at)} · {row.event_count} 次 · {duration(row.estimated_usage_seconds)}</span></i> })}</div></div>
}

export function UsageTime() {
  const [data, setData] = useState<any>(null)
  const [timeline, setTimeline] = useState<'gmm' | 'hmm' | 'compare'>('compare')
  useEffect(() => { Promise.all([
    api<any>('/api/usage-time/summary'), api<any>('/api/usage-time/distribution'), api<any>('/api/usage-time/associations'),
    api<Candidate[]>('/api/usage-time/model-candidates'), api<any>('/api/usage-time/gmm'), api<any>('/api/usage-time/hmm'),
    api<any[]>('/api/usage-time/disagreements?limit=30'), api<Session[]>('/api/usage-time/sessions?model=gmm'), api<Session[]>('/api/usage-time/sessions?model=hmm'),
  ]).then(([summary,distribution,associations,candidates,gmm,hmm,disagreements,gmmSessions,hmmSessions]) => setData({summary,distribution,associations,candidates,gmm,hmm,disagreements,gmmSessions,hmmSessions})) }, [])
  const gmmCandidates = useMemo(() => (data?.candidates || []).filter((row: Candidate) => row.model_family === 'gmm'), [data])
  if (!data) return <div className="page-stack"><div className="empty">正在读取已保存的时长模型…</div></div>
  const { summary, distribution, associations, gmm, hmm } = data
  const enough = summary.status === 'complete'
  return <div className="page-stack usage-time">
    <header className="page-header"><div><span className="eyebrow">Interaction Rhythm & Session Inference</span><h1>使用时长</h1></div><p>从观测数据逐步推断；模型差异不是置信区间，也不存在唯一“真实总时间”。</p></header>

    <section className="panel"><div className="panel-heading"><div><span className="eyebrow">01 · Observed</span><h2>原始行为</h2></div></div><div className="usage-metrics"><div><span>用户事件</span><b>{number.format(summary.user_events || 0)}</b></div><div><span>相邻间隔</span><b>{number.format(summary.sample_count || 0)}</b></div><div><span>时间范围</span><b>{dateLabel(summary.first_event)} → {dateLabel(summary.last_event)}</b></div><div><span>覆盖平台 / 账号</span><b>{(summary.platforms || []).join(' + ') || '—'} · {(summary.accounts || []).length}</b></div></div></section>

    <section className="panel"><div className="panel-heading"><div><span className="eyebrow">02 · Observed</span><h2>交互间隔分布</h2></div><span className="quality-badge">log1p(inter-prompt gap)</span></div><Histogram rows={distribution.histogram || []}/><p className="privacy-note">这里仅展示经验分布，不预先把任何峰命名为 session 或 break。</p></section>

    <section className="panel"><div className="panel-heading"><div><span className="eyebrow">03 · Derived</span><h2>Token 与间隔的关联</h2></div></div><div className="association-summary"><span>上一回复 Spearman ρ <b>{associations.rho_previous_output?.toFixed(3) ?? '—'}</b></span><span>下一输入 Spearman ρ <b>{associations.rho_next_input?.toFixed(3) ?? '—'}</b></span><span>描述性回归 R² <b>{associations.regression?.r_squared?.toFixed(3) ?? '—'}</b></span></div><div className="two-column scatter-grid"><Scatter rows={associations.scatter || []} field="previous_output_tokens" label="gap × 上一回复 Token"/><Scatter rows={associations.scatter || []} field="next_input_tokens" label="gap × 下一输入 Token"/></div><p className="privacy-note">相关性只表示 association，不表示 Token 导致等待时间。</p></section>

    {!enough && <section className="panel empty"><h3>数据不足，暂不拟合 session 模型</h3><p>当前 {summary.sample_count} 个 gap；至少需要 {summary.minimum_model_samples} 个。上面的原始分布和相关统计仍然有效。</p></section>}

    {enough && <>
      <section className="panel"><div className="panel-heading"><div><span className="eyebrow">04 · Model selection</span><h2>GMM 候选与 AIC / BIC</h2></div><div className="score-legend"><span>AIC</span><span>BIC</span></div></div><ScoreChart rows={gmmCandidates}/><div className="usage-table"><div><b>Components</b><b>AIC</b><b>BIC</b><b>选择</b></div>{gmmCandidates.map((row:Candidate)=><div key={row.component_or_state_count}><span>K={row.component_or_state_count}</span><span>{compact(row.aic)}</span><span>{compact(row.bic)}</span><span>{row.selected_by_aic?'AIC ':''}{row.selected_by_bic?'BIC':''}</span></div>)}</div><p className={gmm.criteria_disagree?'notice':'privacy-note'}>AIC 偏好 K={gmm.selected_k_aic}；BIC 偏好 K={gmm.selected_k_bic}。{gmm.criteria_disagree?'两个准则对模型复杂度的选择不同。':'两个准则选择一致。'}分数越低越好。</p></section>

      <section className="panel"><div className="panel-heading"><div><span className="eyebrow">05 · Model-inferred</span><h2>GMM 解释</h2></div></div><div className="component-grid">{(gmm.components || []).map((item:any)=><div key={item.component}><span>{item.label}</span><b>{shortDuration(item.approximate_gap_seconds)}</b><small>权重 {(item.weight*100).toFixed(1)}% · 上一回复约 {compact(item.approximate_previous_output_tokens)} Token</small></div>)}</div><p className="notice">Aistory 将最短间隔 component 解释为连续活动，将最长间隔 component 作为 break 候选。数据 component 与产品解释在此分开呈现。</p></section>

      <section className="panel"><div className="panel-heading"><div><span className="eyebrow">06 · Diagnostic</span><h2>特征消融</h2></div></div><div className="usage-table ablation"><div><b>特征</b><b>K</b><b>AIC</b><b>BIC</b></div>{(summary.ablation || []).map((row:any)=><div key={row.features}><span>{row.features}</span><span>{row.component_or_state_count}</span><span>{compact(row.aic)}</span><span>{compact(row.bic)}</span></div>)}</div><p className="privacy-note">完整 Token 特征相对 gap-only 的 BIC 改善：{summary.token_feature_bic_improvement?.toFixed(1)}。{summary.token_feature_bic_improvement > 10 ? 'Token 特征显著改善拟合。' : 'Token 特征增加的解释力有限。'}</p></section>

      <section className="panel"><div className="panel-heading"><div><span className="eyebrow">07 · Temporal model</span><h2>HMM 状态连续性</h2></div></div><div className="hmm-grid"><div><span>较短间隔状态</span><b>{shortDuration(hmm.state_gap_seconds?.[0] || 0)}</b></div><div><span>较长间隔状态</span><b>{shortDuration(hmm.state_gap_seconds?.[1] || 0)}</b></div><div className="transition-matrix"><span>Active → Active</span><b>{hmm.transition_matrix?.[0]?.[0]?.toFixed(3)}</b><span>Active → Inactive</span><b>{hmm.transition_matrix?.[0]?.[1]?.toFixed(3)}</b><span>Inactive → Inactive</span><b>{hmm.transition_matrix?.[1]?.[1]?.toFixed(3)}</b><span>Inactive → Active</span><b>{hmm.transition_matrix?.[1]?.[0]?.toFixed(3)}</b></div></div></section>

      <section className="panel"><div className="panel-heading"><div><span className="eyebrow">08 · Model comparison</span><h2>GMM 与 HMM 分歧</h2></div></div><div className="usage-metrics"><div><span>边界一致率</span><b>{(summary.agreement_rate*100).toFixed(1)}%</b></div><div><span>分歧 gap</span><b>{summary.disagreement_count}</b></div><div><span>平均概率差</span><b>{summary.mean_probability_difference.toFixed(3)}</b></div></div><div className="usage-table disagreements"><div><b>时间</b><b>gap</b><b>上一输出</b><b>下一输入</b><b>GMM / HMM</b></div>{data.disagreements.slice(0,10).map((row:any,index:number)=><div key={index}><span>{dateLabel(row.time)}</span><span>{shortDuration(row.gap_seconds)}</span><span>{compact(row.prev_output_tokens)}</span><span>{compact(row.next_input_tokens)}</span><span>{row.gmm_break_probability.toFixed(2)} / {row.hmm_break_probability.toFixed(2)}</span></div>)}</div><p className="privacy-note">GMM 主要判断局部特征；HMM 还考虑前后状态连续性。这里不宣称任何一个模型绝对正确。</p></section>

      <section className="panel"><div className="panel-heading"><div><span className="eyebrow">09 · Model-inferred</span><h2>Session 时间线</h2></div><div className="segmented"><button className={timeline==='gmm'?'active':''} onClick={()=>setTimeline('gmm')}>GMM</button><button className={timeline==='hmm'?'active':''} onClick={()=>setTimeline('hmm')}>HMM</button><button className={timeline==='compare'?'active':''} onClick={()=>setTimeline('compare')}>比较</button></div></div><div className="session-timeline">{timeline!=='hmm'&&<SessionTimeline sessions={data.gmmSessions} first={summary.first_event} last={summary.last_event} label="GMM"/>}{timeline!=='gmm'&&<SessionTimeline sessions={data.hmmSessions} first={summary.first_event} last={summary.last_event} label="HMM"/>}</div></section>

      <section className="panel estimate-panel"><div className="panel-heading"><div><span className="eyebrow">10 · Estimated</span><h2>推断的 AI 使用时长</h2></div><span className="quality-badge">Session 尾部余量：{Math.round(summary.tail_allowance_seconds/60)} 分钟</span></div><div className="estimate-grid"><div><span>GMM estimate</span><b>{duration(gmm.estimated_usage_seconds)}</b><small>{gmm.sessions} sessions · 原始 span {duration(gmm.session_span_seconds)}</small></div><div><span>HMM estimate</span><b>{duration(hmm.estimated_usage_seconds)}</b><small>{hmm.sessions} sessions · 原始 span {duration(hmm.session_span_seconds)}</small></div></div><p className="notice">模型范围 {duration(Math.min(gmm.estimated_usage_seconds,hmm.estimated_usage_seconds))} – {duration(Math.max(gmm.estimated_usage_seconds,hmm.estimated_usage_seconds))} 反映模型选择差异，不是正式统计置信区间。</p></section>

      <p className="privacy-note provenance">模型 {summary.model_version} · 训练于 {dateLabel(summary.trained_at)} · {summary.sample_count} 个样本 · log1p + z-score · random state 固定 · 所有平台全局时间线</p>
    </>}
  </div>
}
