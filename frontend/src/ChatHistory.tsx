import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Input, Skeleton } from 'antd'
import { EditOutlined } from '@ant-design/icons'
import { json, type Api } from './ModelAccess'

export type SessionSummary = { id: string; title: string; custom_title?: string | null; last_active_at?: number | null }
const relative = new Intl.RelativeTimeFormat('zh-CN', { numeric: 'always' })
const dateFormat = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium' })
const fullFormat = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' })

function SessionTime({ at, now }: { at: number; now: number }) {
  const date = new Date(at * 1000)
  if (!Number.isFinite(date.getTime())) return null
  const seconds = Math.max(0, (now - date.getTime()) / 1000)
  const label = seconds < 60 ? '刚刚' : seconds < 3600 ? relative.format(-Math.floor(seconds / 60), 'minute')
    : seconds < 86400 ? relative.format(-Math.floor(seconds / 3600), 'hour')
    : seconds < 7 * 86400 ? relative.format(-Math.floor(seconds / 86400), 'day') : dateFormat.format(date)
  const full = fullFormat.format(date)
  return <time dateTime={date.toISOString()} title={full} aria-label={'上次活跃：' + full}>{label}</time>
}

export function ChatHistory({ api, sessions, active, loading, error, retry, select, saved }: {
  api: Api; sessions?: SessionSummary[]; active: string; loading: boolean; error: string; retry: () => void
  select: (id: string) => void; saved: (session: SessionSummary) => void
}) {
  const [editing, setEditing] = useState('')
  const [value, setValue] = useState('')
  const [editError, setEditError] = useState('')
  const [saving, setSaving] = useState(false)
  const [now, setNow] = useState(Date.now)
  const renameButton = useRef<HTMLButtonElement | null>(null)
  const focusAfter = useRef('')
  const composing = useRef(false)
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30000)
    return () => window.clearInterval(timer)
  }, [])
  useEffect(() => {
    if (!editing && focusAfter.current) {
      focusAfter.current = ''
      renameButton.current?.focus()
    }
  }, [editing])
  function closeEdit() { focusAfter.current = editing; setEditing('') }
  async function save(title: string | null) {
    if (saving) return
    const clean = title?.trim()
    if (title !== null && (!clean || Array.from(clean).length > 72)) { setEditError('请输入 1–72 个字'); return }
    setSaving(true); setEditError('')
    try {
      saved(await api<SessionSummary>(`/chat/sessions/${editing}/title`, json('PUT', { title: clean ?? null })))
      closeEdit()
    } catch (error) { setEditError((error as Error).message) }
    finally { setSaving(false) }
  }
  return <div className="chat-history" aria-label="会话历史" aria-busy={loading}>
    {error && <Alert type="error" title={error} action={<Button onClick={retry}>重试</Button>} />}
    {!sessions && loading && <div role="status" aria-label="正在读取会话"><Skeleton active title={false} paragraph={{rows: 3}} /></div>}
    {!error && !loading && sessions?.length === 0 && <p className="chat-history-empty">暂无历史会话。发送一条消息后，对话会出现在这里。</p>}
    {sessions?.map(session => editing === session.id ? <form className="chat-history-edit" key={session.id} onSubmit={e => { e.preventDefault(); if (!composing.current) void save(value) }}>
      {session.last_active_at != null && <small>上次活跃：{fullFormat.format(session.last_active_at * 1000)}</small>}
      <Input autoFocus aria-label="会话名称" aria-invalid={!!editError} readOnly={saving} value={value} onChange={e => { setValue(e.target.value); setEditError('') }}
        onCompositionStart={() => { composing.current = true }} onCompositionEnd={() => { composing.current = false }}
        onKeyDown={e => {
          if (composing.current || e.nativeEvent.isComposing || e.keyCode === 229) {
            if (e.key === 'Enter') e.stopPropagation()
            return
          }
          if (e.key === 'Enter') { e.preventDefault(); void save(value) }
          if (e.key === 'Escape') { e.stopPropagation(); if (!saving) closeEdit() }
        }} />
      {editError && <p role="alert" className="chat-history-edit-error">{editError}</p>}
      <div className="chat-history-actions">
        <Button type="primary" htmlType="submit" loading={saving}>保存</Button><Button disabled={saving} onClick={closeEdit}>取消</Button>
        {session.custom_title && <Button type="link" disabled={saving} onClick={() => void save(null)}>恢复默认标题</Button>}
      </div>
    </form> : <div key={session.id} className="chat-history-row" data-current={session.id === active}>
      <button type="button" className="chat-history-select" aria-current={session.id === active ? 'true' : undefined}
        onClick={() => select(session.id)} disabled={saving || !!editing}>
        <span className="chat-history-title" title={session.title}>{session.title}</span>
        {session.last_active_at != null && <SessionTime at={session.last_active_at} now={now} />}
        {session.id === active && <span className="chat-history-current">当前</span>}
      </button>
      <Button ref={el => { if (focusAfter.current === session.id) renameButton.current = el as HTMLButtonElement | null }}
        type="text" icon={<EditOutlined />} title="重命名" aria-label={'重命名：' + session.title}
        disabled={loading || !!editing} onClick={() => { composing.current = false; setEditError(''); setValue(session.title); setEditing(session.id) }} />
    </div>)}
  </div>
}
