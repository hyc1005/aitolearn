import { useEffect, useState, type FormEvent } from 'react'
import { Alert, Button, Input, Space } from 'antd'

export type ModelAccess = {
  active: string
  providers: { id: string; name: string; model: string; base_url: string; env: string; configured: boolean }[]
}
export type Api = <T = any>(path: string, options?: RequestInit) => Promise<T>

export function ModelAccessPage({ access, api, update }: { access?: ModelAccess; api: Api; update: (value: ModelAccess) => void }) {
  const [selection, setSelection] = useState('')
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState('')
  const [feedback, setFeedback] = useState<{ error: boolean; text: string }>()
  const provider = access?.providers.find(item => item.id === (selection || access.active))
  useEffect(() => { setKey(''); setFeedback(undefined) }, [selection])
  if (!access || !provider) return <p>正在读取模型接入设置…</p>

  async function save(event: FormEvent) {
    event.preventDefault()
    if (!provider || busy) return
    setBusy('save')
    setFeedback(undefined)
    try {
      update(await api('/models/settings/' + provider.id, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_key: key }),
      }))
      setKey('')
      setFeedback({ error: false, text: '已启用 ' + provider.name })
    } catch (error) { setFeedback({ error: true, text: (error as Error).message }) }
    finally { setBusy('') }
  }

  async function test() {
    if (!provider || busy) return
    setBusy('test')
    setFeedback(undefined)
    try {
      await api('/models/test/' + provider.id, { method: 'POST' })
      setFeedback({ error: false, text: '已收到模型平台的测试回复' })
    } catch (error) { setFeedback({ error: true, text: (error as Error).message }) }
    finally { setBusy('') }
  }

  return <section className="model-access">
    <fieldset disabled={!!busy}>
      <legend>选择平台</legend>
      {access.providers.map(item => <label key={item.id} className="provider-choice">
        <input type="radio" name="provider" checked={provider.id === item.id} onChange={() => setSelection(item.id)} />
        <span><strong>{item.name}</strong><span>{item.model}</span></span>
        <span>{item.id === access.active && item.configured ? '当前启用' : item.configured ? '已配置' : '待配置'}</span>
      </label>)}
    </fieldset>
    <form onSubmit={save} className="provider-form">
      <dl><dt>接口地址</dt><dd><code>{provider.base_url}</code></dd><dt>默认模型</dt><dd>{provider.model}</dd></dl>
      <label htmlFor="provider-key">API 密钥</label>
      <Input.Password id="provider-key" autoComplete="off" maxLength={2048} value={key}
        placeholder={provider.configured ? '已配置，可填写替换密钥' : '粘贴平台 API 密钥'}
        onChange={event => setKey(event.target.value)} disabled={!!busy} />
      <p>密钥保存在本机凭据库。环境变量 <code>{provider.env}</code> 存在时优先使用。</p>
      <Space wrap>
        <Button type="primary" htmlType="submit" loading={busy === 'save'} disabled={!!busy || (!key.trim() && !provider.configured)}>保存并启用</Button>
        <Button onClick={() => void test()} loading={busy === 'test'} disabled={!!busy || !provider.configured || !!key.trim()}>测试连接</Button>
      </Space>
      {feedback && <Alert type={feedback.error ? 'error' : 'success'} title={feedback.text} showIcon />}
    </form>
  </section>
}
