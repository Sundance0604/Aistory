import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, LoaderCircle, RefreshCw } from 'lucide-react'
import { api, compact, dateLabel } from '../api'
import type { Account } from '../types'

export function SyncPage() {
  const [state, setState] = useState<any>({ job: { status: 'idle' }, last_sync: { status: 'never' } })
  const [accounts, setAccounts] = useState<Account[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [importPath, setImportPath] = useState('')
  const [importAccount, setImportAccount] = useState('default')
  const [requestError, setRequestError] = useState('')
  const refresh = () => api('/api/sync/status').then(setState)
  useEffect(() => { refresh(); api<Account[]>('/api/accounts').then((items) => { setAccounts(items); setSelected(items.filter((item) => item.enabled).map((item) => item.id)); const chatgpt = items.find(item => item.provider === 'chatgpt'); if (chatgpt) setImportAccount(chatgpt.id) }); const timer = setInterval(refresh, 2000); return () => clearInterval(timer) }, [])
  const start = async (fullIndex = false) => { setRequestError(''); try { await api('/api/sync', { method: 'POST', body: JSON.stringify({ full_index: fullIndex, account_ids: selected }) }); refresh() } catch (reason) { setRequestError(reason instanceof Error ? reason.message : String(reason)) } }
  const startImport = async () => { await api('/api/import', { method: 'POST', body: JSON.stringify({ path: importPath, account_id: importAccount, keep_raw: true }) }); refresh() }
  const active = state.job?.status === 'running'
  const last = state.last_sync || {}
  const result = state.job?.kind === 'sync' && state.job?.result ? state.job.result : last
  const failures = (result.accounts || []).filter((item: any) => item.status === 'failed' || item.error)
  const failed = result.status === 'failed'
  return (
    <div className="page-stack narrow">
      <header className="page-header"><div><span className="eyebrow">增量更新</span><h1>同步</h1></div><p>统一同步 ChatGPT、Gemini 与本机微信历史。</p></header>
      <section className="panel sync-hero">
        <div className={`sync-icon ${active ? 'spinning' : ''}`}>{active ? <LoaderCircle /> : <RefreshCw />}</div>
        <h2>{active ? '正在同步…' : '让本地档案保持最新'}</h2>
        <p>各 Provider 使用自己的增量 cursor，并统一写入本地档案。</p>
        <div className="account-checks">{accounts.map((account) => <label key={account.id}><input type="checkbox" checked={selected.includes(account.id)} onChange={(event) => setSelected(event.target.checked ? [...selected, account.id] : selected.filter((id) => id !== account.id))} />{account.display_name || account.name}<small>{account.provider === 'wechat' ? `WeChat${account.external_user_id ? ` · …${account.external_user_id.slice(-4)}` : ''} · ${account.conversations || 0} 个聊天` : `${account.provider === 'gemini' ? 'Gemini' : 'ChatGPT'} · ${account.conversations || 0} 个对话`}</small></label>)}</div>
        <div className="button-row"><button className="primary" disabled={active || !selected.length} onClick={() => start(false)}>依次同步所选账号</button><button disabled={active || !selected.length} onClick={() => start(true)}>完整索引核对</button></div>
      </section>
      <section className="panel form-panel">
        <h2>导入 ChatGPT 导出数据</h2>
        <p className="notice">支持官方导出的 ZIP 或 <code>conversations.json</code>，也支持现有的逐对话 <code>conversation.json</code> 目录。</p>
        <label>归属账号<select value={importAccount} onChange={(event) => setImportAccount(event.target.value)}>{accounts.filter(account => account.provider === 'chatgpt').map((account) => <option value={account.id} key={account.id}>{account.display_name || account.name}</option>)}</select></label>
        <label>文件或目录路径<input value={importPath} onChange={(event) => setImportPath(event.target.value)} placeholder="D:\Downloads\chatgpt-export.zip" /></label>
        <div className="button-row"><button disabled={active || !importPath} onClick={startImport}>开始导入</button></div>
      </section>
      <section className="panel sync-result">
        <header>{failed ? <AlertTriangle className="error" size={20} /> : <CheckCircle2 size={20} />}<div><span className="eyebrow">上次同步</span><h3>{result.status === 'never' ? '尚未同步' : failed ? '同步失败' : dateLabel(result.finished_at)}</h3></div></header>
        <div><span><b>{compact(result.index_items_seen || 0)}</b> 已扫描</span><span><b>{compact(result.new_conversations || 0)}</b> 新增</span><span><b>{compact(result.updated_conversations || 0)}</b> 变更</span><span><b>{compact(result.unchanged_conversations || 0)}</b> 未变</span></div>
        {(requestError || state.job?.error) && <p className="error">{requestError || state.job.error}</p>}
        {failures.map((item: any) => <p className="error sync-error" key={item.account_id}><b>{item.account_name || item.account_id}：</b>{item.error || '同步失败'}{String(item.error || '').toLowerCase().includes('auth') || String(item.error || '').toLowerCase().includes('permission') ? '。请在“设置 → Gemini 读取”更新 Cookie。' : ''}</p>)}
        {state.job?.kind === 'import' && state.job?.status === 'complete' && <p className="success">导入完成：读取 {state.job.result?.seen || 0} 条，写入 {state.job.result?.imported || 0} 条，未变化 {state.job.result?.unchanged || 0} 条，失败 {state.job.result?.failed || 0} 条。</p>}
      </section>
      <p className="privacy-note">默认不同步账户 File Library。只有 CLI 的显式 <code>--include-files</code> 选项会启用它。</p>
    </div>
  )
}
