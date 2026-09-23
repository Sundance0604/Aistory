import { useEffect, useState } from 'react'
import { CheckCircle2, Database, RefreshCw } from 'lucide-react'
import { api } from '../api'

type DiscoveredWechat = {
  account: string
  wxid: string
  path: string
  data_dir: string
  last_activity: number
  suggested_id: string
}

type WechatDiscovery = {
  available: boolean
  detected_root: string | null
  accounts: DiscoveredWechat[]
  message: string | null
}

export function Settings() {
  const [values, setValues] = useState<any>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [account, setAccount] = useState({ id: '', name: '', provider: 'chatgpt', browser_profile: '', alias: '', wechat_data_dir: '', wechat_account: '' })
  const [wechatRoot, setWechatRoot] = useState('')
  const [discovery, setDiscovery] = useState<WechatDiscovery | null>(null)
  const [discovering, setDiscovering] = useState(false)
  const [addingWechat, setAddingWechat] = useState('')
  const [aliases, setAliases] = useState<Record<string, string>>({})
  const discoverWechat = async (root = wechatRoot) => {
    setDiscovering(true); setError('')
    try {
      const query = root.trim() ? `?data_dir=${encodeURIComponent(root.trim())}` : ''
      const result = await api<WechatDiscovery>(`/api/wechat/discover${query}`)
      setDiscovery(result)
      if (result.detected_root) setWechatRoot(result.detected_root)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setDiscovering(false) }
  }
  useEffect(() => { api('/api/settings').then(setValues); discoverWechat('') }, [])
  if (!values) return <div className="loading">读取设置…</div>
  const save = async () => { setValues(await api('/api/settings', { method: 'PUT', body: JSON.stringify(values) })); setMessage('已保存到本地 JSON 配置') }
  const addAccount = async () => {
    setError('')
    try {
      await api('/api/accounts', { method: 'POST', body: JSON.stringify({ ...account, enabled: true }) })
      setValues(await api('/api/settings'))
      setAccount({ id: '', name: '', provider: 'chatgpt', browser_profile: '', alias: '', wechat_data_dir: '', wechat_account: '' })
      setMessage('账号已添加；同步时将按列表顺序依次处理')
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
  }
  const addDiscoveredWechat = async (item: DiscoveredWechat) => {
    setAddingWechat(item.account); setError(''); setMessage('')
    try {
      await api('/api/accounts', { method: 'POST', body: JSON.stringify({
        id: item.suggested_id, name: '', provider: 'wechat', alias: aliases[item.account] || '',
        wechat_data_dir: item.data_dir, wechat_account: item.account, enabled: true,
      }) })
      setValues(await api('/api/settings'))
      setMessage(`微信账号 …${item.wxid.slice(-4)} 已添加，可以前往“同步”读取历史。`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setAddingWechat('') }
  }
  return (
    <div className="page-stack narrow">
      <header className="page-header"><div><span className="eyebrow">单一配置源</span><h1>设置</h1></div><p>外部配置集中在 <code>config.local.json</code>。</p></header>
      <section className="panel form-panel">
        <h2>显示与同步</h2>
        <label>本地时区<input value={values.app.timezone} onChange={(e) => setValues({ ...values, app: { ...values.app, timezone: e.target.value } })} /></label>
        <label>浏览器<select value={values.chatgpt.browser_channel} onChange={(e) => setValues({ ...values, chatgpt: { ...values.chatgpt, browser_channel: e.target.value } })}><option value="chrome">Google Chrome</option><option value="msedge">Microsoft Edge</option></select></label>
        <label>遇到首个未变化即停止<input type="checkbox" checked={values.chatgpt.stop_on_first_unchanged !== false} onChange={(e) => setValues({ ...values, chatgpt: { ...values.chatgpt, stop_on_first_unchanged: e.target.checked } })} /></label>
      </section>
      <section className="panel form-panel wechat-setup">
        <div className="wechat-title"><Database size={21}/><div><span className="eyebrow">WeChat 4.x · 本机只读</span><h2>自动发现微信历史</h2></div></div>
        <p className="notice">保持桌面微信登录，然后点击自动查找。Aistory 会定位本机 <code>xwechat_files</code>，不会写入微信数据库。</p>
        <label>数据根目录（可选）<input value={wechatRoot} onChange={(e) => setWechatRoot(e.target.value)} placeholder="留空自动查找；也可填写 xwechat_files 路径" /></label>
        <div className="button-row"><button className="primary" disabled={discovering} onClick={() => discoverWechat()}>{discovering ? '正在查找…' : <><RefreshCw size={15}/>自动查找已登录账号</>}</button></div>
        {discovery?.detected_root && <p className="wechat-root"><CheckCircle2 size={15}/>已定位：<code>{discovery.detected_root}</code></p>}
        {discovery?.message && <p className="notice">{discovery.message}</p>}
        {!!discovery?.accounts.length && <div className="wechat-discoveries">{discovery.accounts.map((item) => {
          const configured = values.accounts.some((existing:any) => existing.provider === 'wechat' && existing.wechat_account === item.account)
          return <article key={item.account}><div><b>{item.wxid}</b><small>{item.account}</small><code>{item.path}</code></div><label>本地别名<input value={aliases[item.account] || ''} onChange={(e) => setAliases({ ...aliases, [item.account]: e.target.value })} placeholder="例如：主号 / 副号" /></label><button disabled={configured || addingWechat === item.account} onClick={() => addDiscoveredWechat(item)}>{configured ? '已添加' : addingWechat === item.account ? '正在验证…' : '添加到 Aistory'}</button></article>
        })}</div>}
      </section>
      <section className="panel form-panel">
        <h2>账号与 Provider</h2>
        <p className="notice">每个账号保持独立身份和数据源；多账号同步按列表顺序处理。</p>
        <div className="account-list">{values.accounts.map((item: any) => <div key={item.id}><b>{item.alias ? `${item.name} · ${item.alias}` : item.name}</b><span>{item.provider || 'chatgpt'} · {item.id}</span><code>{item.wechat_data_dir || item.browser_profile || 'JSON Cookie'}</code></div>)}</div>
        <label>账号 ID<input value={account.id} onChange={(e) => setAccount({ ...account, id: e.target.value })} placeholder="例如 work" /></label>
        <label>显示名称<input value={account.name} onChange={(e) => setAccount({ ...account, name: e.target.value })} placeholder="工作账号" /></label>
        <label>平台<select value={account.provider} onChange={(e) => setAccount({ ...account, provider: e.target.value })}><option value="chatgpt">ChatGPT</option><option value="gemini">Gemini</option><option value="wechat">WeChat</option></select></label>
        {account.provider === 'chatgpt' && <label>浏览器资料目录<input value={account.browser_profile} onChange={(e) => setAccount({ ...account, browser_profile: e.target.value })} placeholder="browser_profiles/work" /></label>}
        {account.provider === 'wechat' && <><label>本地别名<input value={account.alias} onChange={(e) => setAccount({ ...account, alias: e.target.value })} placeholder="例如：主号" /></label><label>微信数据根目录<input value={account.wechat_data_dir} onChange={(e) => setAccount({ ...account, wechat_data_dir: e.target.value })} placeholder="可留空自动查找" /></label><label>微信账号目录名（可选）<input value={account.wechat_account} onChange={(e) => setAccount({ ...account, wechat_account: e.target.value })} placeholder="多个微信账号时使用自动发现更方便" /></label><p className="privacy-note">保存时只读验证目录和微信账号身份，不会自动同步历史。</p></>}
        <div className="button-row"><button disabled={!account.id || (account.provider !== 'wechat' && !account.name)} onClick={addAccount}>添加或更新账号</button></div>
      </section>
      <section className="panel form-panel">
        <h2>WeChat 读取</h2>
        <p className="notice">微信数据库仅以只读方式访问；聊天正文只复制到本机 Aistory SQLite，不进入主题分类 API。</p>
        <label>启用 WeChat<input type="checkbox" checked={values.wechat.enabled !== false} onChange={(e) => setValues({ ...values, wechat: { ...values.wechat, enabled: e.target.checked } })} /></label>
        <label>同步私聊<input type="checkbox" checked={values.wechat.sync_private !== false} onChange={(e) => setValues({ ...values, wechat: { ...values.wechat, sync_private: e.target.checked } })} /></label>
        <label>同步群聊<input type="checkbox" checked={values.wechat.sync_groups !== false} onChange={(e) => setValues({ ...values, wechat: { ...values.wechat, sync_groups: e.target.checked } })} /></label>
      </section>
      <section className="panel form-panel">
        <h2>Gemini 读取</h2>
        <p className="notice">Cookie 保存在被 Git 忽略的 config.local.json；设置接口、日志和导出清单均不回传其值。</p>
        <label>Secure-1PSID<input type="password" placeholder={values.gemini.credentials_configured ? '已配置；留空保持不变' : '粘贴 Cookie'} value={values.gemini.secure_1psid || ''} onChange={(e) => setValues({ ...values, gemini: { ...values.gemini, secure_1psid: e.target.value } })} /></label>
        <label>Secure-1PSIDTS<input type="password" placeholder="已配置时留空保持不变" value={values.gemini.secure_1psidts || ''} onChange={(e) => setValues({ ...values, gemini: { ...values.gemini, secure_1psidts: e.target.value } })} /></label>
        <label>代理（可选）<input value={values.gemini.proxy || ''} onChange={(e) => setValues({ ...values, gemini: { ...values.gemini, proxy: e.target.value } })} /></label>
      </section>
      <section className="panel form-panel">
        <h2>外部数据保存</h2>
        <label>保存方式<select value={values.storage.method} onChange={(e) => setValues({ ...values, storage: { ...values.storage, method: e.target.value } })}><option value="filesystem">本地文件系统</option></select></label>
        <label>SQLite 位置<input value={values.storage.database_path} onChange={(e) => setValues({ ...values, storage: { ...values.storage, database_path: e.target.value } })} /></label>
        <label>原始 JSON 位置<input value={values.storage.raw_conversations_dir} onChange={(e) => setValues({ ...values, storage: { ...values.storage, raw_conversations_dir: e.target.value } })} /></label>
        <p className="privacy-note">保存层通过统一接口写入，当前实现为 filesystem；原始 JSON 按账号分目录保存。位置既可使用项目相对路径，也可使用绝对路径。修改 SQLite 位置会创建或打开目标数据库，不会自动搬迁旧数据库。</p>
      </section>
      <section className="panel form-panel">
        <h2>主题分析</h2>
        <p className="notice">只会发送当前用户提示、标题和最多两个先前用户提示。不会发送完整对话或附件。</p>
        <label>API 地址<input value={values.topics.base_url} onChange={(e) => setValues({ ...values, topics: { ...values.topics, base_url: e.target.value } })} /></label>
        <label>模型<input value={values.topics.model} onChange={(e) => setValues({ ...values, topics: { ...values.topics, model: e.target.value } })} /></label>
        <label>API 密钥<input type="password" placeholder={values.topics.api_key_configured ? '已配置；留空保持不变' : '粘贴 API 密钥'} value={values.topics.api_key || ''} onChange={(e) => setValues({ ...values, topics: { ...values.topics, api_key: e.target.value } })} /></label>
        <label>偏好主题词<input value={(values.topics.preferences?.keywords || []).join(', ')} onChange={(e) => setValues({ ...values, topics: { ...values.topics, preferences: { ...(values.topics.preferences || {}), keywords: e.target.value.split(',').map((item: string) => item.trim()).filter(Boolean) } } })} placeholder="例如：机器学习, 写作, 历史" /></label>
        <p className="privacy-note">本地确定性统计完全不需要 API。密钥不会进入 SQLite、API 响应或日志；本项目按你的要求将其保存在被 Git 忽略的本地 JSON 中。</p>
      </section>
      <section className="panel form-panel"><h2>生命周期口径</h2><label>会话间隔（分钟）<input type="number" value={values.analytics.session_gap_minutes} onChange={(e) => setValues({ ...values, analytics: { ...values.analytics, session_gap_minutes: Number(e.target.value) } })}/></label><label>单提示估算（分钟）<input type="number" value={values.analytics.single_prompt_minutes} onChange={(e) => setValues({ ...values, analytics: { ...values.analytics, single_prompt_minutes: Number(e.target.value) } })}/></label><label>会话尾部（分钟）<input type="number" value={values.analytics.session_tail_minutes} onChange={(e) => setValues({ ...values, analytics: { ...values.analytics, session_tail_minutes: Number(e.target.value) } })}/></label></section>
      <section className="panel form-panel"><h2>使用时长模型</h2><p className="notice">“全部 AI”仅合并 ChatGPT 与 Gemini 的主动事件；WeChat 会使用独立时间线，并且只把本人发出的消息作为活动锚点。核心模型只使用相邻事件间隔，不会把对话切换当作硬边界。</p><label>Session 尾部余量（分钟）<input type="number" min="0" value={values.usage_time.tail_allowance_minutes} onChange={(e) => setValues({ ...values, usage_time: { ...values.usage_time, tail_allowance_minutes: Number(e.target.value) } })}/></label><label>边界概率阈值<input type="number" min="0" max="1" step="0.05" value={values.usage_time.boundary_threshold ?? 0.5} onChange={(e) => setValues({ ...values, usage_time: { ...values.usage_time, boundary_threshold: Number(e.target.value) } })}/></label><label>最少 gap 样本<input type="number" min="2" value={values.usage_time.min_model_samples} onChange={(e) => setValues({ ...values, usage_time: { ...values.usage_time, min_model_samples: Number(e.target.value) } })}/></label><p className="privacy-note">修改后保存会重新生成模型。尾部余量和边界阈值都是可见、可配置的建模假设，不是观测事实。</p></section>
      <div className="button-row"><button className="primary" onClick={save}>保存设置</button>{message && <span className="success">{message}</span>}{error && <span className="error">{error}</span>}</div>
    </div>
  )
}
