import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Alert, Button, Card, Drawer, Empty, Input, Popover, Select, Space, Tag, Typography } from 'antd'
import { ArrowUpOutlined, CloseOutlined, PlusOutlined } from '@ant-design/icons'
import { ActiveStatus } from './ActiveStatus'
import { AnswerEvidence, type Answer } from './AnswerEvidence'
import { effortLabel, json, type Api, type ModelAccess, type ModelChoice } from './ModelAccess'
import { ChatMenu } from './ChatMenu'

type Citation = { id: string; title: string; locator: string; material: string; text: string; bbox?: number[] }
type Source = { title: string; locator: string } & (
  { kind: 'material'; citation: Citation } | { kind: 'answer'; attempt_id: string; answer: Answer }
)
type Part = { quote: string; source: Source }
type Axis = { id: string; label: string; score: number | null; n: number; coverage: number; population: number; status: string }
type CardData = {
  kind: string
  [key: string]: any
}
type Result = {
  answer: string; parts: Part[]; model?: string; cards?: CardData[]
  model_call?: ModelChoice; selection?: ModelChoice
  trace?: { tool: string; status: string; count?: number | null; student?: string; group?: string; node?: string; indicator?: string }[]
  mode?: string
  snapshot_id?: string; session_id?: string; context?: Context
}
type Context = { student: string; group: string; node: string; anchor_event?: string; snapshot_id?: string }
type Turn = { question: string; result?: Result; error?: string; context?: Context }
type Onboarding = {
  state: string; detail: Record<string, any>; dismissed: boolean; completed: boolean
  snapshot_id?: string; model_configured: boolean
}
type Props = {
  snapshot?: string
  students: { 学生编号: string; 模拟班级编号: string }[]
  groups: string[]
  nodes: string[]
  access?: ModelAccess
  api: Api
  openAccess: () => void
  openCitation: (source: Citation) => void
  openImport: () => void
  openRun: (id: string) => void
  openDraft: (id: string) => void
  openProfile: (kind: 'student' | 'group', id: string, node: string, indicator: string, snapshot?: string) => void
  openPage: (page: string) => void
  onTaskStarted: () => void
  active: boolean
}

const SESSION_KEY = 'aitolearn.chat.session'

function read(key: string, storage: Storage) {
  try { return storage.getItem(key) || '' } catch { return '' }
}
function write(key: string, value: string, storage: Storage) {
  try { value ? storage.setItem(key, value) : storage.removeItem(key) } catch { /* local preference only */ }
}


function greeting() {
  const hour = new Date().getHours()
  const time = hour < 6 ? '凌晨好' : hour < 11 ? '早上好' : hour < 14 ? '中午好' : hour < 19 ? '下午好' : '晚上好'
  const choices = [time + '，今天想了解什么？', time + '，想先看看哪次测验？', '今天想了解什么？']
  return choices[Math.floor(Math.random() * choices.length)]
}

export function LearningChat(props: Props) {
  const { api, students, groups, nodes, access } = props
  const [context, setContext] = useState<Context>({ student: '', group: '', node: '' })
  const [draft, setDraft] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [busy, setBusy] = useState(false)
  const [sessionId, setSessionId] = useState(() => read(SESSION_KEY, localStorage))
  const [source, setSource] = useState<Answer>()
  const [sourceRows, setSourceRows] = useState<Answer[]>([])
  const [restoring, setRestoring] = useState(Boolean(sessionId))
  const [pickerOpen, setPickerOpen] = useState(false)
  const [pickerKind, setPickerKind] = useState<'student' | 'group'>('student')
  const bottom = useRef<HTMLDivElement>(null)
  const pendingProposals = useRef(new Set<string>())
  const [welcome, setWelcome] = useState(greeting)
  const [choice, setChoice] = useState<ModelChoice>()
  const [legacyProvider, setLegacyProvider] = useState<string>()
  const [savingChoice, setSavingChoice] = useState(false)
  const [choiceError, setChoiceError] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [sessions, setSessions] = useState<{ id: string; title: string }[]>()
  const [historyError, setHistoryError] = useState('')
  const models = (access?.connections || []).filter(c => c.configured).flatMap(c => c.models
    .filter(m => m.enabled && m.verified.includes('default')).map(m => ({ ...m, connection: c, key: JSON.stringify([c.id, m.id]) })))
  const legacyModel = models.find(m => m.connection.id === legacyProvider)
  const preferred = choice || (legacyProvider ? legacyModel && {
    connection_id: legacyProvider, model_id: legacyModel.id, effort: 'default',
  } : access?.default && { ...access.default, effort: 'default' })
  const selected = models.find(m => m.connection.id === preferred?.connection_id && m.id === preferred.model_id)
  const efforts = selected?.efforts.filter(e => selected.connection.adapter !== 'custom' || selected.verified.includes(e)) || []
  const stale = !!access && (!!choice && (!selected || !efforts.includes(choice.effort)) || !!legacyProvider && !choice && !selected)

  async function choose(next: ModelChoice) {
    if (savingChoice) return
    setChoiceError('')
    if (!sessionId) { setChoice(next); return }
    setSavingChoice(true)
    try { setChoice(await api(`/chat/sessions/${sessionId}/selection`, json('PUT', next))) }
    catch (error) { setChoiceError((error as Error).message) }
    finally { setSavingChoice(false) }
  }
  const contextLabel = context.student || context.group || ''

  function restoreSession(id: string) {
    let active = true
    void api(`/chat/sessions/${id}`).then(session => {
      if (!active) return
      setSessionId(session.id)
      write(SESSION_KEY, session.id, localStorage)
      setChoice(session.selection)
      setLegacyProvider(session.provider)
      setChoiceError('')
      setContext({ ...{ student: '', group: '', node: '' }, ...(session.context || {}) })
      setTurns((session.turns || []).map((turn: any) => ({
        question: turn.question,
        context: turn.context,
        result: { answer: turn.answer, parts: turn.parts || [], cards: turn.cards || [], model_call: turn.model_call, model: turn.model,
          trace: turn.trace, mode: turn.mode, snapshot_id: turn.snapshot_id, session_id: session.id },
      })))
    }).catch(error => {
      if (active) {
        setHistoryError(error.message)
        if (error.status === 404 && id === sessionId) {
          setSessionId('')
          write(SESSION_KEY, '', localStorage)
          write('aitolearn.import.question', '', sessionStorage)
        }
      }
    }).finally(() => { if (active) setRestoring(false) })
    return () => { active = false }
  }

  useEffect(() => {
    if (sessionId) return restoreSession(sessionId)
    // Restore the persisted conversation on mount. The history picker restores on demand.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function showHistory(open: boolean) {
    setHistoryOpen(open)
    if (!open) return
    setHistoryError('')
    try { setSessions(await api('/chat/sessions')) }
    catch (error) { setHistoryError((error as Error).message) }
  }

  useEffect(() => {
    if (props.active && turns.length) bottom.current?.scrollIntoView({ block: 'end' })
  }, [turns.length, busy, props.active])

  const pollIds = useMemo(() => {
    const runIds = new Set<string>()
    for (const turn of turns) for (const card of turn.result?.cards || []) {
      if (card.kind === 'cleaning' && card.run_id) runIds.add(card.run_id)
    }
    return [...runIds]
  }, [turns])

  useEffect(() => {
    if (!pollIds.length) return
    let active = true
    const timer = setInterval(() => {
      for (const runId of pollIds) {
        void api(`/agent/runs/${runId}`).then(run => {
          if (!active) return
          setTurns(current => current.map(turn => !turn.result ? turn : {
            ...turn,
            result: { ...turn.result, cards: (turn.result.cards || []).map(card => card.run_id === runId ? {
              ...card, state: run.state, stage: run.stage || '', error: run.error || '',
              report: run.report || card.report, snapshot_id: run.stage === 'published' ? null : card.snapshot_id,
            } : card) },
          }))
          if (run.state === 'done' && run.stage === 'confirm') void refreshProposalFor(runId)
        }).catch(() => { /* keep the last confirmed state */ })
      }
    }, 2500)
    return () => { active = false; clearInterval(timer) }
    // Poll only the currently visible run cards.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pollIds.join(',')])

  async function refreshProposalFor(runId: string) {
    try {
      const state = await api<Onboarding>('/onboarding')
      const proposal = state.detail?.proposal
      if (!proposal || state.detail?.run_id !== runId) return
      setTurns(current => current.map(turn => !turn.result?.cards?.some(card => card.kind === 'cleaning' && card.run_id === runId) ? turn : {
        ...turn,
        result: { ...turn.result, cards: (turn.result.cards || []).some(card => card.id === proposal.id)
          ? turn.result.cards : [...(turn.result.cards || []), proposal] },
      }))
    } catch { /* the next poll retries */ }
  }

  async function send(question: string, override?: Partial<Context>, history = turns) {
    question = question.trim()
    if (!question || busy || restoring || savingChoice || stale) return
    const outgoing: Context = { ...context, ...override }
    setBusy(true)
    setDraft('')
    const shown: Turn = { question, context: outgoing }
    setTurns([...history, shown])
    try {
      const result = await api<Result>('/learning-chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question,
          session_id: sessionId,
          ...(preferred || {}),
          snapshot_id: outgoing.snapshot_id ?? props.snapshot ?? '',
          context_changed: true,
          use_tools: true,
          use_configured_models: true,
          student: outgoing.student,
          group: outgoing.group,
          node: outgoing.node,
          anchor_event: outgoing.anchor_event || '',
          history: history.filter(turn => turn.result).slice(-5).map(turn => ({
            question: turn.question, answer: turn.result!.answer.slice(0, 1200),
          })),
        }),
      })
      if (result.session_id) { setSessionId(result.session_id); write(SESSION_KEY, result.session_id, localStorage) }
      if (result.selection) setChoice(result.selection)
      if (result.context) setContext({ ...result.context, anchor_event: '' })
      setTurns([...history, { question, context: outgoing, result }])
      if (!props.snapshot && result.cards?.some(card => card.kind === 'import'))
        write('aitolearn.import.question', question, sessionStorage)
    } catch (error) {
      setTurns([...history, { question, context: outgoing, error: (error as Error).message }])
    } finally { setBusy(false) }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    void send(draft)
  }

  function newChat() {
    setWelcome(greeting())
    if (savingChoice) return
    setChoice(undefined)
    setLegacyProvider(undefined)
    setChoiceError('')
    setHistoryOpen(false)
    setTurns([])
    setContext({ student: '', group: '', node: '' })
    setSessionId('')
    write(SESSION_KEY, '', localStorage)
    setDraft('')
    setPickerOpen(false)
  }


  function setTarget(kind: 'student' | 'group', value: string) {
    setContext({ student: kind === 'student' ? value : '', group: kind === 'group' ? value : '',
      node: context.node, snapshot_id: props.snapshot })
    setPickerOpen(false)
  }

  async function readEvidence(student: string, node: string, indicator: string, snapshot?: string, group = '', item = '', includeUnscored = false) {
    try {
      const rows = await api<Answer[]>(`/profiles/evidence?${new URLSearchParams({ student, group, node, indicator, item, snapshot_id: snapshot || props.snapshot || '', include_unscored: String(includeUnscored) })}`)
      if (rows[0]) { setSourceRows(rows); setSource(rows[0]) }
      else window.alert('该指标暂无有效作答')
    } catch (error) { window.alert((error as Error).message) }
  }


  async function confirmProposal(card: CardData) {
    if (pendingProposals.current.has(card.id)) return
    pendingProposals.current.add(card.id)
    try {
      const response = await api<any>(`/chat/proposals/${card.id}/confirm`, { method: 'POST' })
      setTurns(current => current.map(turn => !turn.result ? turn : {
        ...turn,
        result: { ...turn.result, cards: (turn.result.cards || []).map(item => item.id === card.id ? response.proposal : item) },
      }))
      if (response.result?.run_id) props.onTaskStarted()
      if (card.action === 'confirm_import' && response.result?.snapshot_id)
        setContext({ student: '', group: '', node: '', snapshot_id: response.result.snapshot_id })
      if (response.result?.snapshot_id) props.onTaskStarted()
    } catch (error) {
      const message = (error as Error).message
      try {
        const body = JSON.parse(message)
        if (body.snapshot_changed && card.action === 'generate_recommendation') {
          const fresh = await api<any>(`/chat/proposals/${card.id}/refresh`, { method: 'POST' })
          setTurns(current => current.map(turn => !turn.result ? turn : {
            ...turn,
            result: { ...turn.result, cards: (turn.result.cards || []).map(item => item.id === card.id ? fresh : item) },
          }))
          window.alert('数据快照已更新，提案已按新快照更新，请重新点击确认。')
          return
        }
        window.alert(body.message || message)
        return
      } catch { /* fall through to the plain error */ }
      window.alert(message)
    } finally {
      pendingProposals.current.delete(card.id)
    }
  }

  function chooseCandidate(option: Record<string, any>, card: CardData) {
    const override: Partial<Context> = option.kind === 'student'
      ? { student: option.id, group: '', node: context.node }
      : option.kind === 'group' ? { student: '', group: option.id, node: context.node }
      : { ...context, ...card.context, node: option.id }
    setContext({ ...context, ...override })
    const question = card.intent === 'generate_recommendation'
      ? `帮我为 ${option.id} 生成学习建议`
      : card.intent === 'progress' ? `看看 ${option.id} 的 ${card.indicator} 有没有进步${card.teaching ? '，教材怎么讲？' : '？'}`
      : `看看 ${option.id} 的表现`
    void send(question, { ...override, snapshot_id: card.snapshot_id || context.snapshot_id })
  }

  useEffect(() => {
    if (!props.snapshot) return
    const question = read('aitolearn.import.question', sessionStorage)
    if (question) { setDraft(question); write('aitolearn.import.question', '', sessionStorage) }
  }, [props.snapshot])

  return <section aria-label="学情对话" className={turns.length ? "learning-chat" : "learning-chat chat-is-empty"}>
    <div className="chat-toolbar">
      <Space wrap>
        <Popover trigger="click" open={historyOpen} onOpenChange={showHistory} content={<div className="chat-history">
          {historyError && <Alert type="error" title={historyError} />}
          {!historyError && !sessions && <p>正在读取会话</p>}
          {sessions?.length === 0 && <p>暂无历史会话</p>}
          {sessions?.map(session => <Button key={session.id} type="text" disabled={session.id === sessionId}
            onClick={() => {
              setHistoryOpen(false); setRestoring(true); setDraft(''); restoreSession(session.id)
            }}>{session.title}</Button>)}
        </div>}>
          <Button disabled={busy || restoring || savingChoice}>最近对话</Button>
        </Popover>
        <Button disabled={busy || restoring || (!turns.length && !draft && !contextLabel && !context.node)} onClick={newChat}>新对话</Button>
      </Space>
    </div>
    {restoring && <ActiveStatus label="正在恢复对话" />}
    {!restoring && !turns.length && <h1 className="chat-welcome">{welcome}</h1>}
    <div className="chat-thread" aria-label="学情对话记录">
      {turns.map((turn, index) => <article key={index} className="chat-turn">
        <p className="chat-question">{turn.question}</p>
        {(turn.context?.student || turn.context?.group || turn.result?.context?.student || turn.result?.context?.group) &&
          <Tag className="chat-answer-context">当时对象 · {turn.context?.student || turn.context?.group ||
            turn.result?.context?.student || turn.result?.context?.group}</Tag>}
        {turn.error ? <div><p role="alert" className="chat-error">{turn.error}</p>
          {index === turns.length - 1 && <>
            <Space wrap>
              <Button disabled={busy} onClick={() => void send(turn.question, turn.context, turns.slice(0, index))}>重试</Button>
              {turn.error.includes('检查模型设置') && <Button onClick={props.openAccess}>打开模型设置</Button>}
            </Space>
          </>}
        </div> : turn.result ? <div>
          {turn.result.model_call ? <small>{turn.result.model_call.model_id} · {turn.result.model_call.connection_name} · {effortLabel[turn.result.model_call.effort]}</small>
            : turn.result.model && <small>{turn.result.model}</small>}
          {turn.result.answer && <p className="preserve">{turn.result.answer}</p>}
          {(turn.result.cards || []).filter(card => card.kind !== 'profile').map((card, cardIndex) => <ResultCard key={card.id || cardIndex} card={card}
            api={api} snapshot={turn.result?.snapshot_id || props.snapshot}
            readEvidence={readEvidence} confirmProposal={confirmProposal} chooseCandidate={chooseCandidate}
            openProfile={props.openProfile} openRun={props.openRun} openImport={props.openImport}
            openDraft={props.openDraft} openPage={props.openPage} />)}
          {!!(turn.result.trace?.length || turn.result.cards?.some(card => card.kind === 'profile')) &&
            <details className="chat-tool-details"><summary>{turn.result.cards?.filter(card => card.kind === 'profile')
              .map(card => [card.target?.id, card.node].filter(Boolean).join(' / ')).join('、') || '工具调用'}
              {!!turn.result.trace?.length && ' · ' + turn.result.trace.length + ' 步'}
            </summary>
            {(turn.result.cards || []).filter(card => card.kind === 'profile').map((card, cardIndex) =>
              <ResultCard key={card.id || cardIndex} card={card} api={api} snapshot={turn.result?.snapshot_id || props.snapshot}
                readEvidence={readEvidence} confirmProposal={confirmProposal} chooseCandidate={chooseCandidate}
                openProfile={props.openProfile} openRun={props.openRun} openImport={props.openImport}
                openDraft={props.openDraft} openPage={props.openPage} />)}
            {!!turn.result.trace?.length && <ol>{turn.result.trace.map((step, index) => <li key={index}>
              {({ resolve_scope: '解析查询对象', read_profile: '读取指标和覆盖人数', read_evidence: '读取原始作答',
                search_materials: '检索资料', open_import: '定位数据导入', read_cleaning: '查看清洗进度',
                propose_recommendation: '准备建议任务', open_page: '定位业务页面', read_exercises: '查找练习',
                read_jobs: '查询任务', propose_retry: '准备重试', read_material_reviews: '查询待校对资料',
                read_material_review: '读取待校对段落', propose_publish_material: '准备发布',
                propose_material_revision: '准备资料修订', read_drafts: '读取建议版本',
                propose_draft_revision: '准备建议修订', propose_attempt_correction: '准备作答修订',
              } as Record<string, string>)[step.tool] || '校验工具'}
              {[step.student || step.group, step.node, step.indicator].filter(Boolean).map(value => ' · ' + value)}
              {step.count != null && ' · ' + step.count + ' 条'}
              {' · ' + (({ ok: '已完成', waiting: '等待选择或确认', no_scored_answers: '暂无有效作答',
                queued: '排队中', running: '进行中', done: '已完成', failed: '任务失败',
                missing: '未找到对象' } as Record<string, string>)[step.status] || '请求未满足工具条件')}
            </li>)}</ol>}
          </details>}
          {(turn.result.parts || []).map(({ quote, source }, id) =>
            <blockquote key={id}>
              <p className="preserve">{quote}</p>
              <Button type="link" onClick={() => source.kind === 'answer' ? (setSourceRows([]), setSource(source.answer)) : props.openCitation(source.citation)}>
                {source.title} · {source.locator}
              </Button>
            </blockquote>)}
        </div> : <ActiveStatus label="正在回答" />}
      </article>)}
      <div ref={bottom} />
    </div>
    <div className="chat-composer-dock"><form className="chat-composer" onSubmit={submit}>
      {(stale || choiceError) && <p role="status">{choiceError || '本会话原模型或思考档位已不可用，请为下一条消息重选。'}</p>}
      <Input.TextArea aria-label="输入学情问题" placeholder={contextLabel ? `询问 ${contextLabel}，或说“帮我生成建议”` : '问一个问题，或说“看看 DC02 最近一次测验”'}
        variant="borderless" maxLength={1200} autoSize={{ minRows: 2, maxRows: 5 }} value={draft} onChange={event => setDraft(event.target.value)}
        onPressEnter={event => {
          if (!event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(draft) }
        }} />
      <div className="chat-composer-actions">
      <Space wrap className="chat-context">
        <Popover trigger="click" open={pickerOpen} onOpenChange={setPickerOpen} content={<div className="context-picker">
          <Select aria-label="上下文类型" value={pickerKind} onChange={value => setPickerKind(value)}
            options={[{ value: 'student', label: '学生' }, { value: 'group', label: '班级' }]} />
          <Select aria-label="选择上下文对象" showSearch={{ optionFilterProp: 'label' }} value={context[pickerKind] || undefined}
            placeholder={pickerKind === 'student' ? '搜索学生' : '选择班级'} style={{ minWidth: 190 }}
            options={(pickerKind === 'student' ? students.map(row => ({
              value: row.学生编号, label: row.学生编号 + ' · ' + row.模拟班级编号 })) :
              groups.map(group => ({ value: group, label: group + ' · 班级' })))}
            onChange={value => setTarget(pickerKind, value)} />
          <Select aria-label="上下文测验" value={context.node || undefined} allowClear placeholder="全部测验" style={{ minWidth: 150 }}
            onChange={node => setContext({ ...context, node: node || '', snapshot_id: props.snapshot })}
            options={nodes.map(node => ({ value: node, label: node }))} />
        </div>}>
          <Button type="text" aria-label="添加上下文" title="添加上下文" icon={<PlusOutlined />} />
        </Popover>
        {context.student && <Tag closable closeIcon={<CloseOutlined />} onClose={() => setContext({ ...context, student: '' })}>学生 {context.student}</Tag>}
        {context.group && <Tag closable closeIcon={<CloseOutlined />} onClose={() => setContext({ ...context, group: '' })}>班级 {context.group}</Tag>}
        {context.node && <Tag closable closeIcon={<CloseOutlined />} onClose={() => setContext({ ...context, node: '' })}>测验 {context.node}</Tag>}
      </Space>
        <div className="chat-submit">
          {models.length ? <ChatMenu label="当前会话模型" value={selected?.key} placeholder="选择模型" disabled={busy || restoring}
            options={models.map(m => ({ id: m.key, label: m.id + (models.some(v => v.id === m.id && v.key !== m.key) ? ' · ' + m.connection.name : '') }))}
            change={key => { const m = models.find(entry => entry.key === key); if (m) void choose({ connection_id: m.connection.id, model_id: m.id, effort: 'default' }) }} />
            : <Button type="text" onClick={props.openAccess}>模型接入</Button>}
          <ChatMenu label="思考强度" value={preferred?.effort} placeholder="思考强度" disabled={busy || restoring}
            options={efforts.map(id => ({ id, label: effortLabel[id] || id }))}
            change={effort => { if (selected) void choose({ connection_id: selected.connection.id, model_id: selected.id, effort }) }} />
          <Button className="chat-send" type="primary" shape="circle" htmlType="submit" aria-label="发送" title="发送"
            loading={busy} icon={<ArrowUpOutlined />} disabled={restoring || savingChoice || stale || !draft.trim()} />
        </div>
      </div>
    </form></div>
    <Drawer open={!!source} title="学生原始作答" size="min(850px, 100vw)" onClose={() => { setSource(undefined); setSourceRows([]) }}>
      {sourceRows.length > 1 && <Select aria-label="选择作答事件" value={source?.作答事件编号}
        options={sourceRows.map(row => ({ value: row.作答事件编号, label: [row.节点编号, row.材料编号, row.作答事件编号].join(' · ') }))}
        onChange={event => setSource(sourceRows.find(row => row.作答事件编号 === event))} />}
      {source && <AnswerEvidence key={source.作答事件编号} answer={source} />}
    </Drawer>
  </section>
}

function ResultCard({ card, api, snapshot, readEvidence, confirmProposal, chooseCandidate, openProfile, openRun, openImport, openDraft, openPage }: {
  card: CardData; api: Api; snapshot?: string
  readEvidence: (student: string, node: string, indicator: string, snapshot?: string, group?: string, item?: string, includeUnscored?: boolean) => void
  confirmProposal: (card: CardData) => void
  chooseCandidate: (option: Record<string, any>, card: CardData) => void
  openProfile: Props['openProfile']; openRun: Props['openRun']; openImport: Props['openImport']
  openPage: Props['openPage']
  openDraft: Props['openDraft']
}) {
  if (card.kind === 'navigation') return <Card size="small" className="chat-card" title={card.title}>
    <Button onClick={() => openPage(card.page)}>打开{card.title}</Button>
  </Card>
  if (card.kind === 'exercises') return <Card size="small" className="chat-card" title="相关练习">
    <ul>{card.items.map((item: { id: string; text: string }) => <li key={item.id}>{item.id} · {item.text}</li>)}</ul>
  </Card>
  if (card.kind === 'import') return <Card size="small" className="chat-card" title="数据导入">
    <Button type="primary" onClick={openImport}>打开数据导入</Button>
  </Card>
  if (card.kind === 'candidates') return <Card size="small" className="chat-card" title="请选择对象">
    <p>{card.prompt}</p><Space wrap>{(card.options || []).map((option: Record<string, any>) =>
      <Button key={option.kind + option.id} onClick={() => chooseCandidate(option, card)}>{option.label}</Button>)}</Space>
  </Card>
  if (card.kind === 'progress') return <Card size="small" className="chat-card" title="按测验查看原始作答">
    <Space wrap>{card.nodes.map((node: string) => <Button key={node}
      onClick={() => readEvidence(card.student, node, card.indicator, card.snapshot_id, '', '', true)}>{node} · 原始作答</Button>)}</Space>
  </Card>
  if (card.kind === 'profile') return <Card size="small" className="chat-card"
    title={(card.target?.kind === 'student' ? '学生 ' : '班级 ') + card.target?.id + (card.node ? ' · ' + card.node : '')}
    extra={<Button onClick={() => openProfile(card.target?.kind, card.target?.id, card.node || '', card.weakest?.id || '', card.snapshot_id || snapshot)}>打开画像</Button>}>
    <div className="chat-indicators">{(card.indicators || []).map((axis: Axis) => <div key={axis.id} className="chat-indicator">
      <div><strong>{axis.label}</strong><small>{axis.score == null ? axis.status : axis.score + '%'} · 覆盖 {axis.coverage}/{axis.population} 人 · {axis.n} 条作答</small></div>
      {card.target?.kind === 'student'
        ? <Button size="small" onClick={() => readEvidence(card.target.id, card.node || '', axis.id, card.snapshot_id || snapshot, '', card.item)}>查看作答</Button>
        : <Button size="small" onClick={() => readEvidence('', card.node || '', axis.id, card.snapshot_id || snapshot, card.target.id, card.item)}>查看原始作答</Button>}
    </div>)}</div>
    {card.item && <Typography.Text type="secondary">题号 {card.item}</Typography.Text>}
  </Card>
  if (card.kind === 'cleaning') {
    const report = card.report
    const stageLabels: Record<string, string> = {
      python: 'Python 清洗', clean_review: '模型复核', confirm: '待教师确认', published: '快照生效',
      snapshot: '读取快照', interpret: '学情解读', generate: '生成草稿', final_review: '模型终审',
      revision: '需要修订', received: '文件已接收',
    }
    return <Card size="small" className="chat-card" title="本批清洗" extra={<Button onClick={() => openRun(card.run_id)}>打开任务详情</Button>}>
      <Space wrap><Tag color={card.state === 'failed' ? 'error' : card.state === 'done' ? 'success' : 'processing'}>
        {({ queued: '排队', running: '清洗中', done: card.stage === 'confirm' ? '待确认' : '已完成', failed: '失败', stopped: '已停止' } as Record<string, string>)[card.state] || card.state}
      </Tag><Tag>{stageLabels[card.stage] || card.stage || '等待执行'}</Tag>{card.label && <Tag>{card.label}</Tag>}</Space>
      {report ? <div className="chat-counts">
        <span><strong>{report.raw_attempts}</strong>原始作答</span>
        <span><strong>{report.events}</strong>归并事件</span>
        <span><strong>{report.valid_scored}</strong>有效已评分</span>
        <span><strong>{report.issues?.length ?? 0}</strong>异常</span>
      </div> : <p>{card.state === 'failed' ? (card.error || '清洗失败，可重试。') : '等待 Python 清洗结果返回。'}</p>}
      {report?.issues?.length ? <details><summary>异常原因</summary>
        {Object.entries(report.issues.reduce((counts: Record<string, number>, issue: Record<string, any>) => {
          counts[issue.reason] = (counts[issue.reason] || 0) + 1; return counts
        }, {})).map(([reason, count]) => <div key={reason}>{reason} · {String(count)} 条</div>)}
      </details> : null}
      {card.state === 'failed' && <Space wrap className="spaced"><Button onClick={() => void api(`/jobs/${card.run_id}/retry`, { method: 'POST' }).catch(error => window.alert(error.message))}>重试当前阶段</Button>
        <Button onClick={openImport}>重新选择文件</Button></Space>}
    </Card>
  }
  if (card.kind === 'proposal') {
    const confirmed = card.status === 'confirmed'
    return <Card size="small" className="chat-card" title={card.title || '操作提案'}>
      <p>{card.payload?.before !== undefined ? (confirmed ? '修订已提交。' : '核对以下改动后再确认。')
        : card.action === 'publish_material' ? (confirmed ? '资料已进入发布队列。' : '确认后开始建立索引并发布这个资料版本。')
        : card.action === 'retry_job' ? (confirmed ? '任务已重新排队。' : '确认后重试这次失败的任务。')
        : confirmed ? (card.action === 'confirm_import' ? '这批数据已确认，后续诊断和建议读取同一快照。'
        : '建议任务已开始，进度与终审结果会保留在任务详情。')
        : (card.action === 'confirm_import' ? '复核已完成。确认后建立数据快照，后续诊断和建议都读取该快照。'
        : '将使用当前快照生成学情解读、建议草稿并执行独立终审。')}</p>
      {(card.payload?.student || card.payload?.group) && <p><Tag>{card.payload.student || card.payload.group}</Tag><Tag>{card.payload.node || '全部测验'}</Tag></p>}
      {card.payload?.before !== undefined && <dl>
        <dt>修改前 {card.payload.field || ''}</dt><dd className="preserve">{card.payload.before || '空白'}</dd>
        <dt>修改后</dt><dd className="preserve">{card.payload.after}</dd>
      </dl>}
      {card.details && <p>{card.details}</p>}
      <Space wrap>
        {!confirmed && <Button type="primary" onClick={() => confirmProposal(card)}>{card.action === 'generate_recommendation' ? '确认生成' : '确认并继续'}</Button>}
        {confirmed && card.result?.run_id && <ProposalTask id={card.result.run_id} api={api}
          openRun={openRun} openDraft={openDraft} openPage={openPage} />}
        {confirmed && card.result?.snapshot_id && <Tag color="success">快照已启用</Tag>}
        {confirmed && card.action === 'revise_material' && <Button onClick={() => openPage('materials')}>打开课程与资料</Button>}
      </Space>
    </Card>
  }
  if (card.kind === 'task') return <Card size="small" className="chat-card" title="任务">
    <Button onClick={() => openRun(card.run_id)}>查看运行详情</Button>
  </Card>
  return null
}

function ProposalTask({ id, api, openRun, openDraft, openPage }: Pick<Props, 'api' | 'openRun' | 'openDraft' | 'openPage'> & { id: string }) {
  const [task, setTask] = useState<{ state: string; stage?: string; kind: string; draft_id?: string; error?: string; model_retrying?: boolean }>()
  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    async function poll() {
      try {
        const value = await api<NonNullable<typeof task>>('/chat/tasks/' + encodeURIComponent(id))
        if (!active) return
        setTask(value)
        if (value.state === 'done' || value.state === 'failed') return
      } catch { /* Retry transient read failures. */ }
      if (active) timer = setTimeout(poll, 2500)
    }
    void poll()
    return () => { active = false; clearTimeout(timer) }
  }, [api, id])
  return <Space wrap>
    {task?.state === 'running' && <ActiveStatus label={({ snapshot: '读取快照', interpret: '学情解读',
      generate: '生成草稿', final_review: '模型终审' } as Record<string, string>)[task.stage || ''] || '正在处理任务'} />}
    {task?.state === 'running' && task.model_retrying && <Tag>正在重试</Tag>}
    {task && task.state !== 'running' && <Tag>{({ queued: '排队中', running: '执行中', done: '已完成', failed: '失败', stopped: '已停止' } as Record<string, string>)[task.state] || task.state}
</Tag>}
    {task?.error && <Typography.Text type="danger">{task.error}</Typography.Text>}
    <Button onClick={() => task && !['clean', 'generate', 'recommendation_review'].includes(task.kind)
      ? openPage('settings') : openRun(id)}>打开任务</Button>
    {task?.draft_id && <Button type="primary" onClick={() => openDraft(task.draft_id!)}>查看建议并审核</Button>}
  </Space>
}
