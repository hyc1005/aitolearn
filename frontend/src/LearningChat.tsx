import { useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { Alert, Button, Card, Drawer, Empty, Input, Popover, Select, Space, Tag, Tooltip, Typography } from 'antd'
import type { TextAreaRef } from 'antd/es/input/TextArea'
import { ArrowDownOutlined, ArrowUpOutlined, BarChartOutlined, CloseOutlined, FileSearchOutlined, HistoryOutlined, PlusOutlined, ProfileOutlined, UserOutlined } from '@ant-design/icons'
import { ActiveStatus } from './ActiveStatus'
import { AnswerEvidence, type Answer } from './AnswerEvidence'
import { effortLabel, json, type Api, type ModelAccess, type ModelChoice } from './ModelAccess'
import { ChatMenu } from './ChatMenu'
import { ChatHistory, type SessionSummary } from './ChatHistory'
import { ChatAttachments, type Reference } from './ChatAttachments'

type Citation = { id: string; title: string; locator: string; material: string; text: string; bbox?: number[] }
type Source = { title: string; locator: string } & (
  { kind: 'material'; citation: Citation } | { kind: 'answer'; attempt_id: string; answer: Answer }
)
type Part = { quote: string; source: Source }
type Axis = { id: string; label: string; score: number | null; earned?: number; possible?: number; n: number; coverage: number; population: number; status: string }

// Interpolate through yellow so the scale follows the same red–yellow–green
// path, rather than passing through a muddy RGB midpoint.
function indicatorColor(rate: number): string {
  const t = Math.max(0, Math.min(100, rate)) / 100
  const start = t <= .5 ? [255, 0, 0] : [255, 255, 0]
  const end = t <= .5 ? [255, 255, 0] : [56, 255, 79]
  const fraction = t <= .5 ? t * 2 : (t - .5) * 2
  return `rgb(${start.map((value, i) => Math.round(value + (end[i] - value) * fraction)).join(' ')})`
}

function AnswerText({ text }: { text: string }) {
  // Older sessions may contain several labelled sections in a single paragraph.
  const formatted = text.replace(/([。；;])\s*(?=(?:薄弱|中等|较强|总体情况|后续建议|下一步)[：:])/g, '$1\n')
  const metadata = /(（[^（）\n]*）|\([^()\n]*\))/g
  const isSupplement = (part: string) => /^[（(]/.test(part)
    && /(?:\d\s*(?:分|题|条|次|人|%|％|个百分点)|(?:覆盖|样本数|人数|作答|评分|得分|满分|均分|平均分)\s*\d|[A-Za-z]+\d+[A-Za-z0-9]*)/.test(part)
    && !/(?:样本有限|样本不足|覆盖不足|无法|不能|不确定|不足以|可能|仅供|注意|限制|风险|尚未|未评分|未作答|缺考|待评分)/.test(part)
  return <div className="preserve chat-reply-text">{formatted.split('\n').map((line, index) =>
    <div className="chat-answer-line" key={index}>{line.split(metadata).map((part, partIndex) =>
      isSupplement(part)
        ? <span className="chat-answer-metadata" key={partIndex}>{part}</span> : part
    )}</div>)}</div>
}
type CardData = {
  kind: string
  [key: string]: any
}
type Result = {
  answer: string; parts: Part[]; model?: string; cards?: CardData[]
  model_call?: ModelChoice; selection?: ModelChoice; elapsed_ms?: number
  trace?: ToolStep[]
  mode?: string
  question?: string
  title?: string
  snapshot_id?: string; session_id?: string; context?: Context
}
type Context = { student: string; group: string; node: string; anchor_event?: string; snapshot_id?: string }
type ToolStep = { tool: string; status: string; count?: number | null; student?: string; group?: string; node?: string; indicator?: string; origin?: string }
type ProcessStep = { type: 'reasoning'; round?: string; text: string }
  | { type: 'tool'; call?: string; step: ToolStep }
  | { type: 'fallback' }
type Turn = { question: string; result?: Result; error?: string; context?: Context; started?: number; elapsed?: number; attachments?: Reference[];
  process?: ProcessStep[] }
type Onboarding = {
  state: string; detail: Record<string, any>; dismissed: boolean; completed: boolean
  snapshot_id?: string; model_configured: boolean
}
type Props = {
  snapshot?: string
  pendingRun?: string
  openAdvice: (target: Pick<Context, 'student' | 'group' | 'node'>) => void
  students: { 学生编号: string; 模拟班级编号: string }[]
  groups: string[]
  nodes: string[]
  access?: ModelAccess
  api: Api
  openAccess: () => void
  openCitation: (source: Citation, trigger: HTMLElement) => void
  openImport: () => void
  openMaterial: (id: string) => void
  openRun: (id: string) => void
  openDraft: (id: string) => void
  openProfile: (kind: 'student' | 'group', id: string, node: string, indicator: string, snapshot?: string) => void
  openPage: (page: string) => void
  onTaskStarted: () => void
  active: boolean
  focusChanged: (active: boolean) => void
}

const SESSION_KEY = 'aitolearn.chat.session'
const TOOL_LABELS: Record<string, string> = {
  resolve_scope:'解析查询对象', read_profile:'读取指标和覆盖人数', read_evidence:'读取原始作答',
  search_materials:'检索资料', read_exercises:'查找练习', read_follow_ups:'读取练后回看',
  read_distribution:'读取班级分布', read_attachments:'读取附件', read_cleaning:'查看清洗进度',
  read_cleaning_issues:'读取清洗异常', read_jobs:'查询任务', read_job_detail:'读取任务详情',
  propose_retry:'准备重试', propose_stop_job:'准备停止任务', read_material_reviews:'查询待校对资料',
  read_material_list:'查询资料列表', read_material_review:'读取待校对段落', read_material_page:'读取资料页',
  propose_publish_material:'准备发布资料', propose_material_revision:'准备资料修订', read_drafts:'读取建议版本',
  read_draft_detail:'读取终审与依据', propose_draft_revision:'准备建议修订', propose_exercise_revision:'准备练习修订',
  propose_approve_draft:'准备确认建议', read_follow_up_options:'读取练后跟踪选项', propose_follow_up:'准备登记练后跟踪',
  ask_clarification:'等待补充信息', prepare_formal_export:'准备导出成果', propose_attempt_correction:'准备作答修订',
  propose_recommendation:'准备建议任务', open_page:'定位业务页面', read_progress:'汇总历次成绩',
  queue_student_import:'启动学情清洗', queue_material_extract:'启动资料解析',
}

async function streamChat(body: unknown, onEvent: (event: any) => void): Promise<Result> {
  const response = await fetch('/api/learning-chat/stream', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new Error(typeof error.detail === 'string' ? error.detail : '无法启动对话，请检查服务状态')
  }
  if (!response.body) throw new Error('无法读取对话响应')
  const reader = response.body.getReader(), decoder = new TextDecoder()
  let buffer = '', result: Result | undefined
  while (true) {
    const chunk = await reader.read()
    buffer += decoder.decode(chunk.value, { stream: !chunk.done })
    const frames = buffer.split(/\r?\n\r?\n/)
    buffer = frames.pop() || ''
    for (const frame of frames) {
      const data = frame.split(/\r?\n/).filter(line => line.startsWith('data:')).map(line => line.slice(5).trim()).join('\n')
      if (!data) continue
      const event = JSON.parse(data)
      if (event.type === 'error') throw new Error(event.message || '对话失败，请重试')
      if (event.type === 'complete') result = event.result
      else onEvent(event)
    }
    if (chunk.done) break
  }
  if (!result) throw new Error('连接中断，未收到完整回答；请检查历史后重试。')
  return result
}

function read(key: string, storage: Storage) {
  try { return storage.getItem(key) || '' } catch { return '' }
}
function write(key: string, value: string, storage: Storage) {
  try { value ? storage.setItem(key, value) : storage.removeItem(key) } catch { /* local preference only */ }
}

function updateProcess(steps: ProcessStep[], event: any): ProcessStep[] {
  if (event.type === 'reasoning' && typeof event.text === 'string' && event.text) {
    const remaining = 120000 - steps.reduce((total, step) => total + (step.type === 'reasoning' ? step.text.length : 0), 0)
    if (remaining <= 0) return steps
    const text = event.text.slice(0, remaining)
    const last = steps[steps.length - 1]
    if (last?.type === 'reasoning' && last.round === event.round_id) {
      return [...steps.slice(0, -1), { ...last, text: last.text + text }]
    }
    return [...steps, { type: 'reasoning', round: event.round_id, text }]
  }
  if (event.type === 'reasoning_unavailable') return [...steps, { type: 'fallback' }]
  if (event.type === 'tool_start') return [...steps, { type: 'tool', call: event.call_id, step: { tool: event.tool, status: 'running' } }]
  if (event.type !== 'tool_end') return steps
  const index = steps.findIndex(item => item.type === 'tool' && item.step.status === 'running' &&
    (event.call_id ? item.call === event.call_id : item.step.tool === event.tool))
  const completed: ProcessStep = { type: 'tool', call: event.call_id, step: event }
  return index < 0 ? [...steps, completed] : steps.map((item, i) => i === index ? completed : item)
}

function toolSummary(step: ToolStep) {
  const status: Record<string, string> = {
    ok: '已完成', waiting: '等待选择或确认', no_scored_answers: '暂无有效作答', queued: '排队中',
    running: '执行中', done: '已完成', failed: '任务失败', missing: '未找到对象', stopped: '已停止',
  }
  return [TOOL_LABELS[step.tool] || '校验工具', step.student || step.group, step.node, step.indicator,
    step.count != null ? step.count + (step.origin ? ' 份' : ' 条') : '',
    step.origin === 'validated_fallback' ? '规则分流' : step.origin === 'agent' ? 'Agent 工具调用' : '',
    status[step.status] || '请求未满足工具条件'].filter(Boolean).join(' · ')
}

function TurnProcess({ turn, children }: { turn: Turn; children?: React.ReactNode }) {
  const live = !turn.result && !turn.error
  const steps: ProcessStep[] = turn.process?.length ? turn.process
    : (turn.result?.trace || []).map(step => ({ type: 'tool', step }))
  if (!steps.length && !children && turn.result?.mode !== 'tools' && !turn.result?.model_call) return null
  const toolCount = steps.filter(step => step.type === 'tool').length
  const hasReasoning = steps.some(step => step.type === 'reasoning')
  return <details key={live ? 'live' : 'finished'} className="chat-tool-details" open={live || undefined}>
    <summary>{live ? '正在思考与执行' : toolCount ? `执行工具 ${toolCount} 次` : '已思考'}</summary>
    {!!steps.length && <ol className="chat-process-timeline">{steps.map((item, index) =>
      <li key={index}>{item.type === 'reasoning' ? <p className="preserve chat-reasoning">{item.text}</p>
        : item.type === 'fallback' ? '已切换为普通模型请求'
        : item.step.status === 'running' && turn.error ? (TOOL_LABELS[item.step.tool] || '校验工具') + ' · 未收到执行结果'
        : toolSummary(item.step)}</li>)}</ol>}
    {!live && !hasReasoning && !turn.error && <p>{turn.started
      ? '本轮未收到可展示的思考内容。' : '思考文本仅在生成时所在的页面保留。'}</p>}
    {children}
  </details>
}


export function LearningChat(props: Props) {
  const { api, students, groups, nodes, access } = props
  const [context, setContext] = useState<Context>({ student: '', group: '', node: '' })
  const [draft, setDraft] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [activeTurn, setActiveTurn] = useState(0)
  const [busy, setBusy] = useState(false)
  const [sessionId, setSessionId] = useState(() => read(SESSION_KEY, localStorage))
  const [sessionTitle, setSessionTitle] = useState('')
  const [source, setSource] = useState<Answer>()
  const [sourceRows, setSourceRows] = useState<Answer[]>([])
  const [restoring, setRestoring] = useState(Boolean(sessionId))
  const [pickerOpen, setPickerOpen] = useState(false)
  const [attachmentOpen, setAttachmentOpen] = useState(false)
  const [attachments, setAttachments] = useState<Reference[]>([])
  const [pickerKind, setPickerKind] = useState<'student' | 'group'>('student')
  const bottom = useRef<HTMLDivElement>(null)
  const thread = useRef<HTMLDivElement>(null)
  useEffect(() => { props.focusChanged(props.active && turns.length > 0) }, [props.active, turns.length > 0])
  const pendingProposals = useRef(new Set<string>())
  const composerDock = useRef<HTMLDivElement>(null)
  const composerInput = useRef<TextAreaRef>(null)
  useEffect(() => {
    const dock = composerDock.current
    const area = thread.current
    if (!dock || !area) return
    const update = () => area.style.setProperty('--composer-space', (dock.offsetHeight + 24) + 'px')
    const observer = new ResizeObserver(update)
    observer.observe(dock)
    update()
    return () => observer.disconnect()
  }, [])
  const followLatest = useRef(true)
  const evidenceTrigger = useRef<HTMLElement | null>(null)
  const [awayFromLatest, setAwayFromLatest] = useState(false)
  const [quickIntent, setQuickIntent] = useState<'student' | 'group'>()
  const [choice, setChoice] = useState<ModelChoice>()
  const [legacyProvider, setLegacyProvider] = useState<string>()
  const [savingChoice, setSavingChoice] = useState(false)
  const [choiceError, setChoiceError] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [sessions, setSessions] = useState<SessionSummary[]>()
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyMobile, setHistoryMobile] = useState(() => window.matchMedia('(max-width: 800px)').matches)
  useEffect(() => {
    const media = window.matchMedia('(max-width: 800px)')
    const update = () => setHistoryMobile(media.matches)
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])
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
    followLatest.current = true
    setAwayFromLatest(false)
    void api(`/chat/sessions/${id}`).then(session => {
      if (!active) return
      setSessionId(session.id)
      setSessionTitle(session.title || '')
      write(SESSION_KEY, session.id, localStorage)
      setChoice(session.selection)
      setLegacyProvider(session.provider)
      setChoiceError('')
      setContext({ ...{ student: '', group: '', node: '' }, ...(session.context || {}) })
      setTurns((session.turns || []).map((turn: any) => ({
        question: turn.question,
        context: turn.context,
        result: { answer: turn.answer, parts: turn.parts || [], cards: turn.cards || [], model_call: turn.model_call, model: turn.model, elapsed_ms: turn.elapsed_ms,
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
    setHistoryLoading(true)
    try { setSessions(await api<SessionSummary[]>('/chat/sessions')) }
    catch (error) { setHistoryError((error as Error).message) }
    finally { setHistoryLoading(false) }
  }

  useEffect(() => {
    if (!props.active || !turns.length) return
    function track() {
      const near = document.documentElement.scrollHeight - window.scrollY - window.innerHeight < 160
      followLatest.current = near
      setAwayFromLatest(!near)
      const items = thread.current?.querySelectorAll('.chat-turn') || []
      let current = 0
      items.forEach((item, index) => { if (item.getBoundingClientRect().top <= 130) current = index })
      setActiveTurn(current)
    }
    window.addEventListener('scroll', track, { passive: true })
    return () => window.removeEventListener('scroll', track)
  }, [props.active, turns.length > 0])

  function jumpToLatest() {
    followLatest.current = true
    setAwayFromLatest(false)
    window.scrollTo({ top: document.documentElement.scrollHeight })
  }

  useEffect(() => {
    if (props.active && turns.length && followLatest.current) jumpToLatest()
  }, [turns, busy, props.active])

  function handleComposerEnter(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.shiftKey || event.nativeEvent.isComposing || event.keyCode === 229) return
    event.preventDefault()
    void send(draft)
  }

  const pollIds = useMemo(() => {
    const runIds = new Set<string>()
    for (const turn of turns) for (const card of turn.result?.cards || []) {
      if (['cleaning', 'material_extract'].includes(card.kind) && card.run_id && !card.pollComplete) runIds.add(card.run_id)
    }
    return [...runIds]
  }, [turns])

  useEffect(() => {
    if (!pollIds.length) return
    let active = true
    const poll = () => {
      for (const runId of pollIds) {
        void api(`/agent/runs/${runId}`).then(run => {
          if (!active) return
          setTurns(current => {
            let changed = false
            const next = current.map(turn => {
              const cards = turn.result?.cards
              if (!cards?.some(card => card.run_id === runId && !card.pollComplete &&
                  (card.state !== run.state || card.stage !== run.stage || card.error !== (run.error || '')))) return turn
              changed = true
              return { ...turn, result: { ...turn.result!, cards: cards.map(card => card.run_id === runId ? {
                ...card, state: run.state, stage: run.stage || '', error: run.error || '',
                extraction: run.result?.extraction, report: run.report || card.report,
                pollComplete: run.state === 'done' && (run.stage === 'published' || card.kind === 'material_extract'),
                snapshot_id: run.stage === 'published' ? null : card.snapshot_id,
              } : card) } }
            })
            return changed ? next : current
          })
          if (run.state === 'done' && run.stage === 'confirm') void refreshProposalFor(runId)
        }).catch(() => { /* keep the last confirmed state */ })
      }
    }
    poll()
    const timer = setInterval(poll, 2500)
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

  async function send(question: string, override?: Partial<Context>, history = turns, refs = attachments) {
    question = question.trim()
    if (!question || busy || restoring || savingChoice || stale) return
    const outgoing: Context = { ...context, ...override }
    followLatest.current = true
    setBusy(true)
    setDraft('')
    const shown: Turn = { question, context: outgoing, started:Date.now(), attachments:refs }
    setAttachments([])
    setTurns([...history, shown])
    try {
      const result = await streamChat({
          question,
          attachments: refs,
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
        }, event => {
          setTurns(current => current.map((turn, index) => index !== history.length ? turn : {
            ...turn,
            process: updateProcess(turn.process || [], event),
          }))
      })
      if (result.session_id) { setSessionId(result.session_id); write(SESSION_KEY, result.session_id, localStorage) }
      if (result.title) setSessionTitle(result.title)
      if (result.selection) setChoice(result.selection)
      if (result.context) setContext({ ...result.context, anchor_event: '' })
      setTurns(current => current.map((turn, index) => index === history.length ? { ...turn, result } : turn))
      if (!props.snapshot && result.cards?.some(card => card.kind === 'import'))
        write('aitolearn.import.question', question, sessionStorage)
    } catch (error) {
      setTurns(current => current.map((turn, index) => index === history.length
        ? { ...turn, elapsed: Date.now() - shown.started!, error: (error as Error).message } : turn))
    } finally { setBusy(false) }
  }

  async function submitAttachments(files: File[], batch: string, target: string) {
    const form = new FormData()
    files.forEach(file => form.append('files', file))
    form.set('batch_id', batch)
    if (sessionId) form.set('session_id', sessionId)
    if (target) form.set('continue_target', target)
    const result = await api<Result>('/chat/attachments/submit', {method:'POST',body:form})
    if (result.session_id) {
      setSessionId(result.session_id); write(SESSION_KEY, result.session_id, localStorage)
    }
    if (result.title) setSessionTitle(result.title)
    followLatest.current = true
    setTurns(old => [...old, {question:result.question || '上传附件',result}])
    props.onTaskStarted()
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    void send(draft)
  }

  function newChat() {
    if (savingChoice) return
    setChoice(undefined)
    setAttachments([])
    setLegacyProvider(undefined)
    setChoiceError('')
    setHistoryOpen(false)
    setTurns([])
    setContext({ student: '', group: '', node: '' })
    setSessionId('')
    setSessionTitle('')
    write(SESSION_KEY, '', localStorage)
    setDraft('')
    setPickerOpen(false)
    setQuickIntent(undefined)
    setAwayFromLatest(false)
  }

  function fillQuick(kind: 'student' | 'group', value: string) {
    setDraft(kind === 'student' ? `看看学生 ${value} 的学习表现` : `看看班级 ${value} 哪些指标需要优先复习`)
    requestAnimationFrame(() => composerInput.current?.focus({ preventScroll: true, cursor: 'end' }))
  }

  function startQuick(kind: 'student' | 'group') {
    if (context[kind]) { fillQuick(kind, context[kind]); return }
    setQuickIntent(kind); setPickerKind(kind); setPickerOpen(true)
  }

  function setTarget(kind: 'student' | 'group', value: string) {
    setContext({ student: kind === 'student' ? value : '', group: kind === 'group' ? value : '',
      node: context.node, snapshot_id: props.snapshot })
    if (quickIntent) fillQuick(kind, value)
    setQuickIntent(undefined)
    setPickerOpen(false)
  }

  async function readEvidence(student: string, node: string, indicator: string, snapshot?: string, group = '', item = '', includeUnscored = false) {
    const trigger = document.activeElement
    try {
      const rows = await api<Answer[]>(`/profiles/evidence?${new URLSearchParams({ student, group, node, indicator, item, snapshot_id: snapshot || props.snapshot || '', include_unscored: String(includeUnscored) })}`)
      if (rows[0]) {
        evidenceTrigger.current = trigger instanceof HTMLElement ? trigger : null
        setSourceRows(rows); setSource(rows[0])
      }
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
      if (response.result?.recommendation_run_id) props.openRun(response.result.recommendation_run_id)
      if (response.result?.continuation_error) window.alert('快照已启用，未自动生成建议：' + response.result.continuation_error)
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
    if (card.kind === 'clarification') { void send(String(option.label)); return }
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

  const history = <ChatHistory api={api} sessions={sessions} active={sessionId}
    loading={historyLoading} error={historyError} retry={() => void showHistory(true)}
    saved={updated => {
      setSessions(old => old?.map(item => item.id === updated.id ? updated : item))
      if (updated.id === sessionId) setSessionTitle(updated.title)
    }}
    select={id => {
      setHistoryOpen(false)
      if (id === sessionId) return
      setRestoring(true); setDraft(''); setAttachments([]); restoreSession(id)
    }} />
  const historyDisabled = busy || restoring || savingChoice
  const title = sessionTitle || Array.from(turns[0]?.question || '').slice(0, 72).join('') || '学情对话'

  return <section aria-label="学情对话" className={turns.length ? "learning-chat" : "learning-chat chat-is-empty"}>
    <header className="chat-toolbar">
      <h1 className="chat-title" title={title}>{title}</h1>
      <Space size={4} className="chat-toolbar-actions">
        {historyMobile ? <>
          <Tooltip title="最近对话"><Button type="text" icon={<HistoryOutlined />} disabled={historyDisabled} aria-label="最近对话" aria-haspopup="dialog" onClick={() => void showHistory(true)} /></Tooltip>
          <Drawer open={historyOpen} title="最近对话" placement="bottom" size="min(72dvh, 560px)" onClose={() => setHistoryOpen(false)}>
            {historyOpen && history}
          </Drawer>
        </> : <Popover trigger="click" open={historyOpen} onOpenChange={showHistory} content={history} destroyOnHidden>
          <Button type="text" icon={<HistoryOutlined />} title="最近对话" aria-label="最近对话" disabled={historyDisabled} aria-expanded={historyOpen} />
        </Popover>}
        <Tooltip title="新对话"><Button type="text" icon={<PlusOutlined />} aria-label="新对话" disabled={busy || restoring || (!turns.length && !draft && !contextLabel && !context.node)} onClick={newChat} /></Tooltip>
      </Space>
    </header>
    {restoring && <div className="chat-restore"><ActiveStatus label="正在恢复对话" /></div>}
    <div ref={thread} className="chat-thread" aria-label="学情对话记录" tabIndex={0}>
    {!restoring && !turns.length && <div className="chat-welcome">
      <h2>今天想了解什么？</h2>
      <p>查看学生和班级表现，沿着原始作答与教材依据继续追问。</p>
    </div>}
    {!restoring && !turns.length && (!props.snapshot || !!props.pendingRun) && <section className="chat-start" aria-label="学情数据状态">
      <div className="chat-status-icon" aria-hidden="true"><FileSearchOutlined /></div>
      <div className="chat-start-copy">
        <h2>{props.pendingRun ? '本批数据待教师确认' : '还没有可用的学情数据'}</h2>
        <p>{props.pendingRun ? (props.snapshot ? '新批次尚未启用，对话仍基于当前生效的旧快照。' : '请先核对清洗与复核结果，确认后即可使用这批数据。')
          : '导入表格后，核对清洗结果并启用快照，即可开始分析。'}</p>
        {!props.snapshot && !props.pendingRun && <ol className="chat-start-steps"><li>导入表格</li><li>核对结果</li><li>开始分析</li></ol>}
      </div>
      {props.pendingRun ? <Button type="primary" onClick={() => props.openRun(props.pendingRun!)}>查看清洗与复核</Button>
        : <Button type="primary" onClick={props.openImport}>导入学情数据</Button>}
    </section>}
      {turns.map((turn, index) => {
        const scope = turn.result?.context ?? turn.context
        return <article key={index} className="chat-turn">
        <p className="chat-question">{turn.question}</p>
        <div className="chat-reply">
        <div className="chat-reply-heading"><span className="chat-reply-mark" aria-hidden="true"><BarChartOutlined /></span><strong>学情助手</strong>
          {scope?.student && <span>学生 {scope.student}</span>}
          {scope?.group && <span>班级 {scope.group}</span>}
          {scope?.node && <span>测验 {scope.node}</span>}
        </div>
        <TurnProcess turn={turn} />
        {turn.result?.cards?.some(card => card.kind === 'profile') &&
          turn.result.cards.filter(card => card.kind === 'profile').map((card, cardIndex) =>
            <ResultCard key={card.id || cardIndex} card={card} api={api} snapshot={turn.result?.snapshot_id || props.snapshot}
              readEvidence={readEvidence} confirmProposal={confirmProposal} chooseCandidate={chooseCandidate}
              openProfile={props.openProfile} openRun={props.openRun} openImport={props.openImport}
              openDraft={props.openDraft} openMaterial={props.openMaterial} openPage={props.openPage} />)}
        {turn.error ? <div className="chat-reply-error"><p role="alert" className="chat-error">{turn.error}{turn.elapsed != null && ' · ' + (turn.elapsed / 1000).toFixed(1) + ' 秒'}</p>
          {index === turns.length - 1 && <>
            <Space wrap>
              <Button disabled={busy} onClick={() => void send(turn.question, turn.context, turns.slice(0, index), turn.attachments)}>重试</Button>
              {turn.error.includes('检查模型设置') && <Button onClick={props.openAccess}>打开模型设置</Button>}
            </Space>
          </>}
        </div> : turn.result ? <div>
          {turn.result.answer && <AnswerText text={turn.result.answer} />}
          {!!turn.result.parts?.length && <section className="chat-citations" aria-label="引用依据">
            <h3>引用依据 <span>{turn.result.parts.length}</span></h3>
          {(turn.result.parts || []).map(({ quote, source }, id) =>
            <blockquote key={id}>
              <p className="preserve">{quote}</p>
              <Button type="link" onClick={event => {
                if (source.kind === 'material') props.openCitation(source.citation, event.currentTarget)
                else { evidenceTrigger.current = event.currentTarget; setSourceRows([]); setSource(source.answer) }
              }}>
                {source.title} · {source.locator}
              </Button>
            </blockquote>)}
          </section>}
          {(turn.result.cards || []).filter(card => card.kind !== 'profile').map((card, cardIndex) => <ResultCard key={card.id || cardIndex} card={card}
            api={api} snapshot={turn.result?.snapshot_id || props.snapshot}
            readEvidence={readEvidence} confirmProposal={confirmProposal} chooseCandidate={chooseCandidate}
            openProfile={props.openProfile} openRun={props.openRun} openImport={props.openImport}
            openDraft={props.openDraft} openMaterial={props.openMaterial} openPage={props.openPage} />)}
          {(turn.result.model_call || turn.result.model || turn.result.elapsed_ms != null) && <footer className="chat-reply-meta">
            {turn.result.model_call ? <span>{[turn.result.model_call.model_id, turn.result.model_call.connection_name, effortLabel[turn.result.model_call.effort]].filter(Boolean).join(' · ')}</span>
              : turn.result.model && <span>{turn.result.model}</span>}
            {turn.result.elapsed_ms != null && <span>耗时 {(turn.result.elapsed_ms / 1000).toFixed(1)} 秒</span>}
          </footer>}
        </div> : <ActiveStatus label="正在回答" started={turn.started} />}
        </div>
      </article>})}
      <div ref={bottom} />
    {!turns.length && !restoring && !!props.snapshot && <div className="chat-quick" aria-label="常用任务">
      <Button icon={<UserOutlined aria-hidden="true" />} onClick={() => startQuick('student')}>查看学生表现</Button>
      <Button icon={<BarChartOutlined aria-hidden="true" />} onClick={() => startQuick('group')}>查看班级薄弱指标</Button>
      <Button icon={<ProfileOutlined aria-hidden="true" />} onClick={() => props.openAdvice({student: context.student, group: context.group, node: context.node})}>生成学习建议</Button>
    </div>}
    </div>
    {!restoring && !!turns.length && <nav className="chat-turn-markers" aria-label="按提问定位">
      {turns.map((turn, index) => <button key={index} type="button" aria-label={`第 ${index + 1} 条提问：${turn.question.slice(0, 40)}`}
        title={turn.question} aria-current={index === activeTurn ? 'true' : undefined}
        onClick={() => { followLatest.current = false; thread.current?.querySelectorAll('.chat-turn')[index]?.scrollIntoView({block:'start'}) }} />)}
    </nav>}
    <div ref={composerDock} className="chat-composer-dock">
      {!!turns.length && awayFromLatest && <div className="chat-jump"><Button shape="circle" icon={<ArrowDownOutlined />} aria-label="回到最新消息" title="回到最新消息" onClick={jumpToLatest} /></div>}
      <form className="chat-composer" onSubmit={submit}>
      {(stale || choiceError) && <p role="status">{choiceError || '本会话原模型或思考档位已不可用，请为下一条消息重选。'}</p>}
      {!!attachments.length && <Space wrap>{attachments.map(item => <Tag key={item.name} closable
        onClose={() => setAttachments(old=>old.filter(ref=>ref !== item))}>{item.name} · 本轮参考</Tag>)}</Space>}
      <Input.TextArea ref={composerInput} aria-label="输入学情问题" placeholder={contextLabel ? `询问 ${contextLabel}，或说“帮我生成建议”` : props.snapshot ? '例如：看看这次测验哪些指标需要复习' : '可先了解如何导入，或添加仅本轮参考的文件'}
        variant="borderless" maxLength={1200} autoSize={{ minRows: 2, maxRows: 5 }} value={draft} onChange={event => setDraft(event.target.value)}
        onPressEnter={handleComposerEnter} />
      <div className="chat-composer-actions">
      <Space wrap className="chat-context">
        <Popover trigger="click" open={pickerOpen} onOpenChange={open => { setPickerOpen(open); if (!open) setQuickIntent(undefined) }} content={<div className="context-picker">
          <Button type="primary" onClick={()=>{setPickerOpen(false);setAttachmentOpen(true)}}>上传文件</Button>
          <strong>添加学情上下文</strong>
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
          <Button type="text" aria-label="添加上下文" title="添加上下文" icon={<PlusOutlined />}>添加</Button>
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
    {attachmentOpen && <ChatAttachments api={api} context={context} close={()=>setAttachmentOpen(false)}
      autoSubmit={submitAttachments} useReferences={setAttachments} openRun={props.openRun} openImport={props.openImport} openMaterials={()=>props.openPage('materials')} />}
    <Drawer open={!!source} title="学生原始作答" size="min(850px, 100vw)" onClose={() => { setSource(undefined); setSourceRows([]) }}
      afterOpenChange={open => { if (!open) { evidenceTrigger.current?.focus({ preventScroll: true }); evidenceTrigger.current = null } }}>
      {sourceRows.length > 1 && <Select aria-label="选择作答事件" value={source?.作答事件编号}
        options={sourceRows.map(row => ({ value: row.作答事件编号, label: [row.节点编号, row.材料编号, row.作答事件编号].join(' · ') }))}
        onChange={event => setSource(sourceRows.find(row => row.作答事件编号 === event))} />}
      {source && <AnswerEvidence key={source.作答事件编号} answer={source} />}
    </Drawer>
  </section>
}

function ResultCard({ card, api, snapshot, readEvidence, confirmProposal, chooseCandidate, openProfile, openRun, openImport, openDraft, openMaterial, openPage }: {
  card: CardData; api: Api; snapshot?: string
  readEvidence: (student: string, node: string, indicator: string, snapshot?: string, group?: string, item?: string, includeUnscored?: boolean) => void
  confirmProposal: (card: CardData) => void
  chooseCandidate: (option: Record<string, any>, card: CardData) => void
  openProfile: Props['openProfile']; openRun: Props['openRun']; openImport: Props['openImport']
  openMaterial: Props['openMaterial']
  openPage: Props['openPage']
  openDraft: Props['openDraft']
}) {
  if (card.kind === 'attachment_skipped') return <Card size="small" className="chat-card" title="未提交的文件">
    <ul>{card.files.map((file: {name:string;reason:string}) => <li key={file.name}>{file.name}：{file.reason}</li>)}</ul>
    <Button onClick={openImport}>前往数据导入页</Button>
  </Card>
  if (card.kind === 'material_extract') return <Card size="small" className="chat-card" title={card.name}>
    {card.state === 'failed' ? <Alert type="error" title={card.error || '解析失败'} />
      : card.state === 'done' ? <p>解析完成 · 等待教师校对{card.extraction && (' · 文字层 ' + card.extraction.text_pages + ' 页 · OCR ' + card.extraction.ocr_units + ' 处')}</p>
      : card.state === 'stopped' ? <p>已停止</p> : <ActiveStatus label={card.state === 'running' ? '正在解析' : '等待解析'} />}
    <Space wrap><Button onClick={() => openRun(card.run_id)}>查看任务</Button>
      {card.state === 'done' && <Button type="primary" onClick={() => openMaterial(card.material_id)}>校对资料</Button>}</Space>
  </Card>
  if (card.kind === 'material_blocks') return <Card size="small" className="chat-card" title={'待校对 · ' + card.name}>
    <p>从第 {card.offset + 1} 条开始，共 {card.count} 条。</p>
    {card.blocks.map((block: {id:string; text:string; locator:string}) => <div key={block.id}>
      <strong>{block.locator}</strong><p className="preserve">{block.text}</p></div>)}
    <Button onClick={() => openMaterial(card.material_id)}>对照原图校对</Button>
  </Card>
  if (card.kind === 'navigation') return <Card size="small" className="chat-card" title={card.title}>
    <Button onClick={() => openPage(card.page)}>打开{card.title}</Button>
  </Card>
  if (card.kind === 'formal_export') return <Card size="small" className="chat-card" title="导出正式成果">
    <p>当前快照包含 {card.count} 条已确认建议；下载时会再次核对快照。</p>
    <Button type="primary" href={'/api/export?format=json&formal=true&expected_snapshot=' + encodeURIComponent(card.snapshot_id)}>下载 JSON</Button>
  </Card>
  if (card.kind === 'drafts') return <Card size="small" className="chat-card" title="学习建议">
    {card.items.map((item: {id: string; revision: number; message: string; review_state?: string; approved: boolean}) =>
      <div key={item.id}><p>版本 {item.revision} · {item.approved ? '已确认' : item.review_state === 'pass' ? '终审通过，待确认' : '尚未通过终审'}</p>
        <p className="preserve">{item.message}</p><Button onClick={() => openDraft(item.id)}>查看完整建议</Button></div>)}
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
  if (card.kind === 'clarification') return <Card size="small" className="chat-card" title="需要补充信息">
    <p>{card.prompt}</p><Space wrap>{(card.options || []).map((option: string) =>
      <Button key={option} onClick={() => chooseCandidate({label: option}, card)}>{option}</Button>)}</Space>
    <p>也可以在输入框直接回复。</p>
  </Card>
  if (card.kind === 'progress') return <Card size="small" className="chat-card" title="按测验查看原始作答">
    <Space wrap>{card.nodes.map((node: string) => <Button key={node}
      onClick={() => readEvidence(card.student, node, card.indicator, card.snapshot_id, '', '', true)}>{node} · 原始作答</Button>)}</Space>
  </Card>
  if (card.kind === 'profile') return <Card size="small" className="chat-card"
    title={(card.target?.kind === 'student' ? '学生 ' : '班级 ') + card.target?.id + (card.node ? ' · ' + card.node : '')}
    extra={<Button onClick={() => openProfile(card.target?.kind, card.target?.id, card.node || '', card.weakest?.id || '', card.snapshot_id || snapshot)}>打开画像</Button>}>
    <div className="chat-indicators">{(card.indicators || []).map((axis: Axis) => {
      const student = card.target?.kind === 'student'
      const full = axis.possible != null && axis.possible > 0 && axis.earned === axis.possible
      const rate = axis.score == null || !Number.isFinite(axis.score) ? null : Math.max(0, Math.min(100, axis.score))
      const color = rate == null ? undefined : indicatorColor(rate)
      return <div key={axis.id} className="chat-indicator">
      <div className="chat-indicator-copy"><span className="chat-indicator-dot" aria-hidden="true" style={color ? {backgroundColor: color} : undefined} />
      <div><strong>{axis.label}</strong><small><span>{rate == null ? axis.status : axis.score + '%'}{student && rate != null && (full ? ' · 当前满分' : rate < 60 ? ' · 优先关注' : '')}</span> · 覆盖 {axis.coverage}/{axis.population} 人 · {axis.n} 条作答</small></div></div>
      {card.target?.kind === 'student'
        ? <Button size="small" onClick={() => readEvidence(card.target.id, card.node || '', axis.id, card.snapshot_id || snapshot, '', card.item)}>查看作答</Button>
        : <Button size="small" onClick={() => readEvidence('', card.node || '', axis.id, card.snapshot_id || snapshot, card.target.id, card.item)}>查看原始作答</Button>}
    </div>})}</div>
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
        : card.action === 'stop_job' ? (confirmed ? '停止请求已提交。正在运行的任务会在可停止的阶段结束。' : '确认后停止这个任务。')
        : card.action === 'approve_draft' ? (confirmed ? '学习建议已确认。' : `确认第 ${card.payload?.revision} 版通过终审的学习建议。`)
        : card.action === 'assign_follow_up' ? (confirmed ? '练后跟踪已登记。' : '请核对学生、基线测验和练习方向，确认后登记。')
        : confirmed ? (card.action === 'confirm_import' ? '这批数据已确认，后续诊断和建议读取同一快照。'
        : '建议任务已开始，进度与终审结果会保留在任务详情。')
        : (card.action === 'confirm_import' ? '复核已完成。确认后建立数据快照，后续诊断和建议都读取该快照。'
        : '将使用当前快照生成学情解读、建议草稿并执行独立终审。')}</p>
      {(card.payload?.student || card.payload?.group) && <p><Tag>{card.payload.student || card.payload.group}</Tag><Tag>{card.payload.node || '全部测验'}</Tag></p>}
      {card.action === 'assign_follow_up' && <p>基线 {card.payload?.baseline_node} · 方向 {(card.payload?.directions || []).join('、')}</p>}
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
        {card.action === 'approve_draft' && <Button onClick={() => openDraft(card.payload.draft_id)}>查看完整建议</Button>}
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
