import { useState, type FormEvent } from 'react'
import { Alert, Button, Input, Select, Space } from 'antd'
import { ActiveStatus } from './ActiveStatus'

export type ModelChoice = { connection_id: string; model_id: string; effort: string; connection_name?: string }
export type ModelEntry = { id: string; enabled: boolean; mode: string; efforts: string[]; verified: string[]; legacy?: boolean }
export type Connection = { id: string; name: string; adapter: string; base_url: string; configured: boolean; env: string; models: ModelEntry[]; catalog: string[] }
export type ModelAccess = { default?: Omit<ModelChoice, 'effort'>; connections: Connection[]; saved_id?: string; background: string }
export type Api = <T = any>(path: string, options?: RequestInit) => Promise<T>
export const effortLabel: Record<string, string> = { default: '平台默认', off: '关闭', on: '开启', minimal: '最低', low: '低', medium: '中', high: '高', xhigh: '很高', max: '最高' }
export const json = (method: string, body: unknown): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export function ModelAccessPage({ access, api, update }: { access?: ModelAccess; api: Api; update: (value: ModelAccess) => void }) {
  const [selection, setSelection] = useState('')
  const [catalogNotice, setCatalogNotice] = useState('')
  if (!access) return <p>正在读取模型接入设置…</p>
  const id = selection || access.default?.connection_id || 'opencode'
  const connection = access.connections.find(c => c.id === id)
  const background = access.connections.filter(c => c.adapter !== 'custom' && c.configured)
  return <>
    <label>后台默认平台 <Select aria-label="后台默认平台" value={access.background} style={{ minWidth: 150 }}
      options={background.map(c => ({ value: c.id, label: c.name }))}
      onChange={async value => {
        try {
          await api('/models/settings/' + value, json('PUT', {}))
          update(await api('/models/connections'))
        } catch (error) { window.alert((error as Error).message) }
      }} /></label>
    <section className="model-access">
      <fieldset><legend>选择连接</legend>
        {access.connections.map(c => <label key={c.id} className="provider-choice">
          <input type="radio" name="connection" checked={id === c.id} onChange={() => setSelection(c.id)} />
          <strong>{c.name}</strong><span>{c.configured ? '已配置' : '待配置'}</span>
        </label>)}
        <Button onClick={() => setSelection('new')}>添加自定义连接</Button>
      </fieldset>
      <div className="provider-form">
        {catalogNotice && <Alert type="warning" title={catalogNotice} showIcon closable onClose={() => setCatalogNotice('')} />}
        <ConnectionEditor key={id} connection={connection} access={access} api={api} update={update} select={setSelection} notice={setCatalogNotice} />
      </div>
    </section>
  </>
}

function ConnectionEditor({ connection: c, access, api, update, select, notice }: { connection?: Connection; access: ModelAccess; api: Api; update: (value: ModelAccess) => void; select: (id: string) => void; notice: (value: string) => void }) {
  const [name, setName] = useState(c?.name || '')
  const [address, setAddress] = useState(c?.base_url || '')
  const [key, setKey] = useState('')
  const [focused, setFocused] = useState<string>()
  const [manual, setManual] = useState('')
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState<{ error: boolean; text: string }>()
  const [testTiming,setTestTiming] = useState<{start:number; end?:number}>()
  const [probe, setProbe] = useState<Record<string, string>>({})
  const [cap, setCap] = useState<Record<string, { mode: string; efforts: string[] }>>({})
  const path = '/models/connections/' + (c?.id || 'new')
  const ids = [...new Set([...(c?.models.map(m => m.id) || []), ...(c?.catalog || [])])]
  async function action(work: () => Promise<void>, ok: string) {
    setBusy(true); setFeedback(undefined); notice('')
    try { await work(); setFeedback({ error: false, text: ok }) }
    catch (error) { setFeedback({ error: true, text: (error as Error).message }) }
    finally { setBusy(false) }
  }
  function save(event: FormEvent) {
    event.preventDefault()
    void action(async () => {
      const saved = await api<ModelAccess>(path, json('PUT', { name, base_url: address, api_key: key || undefined }))
      update(saved); setKey('')
      try { if (saved.connections.find(entry => entry.id === saved.saved_id)?.configured) update(await api(`/models/connections/${saved.saved_id}/catalog`, { method: 'POST' })) }
      catch (error) { notice('连接已保存。' + (error as Error).message) }
      finally { if (!c && saved.saved_id) select(saved.saved_id) }
    }, '连接已保存')
  }
  const saveModel = async (id: string, enabled: boolean, mode = 'unknown', efforts: string[] = []) => {
    update(await api(path + '/model', json('PUT', { model_id: id, enabled, mode, efforts })))
  }
  return <div className="provider-form">
    <form className="provider-form" onSubmit={save}>
      <label>连接名称 <Input value={name} required maxLength={100} onChange={e => setName(e.target.value)} /></label>
      <label>API 地址 <Input value={address} readOnly={!!c && c.adapter !== 'custom'} required maxLength={500} placeholder="https://example.com/v1" onChange={e => setAddress(e.target.value)} /></label>
      <label>API 密钥 <Input.Password autoComplete="off" maxLength={2048} value={key} placeholder={c?.configured ? '已保存，可填写替换密钥' : '输入 API 密钥'} onChange={e => setKey(e.target.value)} /></label>
      <p>密钥保存在本机凭据库。更改自定义地址须重新确认密钥。{c?.env ? ` 环境变量 ${c.env} 优先。` : ''}</p>
      <Space wrap><Button type="primary" htmlType="submit" disabled={busy}>保存连接</Button>
        {c && <Button disabled={busy || !c.configured} onClick={() => void action(async () => update(await api(path + '/catalog', { method: 'POST' })), '目录已刷新')}>刷新目录</Button>}</Space>
    </form>
    {testTiming && (testTiming.end ? <small>模型测试耗时 {((testTiming.end-testTiming.start)/1000).toFixed(1)} 秒</small> : <ActiveStatus label="等待模型测试" started={testTiming.start} />)}
    {feedback && <Alert type={feedback.error ? 'warning' : 'success'} title={feedback.text} showIcon />}
    {c && <fieldset disabled={busy} className="connection-models"><legend>模型目录和聊天清单</legend>
      <small>已启用 {c.models.filter(m => m.enabled).length} 个 · 默认 {access.default?.model_id || '未设置'}</small>
      <Select aria-label="启用多个聊天模型" mode="multiple" maxTagCount={2} listHeight={260} showSearch={{ optionFilterProp:'label' }}
        disabled={busy} value={c.models.filter(m=>m.enabled).map(m=>m.id)}
        options={ids.map(id=>({value:id,label:id}))}
        onChange={next=>void action(async ()=>{
          for (const id of ids.filter(id=>next.includes(id) !== c.models.some(m=>m.id === id && m.enabled))) {
            const m = c.models.find(entry=>entry.id === id)
            await saveModel(id,next.includes(id),m?.mode,m?.efforts.filter(e=>!['default','off','on'].includes(e)))
            setFocused(id)
          }
        },'聊天清单已保存；尚未通过默认档测试的模型不会出现在聊天菜单')} />
      <Select aria-label="搜索模型目录" placeholder="搜索并选择模型" showSearch={{ optionFilterProp:'label' }} listHeight={260}
        value={focused} onChange={setFocused} options={[
        {label:'已启用',options:ids.filter(id => c.models.some(m => m.id === id && m.enabled)).map(id => ({value:id,label:id}))},
        {label:'已登记',options:ids.filter(id => c.models.some(m => m.id === id && !m.enabled)).map(id => ({value:id,label:id}))},
        {label:'其他目录模型',options:ids.filter(id => !c.models.some(m => m.id === id)).map(id => ({value:id,label:id}))},
      ]} />
      <Space wrap><Input aria-label="手动登记模型 ID" placeholder="手动填写模型 ID" value={manual} onChange={e => setManual(e.target.value)} />
        <Button disabled={busy || !manual.trim() || c.models.some(m => m.id === manual.trim())} onClick={() => void action(async () => { await saveModel(manual.trim(), false); setFocused(manual.trim()); setManual('') }, '模型已登记，请测试后勾选使用')}>登记</Button></Space>
      {ids.filter(id => id === focused).map(id => {
        const m = c.models.find(entry => entry.id === id)
        const modes = cap[id] || { mode: m?.mode || 'unknown', efforts: m?.efforts.filter(e => e !== 'default') || [] }
        const current = m?.efforts.includes(probe[id]) ? probe[id] : 'default'
        const isDefault = access.default?.connection_id === c.id && access.default.model_id === id
        return <div className="connection-model" key={id}>
          <label><input type="checkbox" checked={!!m?.enabled} onChange={e => void action(() => saveModel(id, e.target.checked, m?.mode, m?.efforts.filter(v => v !== 'default' && v !== 'on' && v !== 'off')), '聊天清单已保存')} /> {id}</label>
          <small>{m?.verified.includes('default') ? `可聊天 · ${m.verified.map(e => effortLabel[e]).join('、')}` : '尚未通过聊天测试'}</small>
          {!m && <Button size="small" onClick={() => void action(() => saveModel(id, false), '已登记，请测试')}>登记并测试</Button>}
          {m && <>
            {c.adapter === 'custom' && <Space wrap>
              <Select aria-label={`${id} 思考能力`} value={modes.mode} options={[{ value: 'unknown', label: '平台默认' }, { value: 'switch', label: '开启／关闭（thinking）' }, { value: 'effort', label: '多档位（reasoning_effort）' }]} onChange={mode => setCap({ ...cap, [id]: { mode, efforts: [] } })} />
              {modes.mode === 'effort' && <Select aria-label={`${id} 登记档位`} mode="multiple" style={{ minWidth: 140 }} value={modes.efforts} options={['minimal', 'low', 'medium', 'high', 'xhigh', 'max'].map(value => ({ value, label: effortLabel[value] }))} onChange={efforts => setCap({ ...cap, [id]: { ...modes, efforts } })} />}
              <Button size="small" disabled={busy || !cap[id]} onClick={() => void action(async () => {
                await saveModel(id, m.enabled, modes.mode, modes.efforts); setCap(old => { const next = { ...old }; delete next[id]; return next })
              }, '思考能力已保存，请测试对应档位')}>保存能力</Button>
            </Space>}
            <Space wrap>
              <Select aria-label={`${id} 测试强度`} value={current} options={m.efforts.map(value => ({ value, label: effortLabel[value] }))} onChange={effort => setProbe({ ...probe, [id]: effort })} />
              <Button size="small" disabled={busy || !c.configured} onClick={() => void action(async () => {
                const start=Date.now(); setTestTiming({start})
                try { update(await api(path + '/test', json('POST', { model_id:id,effort:current }))) }
                finally { setTestTiming({start,end:Date.now()}) }
              }, `${id} / ${effortLabel[current]}：聊天测试通过`)}>测试</Button>
              <Button size="small" disabled={busy || !m.enabled || !m.verified.includes('default') || isDefault} onClick={() => void action(async () => update(await api('/models/chat-default/' + c.id, json('PUT', { model_id: id }))), '新会话默认模型已保存')}>{isDefault ? '新会话默认' : '设为默认'}</Button>
            </Space>
          </>}
        </div>
      })}
    </fieldset>}
  </div>
}
