import { useEffect, useState } from 'react'
import { api } from '../api'

export function Settings() {
  const [values, setValues] = useState<any>(null)
  const [message, setMessage] = useState('')
  const [account, setAccount] = useState({ id: '', name: '', browser_profile: '' })
  useEffect(() => { api('/api/settings').then(setValues) }, [])
  if (!values) return <div className="loading">读取设置…</div>
  const save = async () => { setValues(await api('/api/settings', { method: 'PUT', body: JSON.stringify(values) })); setMessage('已保存到本地 JSON 配置') }
  const addAccount = async () => {
    await api('/api/accounts', { method: 'POST', body: JSON.stringify({ ...account, enabled: true }) })
    setValues(await api('/api/settings'))
    setAccount({ id: '', name: '', browser_profile: '' })
    setMessage('账号已添加；同步时将按列表顺序依次处理')
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
      <section className="panel form-panel">
        <h2>账号与浏览器资料</h2>
        <p className="notice">每个账号使用独立浏览器资料目录。多账号同步不会并发，而是按下列顺序逐个打开、读取和关闭。</p>
        <div className="account-list">{values.accounts.map((item: any) => <div key={item.id}><b>{item.name}</b><span>{item.id}</span><code>{item.browser_profile}</code></div>)}</div>
        <label>账号 ID<input value={account.id} onChange={(e) => setAccount({ ...account, id: e.target.value })} placeholder="例如 work" /></label>
        <label>显示名称<input value={account.name} onChange={(e) => setAccount({ ...account, name: e.target.value })} placeholder="工作账号" /></label>
        <label>浏览器资料目录<input value={account.browser_profile} onChange={(e) => setAccount({ ...account, browser_profile: e.target.value })} placeholder="browser_profiles/work" /></label>
        <div className="button-row"><button disabled={!account.id || !account.name} onClick={addAccount}>添加或更新账号</button></div>
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
        <p className="privacy-note">本地确定性统计完全不需要 API。密钥不会进入 SQLite、API 响应或日志；本项目按你的要求将其保存在被 Git 忽略的本地 JSON 中。</p>
      </section>
      <div className="button-row"><button className="primary" onClick={save}>保存设置</button>{message && <span className="success">{message}</span>}</div>
    </div>
  )
}
