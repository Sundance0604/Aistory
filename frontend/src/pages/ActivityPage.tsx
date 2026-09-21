import { useMemo, useState } from 'react'
import { api, compact } from '../api'
import { MiniBars } from '../components/MiniBars'
import type { Day } from '../types'

type Granularity = 'daily' | 'weekly' | 'monthly'
type Metric = 'prompts' | 'prompt_visible_tokens' | 'response_visible_tokens' | 'total_visible_tokens' | 'active_conversations'

export function ActivityPage({ initial }: { initial: Day[] }) {
  const [granularity, setGranularity] = useState<Granularity>('daily')
  const [metric, setMetric] = useState<Metric>('total_visible_tokens')
  const [data, setData] = useState<Day[]>(initial)
  const changeGranularity = async (value: Granularity) => {
    setGranularity(value)
    setData(await api<Day[]>(`/api/activity/${value}`))
  }
  const total = useMemo(() => data.reduce((sum, row) => sum + Number(row[metric]), 0), [data, metric])
  return (
    <div className="page-stack">
      <header className="page-header"><div><span className="eyebrow">时间序列</span><h1>活动</h1></div><p>按本地时区聚合的确定性统计。</p></header>
      <section className="panel">
        <div className="panel-heading wrap">
          <div><span className="eyebrow">当前视图总计</span><h2>{compact(total)}</h2></div>
          <div className="controls">
            <div className="segmented">{(['daily', 'weekly', 'monthly'] as Granularity[]).map((value) => <button className={granularity === value ? 'active' : ''} onClick={() => changeGranularity(value)} key={value}>{value === 'daily' ? '日' : value === 'weekly' ? '周' : '月'}</button>)}</div>
            <select value={metric} onChange={(event) => setMetric(event.target.value as Metric)}>
              <option value="prompts">提示数</option><option value="prompt_visible_tokens">输入可见 Token</option><option value="response_visible_tokens">输出可见 Token</option><option value="total_visible_tokens">总可见 Token</option><option value="active_conversations">活跃对话</option>
            </select>
          </div>
        </div>
        <MiniBars data={data} metric={metric} />
        <div className="activity-table"><span>周期</span><span>提示</span><span>输入</span><span>输出</span>{data.slice(-20).reverse().map((row) => <div className="table-row" key={row.date}><b>{row.date}</b><span>{compact(row.prompts)}</span><span>{compact(row.prompt_visible_tokens)}</span><span>{compact(row.response_visible_tokens)}</span></div>)}</div>
      </section>
    </div>
  )
}
