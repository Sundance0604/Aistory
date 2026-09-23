import { useEffect, useMemo, useState } from 'react'
import { api, compact, dateLabel, number } from '../api'

type Candidate = { model_family: string; component_or_state_count: number; feature_set: string; log_likelihood: number; aic: number; bic: number; selected_by_aic: number; selected_by_bic: number }
type Boundary = { gap_index:number; from_timestamp:string; to_timestamp:string; gap_seconds:number; gmm_boundary_prob:number; hmm_long_gap_prob:number; gmm_boundary:number; hmm_boundary:number; disagreement:number }

function duration(seconds?: number | null) {
  if (seconds == null) return '—'
  const minutes = Math.round(seconds / 60), hours = Math.floor(minutes / 60)
  return hours ? `${hours} 小时 ${minutes % 60} 分` : `${minutes} 分钟`
}

function shortDuration(seconds?: number | null) {
  if (seconds == null) return '—'
  if (seconds < 60) return `${Math.round(seconds)} 秒`
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)} 小时`
  return `${(seconds / 86400).toFixed(1)} 天`
}

function Histogram({ rows }: { rows: any[] }) {
  const max = Math.max(...rows.map(row => row.count), 1)
  return <div className="usage-chart-scroll"><svg className="usage-histogram" viewBox="0 0 900 230" role="img" aria-label="主动事件间隔的对数分布">
    <line x1="38" y1="190" x2="880" y2="190" />
    {rows.map((row, index) => { const width = 842 / Math.max(rows.length, 1), height = row.count / max * 160; return <rect key={index} x={38 + index * width} y={190 - height} width={Math.max(2, width - 2)} height={height}><title>{shortDuration(row.seconds_start)}–{shortDuration(row.seconds_end)}：{row.count}</title></rect> })}
    <text x="38" y="218">短间隔</text><text x="810" y="218">长间隔</text>
  </svg></div>
}

function Scatter({ rows, field, label }: { rows:any[]; field:'previous_output_tokens'|'next_input_tokens'; label:string }) {
  const points = rows.map(row => ({ x:Math.log1p(row[field]), y:Math.log1p(row.gap_seconds) }))
  const maxX = Math.max(...points.map(point => point.x), 1), maxY = Math.max(...points.map(point => point.y), 1)
  return <div><h3>{label}</h3><svg className="usage-scatter" viewBox="0 0 420 220" role="img" aria-label={label}><line x1="35" y1="190" x2="405" y2="190"/><line x1="35" y1="15" x2="35" y2="190"/>{points.map((point,index)=><circle key={index} cx={35+point.x/maxX*365} cy={190-point.y/maxY*170} r="2"/>)}<text x="255" y="214">log1p(Token)</text><text x="40" y="14">log1p(gap)</text></svg></div>
}

function ScoreChart({ rows }: { rows:Candidate[] }) {
  if (!rows.length) return null
  const values = rows.flatMap(row => [row.aic,row.bic]), min=Math.min(...values), max=Math.max(...values), range=max-min||1
  const step = 600 / Math.max(rows.length - 1, 1), x=(index:number)=>70+index*step, y=(value:number)=>180-(value-min)/range*145
  return <svg className="score-chart" viewBox="0 0 720 220" role="img" aria-label="gap-only GMM AIC 与 BIC 候选比较"><line x1="55" y1="180" x2="690" y2="180"/><polyline className="aic-line" points={rows.map((row,index)=>`${x(index)},${y(row.aic)}`).join(' ')}/><polyline className="bic-line" points={rows.map((row,index)=>`${x(index)},${y(row.bic)}`).join(' ')}/>{rows.map((row,index)=><g key={row.component_or_state_count}><text x={x(index)-10} y="205">K={row.component_or_state_count}</text><circle className="aic-dot" cx={x(index)} cy={y(row.aic)} r="4"/><circle className="bic-dot" cx={x(index)} cy={y(row.bic)} r="4"/></g>)}</svg>
}

function BoundaryTimeline({ rows, model }: { rows:Boundary[]; model:'gmm'|'hmm' }) {
  if (!rows.length) return <div className="empty">没有可显示的边界记录</div>
  return <div className="boundary-lane"><b>{model.toUpperCase()}</b><div>{rows.map(row => {
    const boundary = model === 'gmm' ? row.gmm_boundary : row.hmm_boundary
    const probability = model === 'gmm' ? row.gmm_boundary_prob : row.hmm_long_gap_prob
    return <i key={row.gap_index} className={`${boundary?'is-boundary':''} ${row.disagreement?'is-disagreement':''}`} style={{opacity:String(.2 + probability * .8)}}><span>{dateLabel(row.to_timestamp)} · gap {shortDuration(row.gap_seconds)} · 边界概率 {probability.toFixed(3)} · {boundary?'边界':'非边界'}{row.disagreement?' · 模型分歧':''}</span></i>
  })}</div></div>
}

function ModelDiagnostics({ name, model }: { name:string; model:any }) {
  const boundary=model.boundary_diagnostics||{}, session=model.session_duration_diagnostics||{}
  return <div className="diagnostic-card"><h3>{name}</h3><dl><dt>边界 / 比例</dt><dd>{boundary.boundaries ?? '—'} / {boundary.boundary_rate == null?'—':`${(boundary.boundary_rate*100).toFixed(1)}%`}</dd><dt>Session</dt><dd>{boundary.sessions ?? '—'}</dd><dt>Session 内 gap 中位数</dt><dd>{shortDuration(boundary.median_within_gap_seconds)}</dd><dt>Session 内 gap P90 / 最大</dt><dd>{shortDuration(boundary.p90_within_gap_seconds)} / {shortDuration(boundary.max_within_gap_seconds)}</dd><dt>边界 gap 中位数 / 最小</dt><dd>{shortDuration(boundary.median_break_gap_seconds)} / {shortDuration(boundary.minimum_break_gap_seconds)}</dd><dt>Session 时长中位数 / P95</dt><dd>{shortDuration(session.median_seconds)} / {shortDuration(session.p95_seconds)}</dd><dt>最长 Session</dt><dd>{shortDuration(session.longest_seconds)}</dd><dt>&gt; 6h / 12h / 24h</dt><dd>{session.over_6h ?? '—'} / {session.over_12h ?? '—'} / {session.over_24h ?? '—'}</dd></dl></div>
}

export function UsageTime({ provider }: { provider: string }) {
  const [data,setData]=useState<any>(null), [timeline,setTimeline]=useState<'gmm'|'hmm'|'compare'>('compare')
  useEffect(()=>{ setData(null); const scope=`provider=${encodeURIComponent(provider)}`; api<any>(`/api/usage-time/summary?${scope}`).then(summary=>Promise.all([
    api<any>(`/api/usage-time/distribution?${scope}`),api<any>(`/api/usage-time/associations?${scope}`),
    api<Candidate[]>(`/api/usage-time/model-candidates?${scope}`),api<any>(`/api/usage-time/gmm?${scope}`),api<any>(`/api/usage-time/hmm?${scope}`),
    api<any[]>(`/api/usage-time/disagreements?limit=30&${scope}`),api<Boundary[]>(`/api/usage-time/boundaries?limit=240&${scope}`),
  ]).then(([distribution,associations,candidates,gmm,hmm,disagreements,boundaries])=>setData({summary,distribution,associations,candidates,gmm,hmm,disagreements,boundaries})))},[provider])
  const gmmCandidates=useMemo(()=>(data?.candidates||[]).filter((row:Candidate)=>row.model_family==='gmm'),[data])
  if(!data)return <div className="page-stack"><div className="empty">正在读取已保存的时长模型…</div></div>
  const {summary,distribution,associations,gmm,hmm}=data, enough=summary.status==='complete', matrix=summary.confusion_matrix||{}
  return <div className="page-stack usage-time">
    <header className="page-header"><div><span className="eyebrow">Interaction Rhythm & Session Inference</span><h1>{provider === 'wechat' ? '估算聊天活跃时间' : '估算数字活跃时间'}</h1></div><p>{provider === 'wechat' ? '只使用本人发送消息作为强活动锚点；收到消息不会单独生成 Session。' : !provider ? '“全部 AI”只合并 ChatGPT 与 Gemini 的主动事件，不包含微信。' : '从当前 AI Provider 的主动事件时间戳推断 Session 边界。'}结果不是真实持续操作时长。</p></header>

    <section className="panel"><div className="panel-heading"><div><span className="eyebrow">01 · Observed</span><h2>原始行为</h2></div></div><div className="usage-metrics"><div><span>用户事件</span><b>{number.format(summary.user_events||0)}</b></div><div><span>相邻间隔</span><b>{number.format(summary.sample_count||0)}</b></div><div><span>时间范围</span><b>{dateLabel(summary.first_event)} → {dateLabel(summary.last_event)}</b></div><div><span>覆盖平台 / 账号</span><b>{(summary.platforms||[]).join(' + ')||'—'} · {(summary.accounts||[]).length}</b></div></div></section>

    <section className="panel"><div className="panel-heading"><div><span className="eyebrow">02 · Observed</span><h2>交互间隔分布</h2></div><span className="quality-badge">log1p(inter-prompt gap)</span></div><Histogram rows={distribution.histogram||[]}/><p className="privacy-note">这里只展示经验分布，不预先把任何峰命名为 Session 或边界。</p></section>

    <section className="panel"><div className="panel-heading"><div><span className="eyebrow">03 · Exploratory</span><h2>Token 与间隔的关联</h2></div></div><div className="association-summary"><span>上一回复 Spearman ρ <b>{associations.rho_previous_output?.toFixed(3)??'—'}</b></span><span>下一输入 Spearman ρ <b>{associations.rho_next_input?.toFixed(3)??'—'}</b></span><span>描述性回归 R² <b>{associations.regression?.r_squared?.toFixed(3)??'—'}</b></span></div><div className="two-column scatter-grid"><Scatter rows={associations.scatter||[]} field="previous_output_tokens" label="gap × 上一回复 Token"/><Scatter rows={associations.scatter||[]} field="next_input_tokens" label="gap × 下一输入 Token"/></div><p className="notice">当前数据中 Token 长度与交互间隔的关联较弱，因此 Token 不参与核心 Session 边界推断，仅保留作描述性分析。</p></section>

    {!enough&&<section className="panel empty"><h3>数据不足，暂不拟合 Session 模型</h3><p>当前 {summary.sample_count} 个 gap；至少需要 {summary.minimum_model_samples} 个。</p></section>}
    {enough&&<>
      <section className="panel"><div className="panel-heading"><div><span className="eyebrow">04 · Model selection</span><h2>Gap-only GMM 候选</h2></div><div className="score-legend"><span>AIC</span><span>BIC</span></div></div><ScoreChart rows={gmmCandidates}/><div className="usage-table"><div><b>Components</b><b>AIC</b><b>BIC</b><b>选择</b></div>{gmmCandidates.map((row:Candidate)=><div key={row.component_or_state_count}><span>K={row.component_or_state_count}</span><span>{compact(row.aic)}</span><span>{compact(row.bic)}</span><span>{row.selected_by_aic?'AIC ':''}{row.selected_by_bic?'BIC':''}</span></div>)}</div><p className={gmm.criteria_disagree?'notice':'privacy-note'}>AIC 偏好 K={gmm.selected_k_aic}；BIC 偏好 K={gmm.selected_k_bic}。分数越低越好，且这里只比较同一个 gap-only 特征空间内的复杂度。</p></section>

      <section className="panel"><div className="panel-heading"><div><span className="eyebrow">05 · Gap regimes</span><h2>GMM 间隔状态</h2></div><span className="quality-badge">边界阈值 {summary.boundary_threshold.toFixed(2)}</span></div><div className="component-grid">{(gmm.regimes||[]).map((item:any)=><div key={item.semantic_index}><span>{item.label}</span><b>典型 gap：{shortDuration(item.median_gap_seconds)}</b><small>P25–P75 {shortDuration(item.p25_gap_seconds)}–{shortDuration(item.p75_gap_seconds)} · 权重 {(item.empirical_weight*100).toFixed(1)}%</small></div>)}</div><p className="notice">最长 gap regime 的后验概率形成边界概率，再由可配置阈值转换为 Session 边界；间隔状态本身不是用户状态。</p></section>

      <section className="panel"><div className="panel-heading"><div><span className="eyebrow">06 · Diagnostic</span><h2>特征模型诊断</h2></div></div><div className="usage-table ablation"><div><b>特征空间</b><b>维度</b><b>AIC 选 K</b><b>BIC 选 K</b></div>{(summary.feature_model_diagnostics||[]).map((row:any)=><div key={row.features}><span>{row.features}</span><span>{row.observed_dimensions}</span><span>{row.selected_k_aic}</span><span>{row.selected_k_bic}</span></div>)}</div><p className="privacy-note">BIC 仅用于同一特征空间内选择模型复杂度。不同观测维度模型的绝对 BIC 不用于直接判断某一特征是否提升 Session 识别能力。</p></section>

      <section className="panel"><div className="panel-heading"><div><span className="eyebrow">07 · Sequential gap model</span><h2>HMM 间隔状态连续性</h2></div></div><div className="hmm-grid">{(hmm.regimes||[]).map((item:any)=><div key={item.semantic_index}><span>{item.label} regime</span><b>典型 gap：{shortDuration(item.median_gap_seconds)}</b><small>经验权重 {(item.empirical_weight*100).toFixed(1)}%</small></div>)}<div className="transition-matrix"><span>Short-gap → Short-gap</span><b>{hmm.transition_matrix?.[0]?.[0]?.toFixed(3)}</b><span>Short-gap → Long-gap</span><b>{hmm.transition_matrix?.[0]?.[1]?.toFixed(3)}</b><span>Long-gap → Long-gap</span><b>{hmm.transition_matrix?.[1]?.[1]?.toFixed(3)}</b><span>Long-gap → Short-gap</span><b>{hmm.transition_matrix?.[1]?.[0]?.toFixed(3)}</b></div></div><p className="privacy-note">HMM 转移概率描述相邻交互间隔所属时间尺度的连续性，不表示用户在现实时间中的“活跃 / 离开”状态。状态持久性：Short-gap {(hmm.state_persistence?.[0]*100).toFixed(1)}%，Long-gap {(hmm.state_persistence?.[1]*100).toFixed(1)}%。</p></section>

      <section className="panel"><div className="panel-heading"><div><span className="eyebrow">08 · Validation</span><h2>边界与 Session 异常诊断</h2></div></div><div className="diagnostic-grid"><ModelDiagnostics name="GMM · 局部 gap 模型" model={gmm}/><ModelDiagnostics name="HMM · 序列 gap 模型" model={hmm}/></div><p className="privacy-note">Session 时长诊断使用包含尾部余量的估计时长；极长 Session 和过大的 Session 内 gap 会直接暴露边界漏判。</p></section>

      <section className="panel"><div className="panel-heading"><div><span className="eyebrow">09 · Model comparison</span><h2>GMM 与 HMM 分歧</h2></div></div><div className="usage-metrics"><div><span>边界一致率</span><b>{(summary.agreement_rate*100).toFixed(1)}%</b></div><div><span>分歧 gap</span><b>{summary.disagreement_count}</b></div><div><span>平均概率差</span><b>{summary.mean_probability_difference.toFixed(3)}</b></div><div><span>GMM 是 / HMM 是</span><b>{matrix.gmm_yes_hmm_yes??'—'}</b></div></div><div className="confusion"><span></span><b>HMM 非边界</b><b>HMM 边界</b><b>GMM 非边界</b><strong>{matrix.gmm_no_hmm_no??'—'}</strong><strong>{matrix.gmm_no_hmm_yes??'—'}</strong><b>GMM 边界</b><strong>{matrix.gmm_yes_hmm_no??'—'}</strong><strong>{matrix.gmm_yes_hmm_yes??'—'}</strong></div><div className="usage-table disagreements"><div><b>时间 / gap</b><b>上一输出 / 下一输入</b><b>GMM 边界概率 / 决策</b><b>HMM Long-gap 概率 / 决策</b><b>概率差</b></div>{data.disagreements.slice(0,10).map((row:any,index:number)=><div key={index}><span>{dateLabel(row.timestamp)} · {shortDuration(row.gap_seconds)}</span><span>{compact(row.previous_output_tokens)} / {compact(row.next_input_tokens)}</span><span>{row.gmm_boundary_prob.toFixed(3)} · {row.gmm_boundary?'是':'否'}</span><span>{row.hmm_long_gap_prob.toFixed(3)} · {row.hmm_boundary?'是':'否'}</span><span>{row.probability_difference.toFixed(3)}</span></div>)}</div><p className="privacy-note">GMM 主要刻画局部间隔结构，HMM 额外考虑相邻间隔状态的连续性。这里不宣称任一模型是绝对正确的。</p></section>

      <section className="panel"><div className="panel-heading"><div><span className="eyebrow">10 · Boundary timeline</span><h2>事件间隔与边界</h2></div><div className="segmented"><button className={timeline==='gmm'?'active':''} onClick={()=>setTimeline('gmm')}>GMM</button><button className={timeline==='hmm'?'active':''} onClick={()=>setTimeline('hmm')}>HMM</button><button className={timeline==='compare'?'active':''} onClick={()=>setTimeline('compare')}>比较</button></div></div><div className="boundary-timeline">{timeline!=='hmm'&&<BoundaryTimeline rows={data.boundaries} model="gmm"/>}{timeline!=='gmm'&&<BoundaryTimeline rows={data.boundaries} model="hmm"/>}</div><p className="privacy-note">显示最近 {data.boundaries.length} 个 gap。每格代表两个事件之间的间隔，粗边框为 Session 边界，红色标记为模型分歧；不再用连续长条暗示持续操作。</p></section>

      <section className="panel estimate-panel"><div className="panel-heading"><div><span className="eyebrow">11 · Model-implied</span><h2>估算聊天与数字活跃时间</h2></div><span className="quality-badge">Session 尾部余量：{Math.round(summary.tail_allowance_seconds/60)} 分钟</span></div><div className="estimate-grid"><div><span>GMM model-implied</span><b>{duration(gmm.estimated_usage_seconds)}</b><small>{gmm.sessions} sessions · 原始 span {duration(gmm.session_span_seconds)}</small></div><div><span>HMM model-implied</span><b>{duration(hmm.estimated_usage_seconds)}</b><small>{hmm.sessions} sessions · 原始 span {duration(hmm.session_span_seconds)}</small></div></div><p className="notice">模型设定敏感性范围：{duration(Math.min(gmm.estimated_usage_seconds,hmm.estimated_usage_seconds))} – {duration(Math.max(gmm.estimated_usage_seconds,hmm.estimated_usage_seconds))}。该结果根据交互时间戳与 Session 边界推断得到，不等同于真实持续注视屏幕或连续操作平台的时间，也不是统计置信区间。</p></section>

      <p className="privacy-note provenance">模型 {summary.model_version} · 训练于 {dateLabel(summary.trained_at)} · {summary.sample_count} 个样本 · 核心特征 log1p(gap) + z-score · 边界阈值 {summary.boundary_threshold.toFixed(2)} · {provider || '全部 AI'}时间线</p>
    </>}
  </div>
}
