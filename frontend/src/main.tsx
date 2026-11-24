import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { App, Alert, Button, Card, ConfigProvider, Drawer, Empty, Input, Layout, Select, Skeleton, Space, Steps, Table, Tag, Tooltip, Typography, theme } from 'antd'
import { BookOutlined } from '@ant-design/icons'
import { NavIcon } from './NavIcon'
import zhCN from 'antd/locale/zh_CN'
import AgentWorkbench, { ReviewPanel, runNames } from './AgentWorkbench'
import { CaseReplay, type CaseData } from './CaseReplay'
import { CleaningCounts, issueFilters } from './CleaningCounts'
import { Diagnostics, type DiagnosticSelection, type DiagnosticAxis, type Profile } from './Diagnostics'
import { AnswerEvidence } from './AnswerEvidence'
import { applyWorkbenchPalette, chartComparisonColor, workbenchTheme } from './workbenchTheme'
import { LearningChat } from './LearningChat'
import { ModelAccessPage, type ModelAccess } from './ModelAccess'
import * as echarts from 'echarts/core'
import { RadarChart } from 'echarts/charts'
import { RadarComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import './style.css'

echarts.use([RadarChart, RadarComponent, TooltipComponent, CanvasRenderer])
const { Title, Text, Paragraph } = Typography
type Row = Record<string, any>
type Axis = { id: string; label: string; score: number | null; n: number; coverage: number; population: number; status: string; earned: number; possible: number; distribution: number[] }
const noAxes: Axis[] = []
const pages = [
  ['chat', '学情对话'], ['import', '数据导入'], ['materials', '课程与资料'], ['overview', '班级诊断'],
  ['student', '画像'], ['drafts', '学习建议'], ['settings', '设置'],
] as const
const pageKeys = new Set<string>(pages.map(([key]) => key))
type ProfileTarget = { kind: 'student' | 'group'; id: string }
const mobileSidebarQuery = '(max-width: 800px)'
const sidebarPreference = 'aitolearn.sidebar.collapsed.v2'
function pageFromHash() {
  if (location.hash.startsWith('#agent')) return 'run'
  if (location.hash === '#models') return 'settings'
  const key = location.hash.replace(/^#/, '').split('/')[0]
  return pageKeys.has(key) ? key : 'chat'
}
async function api<T = any>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch('/api' + path, options)
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(typeof body.detail === 'string' ? body.detail
      : body.detail ? JSON.stringify(body.detail) : '操作失败，请检查输入或服务状态')
  }
  return response.json()
}
const json = (method: string, data: unknown) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) })
const post = { method: 'POST' }
const param = encodeURIComponent
function query(values: Record<string, string>) { return '?' + new URLSearchParams(values).toString() }

function ProfileRadar({ axes, compare, label, onSelect }: {
  axes: Axis[]; compare: Axis[]; label: string; onSelect: (axis: Axis) => void
}) {
  const { token } = theme.useToken()
  const ref = useRef<HTMLDivElement>(null)
  const instance = useRef<ReturnType<typeof echarts.init>>(null)
  const comparison = new Map(compare.map(axis => [axis.id, axis.score]))
  const previous = axes.map(axis => comparison.get(axis.id))
  const hasComparison = previous.length > 0 && previous.every(value => value != null)
  const latest = useRef({ axes, onSelect })
  useLayoutEffect(() => { latest.current = { axes, onSelect } })
  useEffect(() => {
    if (!ref.current) return
    const chart = echarts.init(ref.current)
    instance.current = chart
    chart.on('click', event => {
      const axis = latest.current.axes.find(item => item.label === event.name?.replace(/\n/g, ''))
      if (axis) latest.current.onSelect(axis)
    })
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(ref.current)
    return () => { observer.disconnect(); chart.dispose(); instance.current = null }
  }, [])
  useEffect(() => {
    const complete = axes.every(axis => axis.score != null)
    instance.current?.setOption({
      baseOption: {
        animation: !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
        tooltip: { textStyle: { fontSize: token.fontSizeSM } },
        textStyle: { fontFamily: token.fontFamily, fontSize: token.fontSizeSM },
        radar: { indicator: axes.map(axis => ({ name: axis.label, max: 100 })), radius: '53%', axisNameGap: 8,
          triggerEvent: true, axisName: { color: token.colorTextSecondary, fontSize: token.fontSizeSM, width: 94, overflow: 'break' } },
        series: [{ type: 'radar', lineStyle: { width: complete ? 2 : 0 }, symbolSize: 8,
          data: [{ name: label, value: axes.map(axis => axis.score ?? NaN), itemStyle: { color: token.colorPrimary },
            areaStyle: { opacity: complete ? 0.18 : 0 } },
            ...(hasComparison ? [{ name: '班级参照', value: previous,
              itemStyle: { color: chartComparisonColor }, lineStyle: { width: 1, type: 'dashed' } }] : [])] }],
      },
      media: [{ query: { maxWidth: 520 }, option: {
        radar: { axisName: {
          lineHeight: 19,
          formatter: (label: string) => label.match(/.{1,6}/gu)?.join('\n') || label,
        } },
      } }, { query: { maxWidth: 365 }, option: {
        radar: { radius: '41%', axisName: {
          lineHeight: 19,
          formatter: (label: string) => label.match(/.{1,4}/gu)?.join('\n') || label,
        } },
      } }],
    }, { notMerge: true })
  }, [axes, compare, label, token])
  return <section className="student-radar" aria-label="整体轮廓">
    <Title level={5}>整体轮廓</Title>
    <Space wrap className="radar-legend"><span>{label}</span>{hasComparison && <span className="radar-class">班级参照</span>}</Space>
    <div className="chart radar" ref={ref} role="img" aria-label={label + '学习表现雷达图'} />
  </section>
}

function Comparison({ axes, compare, select }: { axes: Axis[]; compare: Axis[]; select: (axis: Axis) => void }) {
  const byId = new Map(compare.map(axis => [axis.id, axis]))
  if (!axes.length) return null
  return <section className="student-comparison" aria-label="学生与班级对照">
    <Title level={5}>学生与班级对照</Title>
    <Text type="secondary">实心圆为学生，空心菱形为班级 · 0–100%</Text>
    {axes.map(axis => {
      const other = byId.get(axis.id)
      const delta = axis.score == null || other?.score == null ? null : Number((axis.score - other.score).toFixed(1))
      return <div key={axis.id} id={'indicator-' + axis.id} className="comparison-row">
        <Button type="link" className="comparison-label" onClick={() => select(axis)}>{axis.label}</Button>
        <div className="dot-track" aria-hidden="true">
          {axis.score != null && <i style={{ left: axis.score + '%' }} />}
          {other?.score != null && <i className="class-dot" style={{ left: other.score + '%' }} />}
        </div>
        <Text>学生 {axis.score == null ? axis.status : axis.score + '%'}（{axis.n} 条） · 班级 {other?.score == null ? other?.status || '无数据' : other.score + '%'}（{other?.n ?? '—'} 条）{delta != null && ' · 差 ' + (delta > 0 ? '+' : '') + delta + ' 个百分点'}</Text>
      </div>
    })}
  </section>
}

function SourcePage({ citation }: { citation: Row }) {
  const [size, setSize] = useState({ w: 0, h: 0 })
  const [original, setOriginal] = useState(false)
  const bbox: number[] | undefined = citation.bbox
  const locator = original ? citation.locator.replace(' · 矫正图', '') : citation.locator
  if (!bbox && citation.title === '赛题题库与学习卡') return null
  return <>
    {citation.locator.endsWith(' · 矫正图') && <Button onClick={() => { setOriginal(!original); setSize({w:0,h:0}) }}>{original ? '查看矫正图' : '查看原图'}</Button>}
    <div className="source-preview">
      <img key={locator} className="source-page" alt="资料页面" src={'/api/materials/' + citation.material + '/preview' + query({locator})}
        onLoad={e => setSize({w:e.currentTarget.naturalWidth,h:e.currentTarget.naturalHeight})}
        onError={e => {e.currentTarget.hidden = true; setSize({w:0,h:0})}} />
      {!original && bbox?.length === 4 && size.w > 0 && size.h > 0 && <div className="source-highlight" style={{
        left: 100*bbox[0]/size.w+'%', top:100*bbox[1]/size.h+'%', width:100*(bbox[2]-bbox[0])/size.w+'%', height:100*(bbox[3]-bbox[1])/size.h+'%',
      }} />}
    </div>
  </>
}
function Workbench() {
  const { message } = App.useApp()
  const [page, setPage] = useState<string>(pageFromHash)
  const [mobile, setMobile] = useState(() => window.matchMedia(mobileSidebarQuery).matches)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [rail, setRail] = useState(() => {
    try { return localStorage.getItem(sidebarPreference) === 'true' }
    catch { return false }
  })
  const navToggle = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    const media = window.matchMedia(mobileSidebarQuery)
    const resize = () => { setMobile(media.matches); setMobileOpen(false) }
    media.addEventListener('change', resize)
    return () => media.removeEventListener('change', resize)
  }, [])
  const [runId, setRunId] = useState(() => /^#agent\/([a-f0-9]+)$/.exec(location.hash)?.[1] || '')
  const [runDetail, setRunDetail] = useState<Row>()
  const [cleanDetail, setCleanDetail] = useState<Row>()
  const [runError, setRunError] = useState('')
  const [caseAvailable, setCaseAvailable] = useState(false)
  const [onboardingSignal, setOnboardingSignal] = useState(0)
  const [replay, setReplay] = useState<CaseData>()
  const [data, setData] = useState<Row>({})
  const [target, setTarget] = useState<ProfileTarget>()
  const student = target?.kind === 'student' ? target.id : ''
  const profileGroup = target?.kind === 'group' ? target.id : ''
  const [node, setNode] = useState('')
  const [topic, setTopic] = useState('')
  const [jobs, setJobs] = useState<Row[]>([])
  const [materials, setMaterials] = useState<Row[]>([])
  const [curriculum, setCurriculum] = useState<Row>({})
  const [drafts, setDrafts] = useState<Row[]>([])
  const [deliveries, setDeliveries] = useState<Row[]>([])
  const [health, setHealth] = useState<Row>({})
  const [modelAccess, setModelAccess] = useState<ModelAccess>()
  const [report, setReport] = useState<Row>()
  const [files, setFiles] = useState<File[]>([])
  const importInput = useRef<HTMLInputElement>(null)
  const [correction, setCorrection] = useState<Row>()
  const [corrected, setCorrected] = useState('{}')
  const [mapping, setMapping] = useState('{}')
  const [selected, setSelected] = useState<Row>()
  const [answerId, setAnswerId] = useState('')
  const [profileSnapshot, setProfileSnapshot] = useState('')
  const [focusIndicator, setFocusIndicator] = useState('')
  const [citation, setCitation] = useState<Row>()
  const [doc, setDoc] = useState<Row>()
  const [changes, setChanges] = useState<Record<string, string>>({})
  const [lookup, setLookup] = useState('')
  const [hits, setHits] = useState<Row[]>([])
  const [draft, setDraft] = useState<Row>()
  const [binding, setBinding] = useState({ label: '', webhook: '', secret: '' })
  const [preview, setPreview] = useState<Row>()
  const practicePreview = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (preview) practicePreview.current?.scrollIntoView({ block: 'nearest' })
  }, [preview])
  const [busy, setBusy] = useState(false)
  async function refresh() {
    const [r, library, map, list, delivery, status] = await Promise.all([
      api('/imports/report'), api('/materials'), api('/curriculum'), api('/recommendations'), api('/deliveries'), api('/health'),
    ])
    setReport(r); setMaterials(library); setCurriculum(map); setDrafts(list); setDeliveries(delivery); setHealth(status)
  }
  const goTo = (next: string) => {
    if (next === 'run') return
    const fromModels = location.hash === '#models'
    if (location.hash) history.replaceState(null, '', location.pathname + location.search + (next === 'chat' ? '' : '#' + next))
    setPage(next)
    if (next === 'settings' && fromModels) {
      requestAnimationFrame(() => document.getElementById('model-access')?.scrollIntoView({ block: 'start' }))
    }
  }
  const openRun = (id: string) => {
    if (!id) { goTo('settings'); return }
    location.hash = 'agent/' + id
    setRunId(id)
    setPage('run')
  }
  async function perform(action: () => Promise<unknown>) {
    setBusy(true)
    try { await action() } catch (e) { message.error((e as Error).message) } finally { setBusy(false) }
  }
  useEffect(() => {
    if (location.hash === '#models') requestAnimationFrame(() => document.getElementById('model-access')?.scrollIntoView({ block: 'start' }))
  }, [page])
  useEffect(() => {
    if (page === 'chat' || page === 'settings' || page === 'run') {
      void api<ModelAccess>('/models/settings').then(setModelAccess).catch(error => message.error(error.message))
    }
  }, [page])
  useEffect(() => {
    void refresh().catch(e => message.error(e.message))
    let signature = ''
    const poll = async () => {
      try {
        const list: Row[] = await api('/jobs')
        setJobs(list)
        const next = JSON.stringify(list.map(j => [j.id, j.state, j.stage]))
        if (next !== signature) { signature = next; await refresh() }
      } catch { /* keep the current screen while the backend restarts */ }
    }
    void poll()
    const timer = setInterval(poll, 2500)
    return () => clearInterval(timer)
  }, [])
  useEffect(() => {
    const onHashChange = () => {
      setRunId(/^#agent\/([a-f0-9]+)$/.exec(location.hash)?.[1] || '')
      setPage(pageFromHash())
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])
  useEffect(() => {
    if (!runId || page !== 'run') return
    let active = true
    const load = async () => {
      try {
        const detail = await api('/agent/runs/' + runId)
        if (active) { setRunDetail(detail); setRunError('') }
      } catch (error) {
        if (active) setRunError((error as Error).message)
      }
    }
    setRunError('')
    void load()
    const timer = setInterval(load, 1800)
    return () => { active = false; clearInterval(timer) }
  }, [runId, page])
  const latestClean = useMemo(() => [...jobs].filter(job => job.kind === 'clean').sort((a, b) => b.created - a.created)[0], [jobs])
  useEffect(() => {
    if (!latestClean || !['import', 'chat', 'settings'].includes(page)) return
    let active = true
    void api('/agent/runs/' + latestClean.id).then(detail => { if (active) setCleanDetail(detail) }).catch(() => {})
    return () => { active = false }
  }, [latestClean?.id, latestClean?.state, latestClean?.stage, page])
  useEffect(() => {
    if (!['chat', 'settings', 'run', 'overview'].includes(page)) return
    void api('/agent/cases/q015').then(result => {
      setCaseAvailable(!!result?.snapshot_id && (result.steps || []).some((step: Row) =>
        step.evidence || step.before || step.after || step.result))
    }).catch(() => setCaseAvailable(false))
  }, [page, report?.snapshot_id])
  const evidenceRequest = useRef(0)
  const profileRequestSnapshot = page === 'student' ? profileSnapshot : ''
  const requestedStudent = page === 'student' ? student : ''
  const requestedGroup = page === 'student' ? profileGroup : ''
  const requestedNode = page === 'student' ? node : ''
  const profileKey = JSON.stringify([requestedStudent, requestedGroup, requestedNode, profileRequestSnapshot || report?.snapshot_id || ''])
  const [profileError, setProfileError] = useState('')
  useLayoutEffect(() => { evidenceRequest.current++ }, [profileKey, page])
  useEffect(() => {
    const controller = new AbortController()
    setProfileError('')
    void api('/profiles' + query({ student: requestedStudent, group: requestedGroup, node: requestedNode,
      snapshot_id: profileRequestSnapshot || report?.snapshot_id || '' }), { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted) setData({ ...result, profileKey })
    }).catch(error => {
      if (!controller.signal.aborted) { setProfileError(error.message); message.error(error.message) }
    })
    return () => controller.abort()
  }, [profileKey, profileRequestSnapshot, report?.snapshot_id, requestedStudent, requestedGroup, requestedNode])
  const profileReady = data.profileKey === profileKey
  useEffect(() => {
    if (page !== 'student' || !focusIndicator || !profileReady) return
    const indicator = document.getElementById('indicator-' + focusIndicator)
    indicator?.scrollIntoView({ block: 'nearest' })
    indicator?.querySelector('button')?.focus({ preventScroll: true })
    setFocusIndicator('')
  }, [page, focusIndicator, profileReady])
  const cleaningAudit = report?.last_agent_review
  const groups: Row[] = data.groups || []
  const students: Row[] = data.students || []
  const currentClass = students.find(s => s.学生编号 === student)?.模拟班级编号
  const current: Row = data.profile || {}
  const axes: Axis[] = useMemo(() => topic
    ? (current.axes || noAxes).filter((a: Axis) => a.id.startsWith(topic)) : current.axes || noAxes,
  [current.axes, current.topics, topic])
  const comparison: Row = student && groups.find(g => g.group === currentClass) || {}
  const compare: Axis[] = useMemo(() => topic
    ? (comparison.axes || noAxes).filter((a: Axis) => a.id.startsWith(topic)) : comparison.axes || noAxes,
  [comparison.axes, comparison.topics, topic])
  const readEvidence = async (target: DiagnosticSelection & { student: string; axis: DiagnosticAxis }) => {
    const request = ++evidenceRequest.current
    const { axis, ...params } = target
    try {
      const rows = await api('/profiles/evidence' + query(params))
      if (request === evidenceRequest.current) {
        setAnswerId(rows[0]?.作答事件编号 || '')
        setSelected({ axis, rows, ...params })
      }
    } catch (error) {
      if (request === evidenceRequest.current) message.error((error as Error).message)
    }
  }
  const chooseEvidence = (axis: Axis) => {
    if (!data.snapshot_id || !profileReady) return
    void readEvidence({ axis, indicator: axis.id, student, group: profileGroup, node, snapshot_id: data.snapshot_id })
  }
  const openReplay = () => perform(async () => setReplay(await api<CaseData>('/agent/cases/q015')))
  const selectedAnswer: Row | undefined = selected?.rows.find((row: Row) => row.作答事件编号 === answerId)
  const linkedDraft = selectedAnswer && drafts.find(d => d.snapshot_id === selected?.snapshot_id
    && d.student === selectedAnswer.学生编号 && d.anchor_event === answerId && d.approved)
    || (selectedAnswer && drafts.find(d => d.snapshot_id === selected?.snapshot_id && d.student === selectedAnswer.学生编号 && d.anchor_event === answerId))
  const teachingSource = linkedDraft?.citations?.find((item: Row) => item.locator === selectedAnswer?.材料编号)
    || linkedDraft?.citations?.find((item: Row) => String(item.locator).startsWith('L'))
  const importFiles = () => perform(async () => {
    const payload = new FormData()
    files.forEach(f => payload.append('files', f))
    payload.append('mapping', mapping)
    const result = await api('/imports', { ...post, body: payload })
    if (result.skipped?.length) message.info('已跳过字段说明：' + result.skipped.join('、'))
    setFiles([])
    await refresh()
  })
  const openDoc = (id: string) => perform(async () => { setDoc(await api('/materials/' + id)); setChanges({}) })
  const launch = (target: Row, onLaunched?: () => void) => perform(async () => {
    const result = await api('/agent/runs', json('POST', { node, snapshot_id: data.snapshot_id, ...target }))
    openRun(result.run_id)
    onLaunched?.()
  })
  const openDraft = (d: Row) => {
    setDraft({ ...d, message: d.message ?? d.follow_up })
    setPreview(undefined)
    setBinding({ label: '', webhook: '', secret: '' })
  }
  const savedDraft = draft && drafts.find(d => d.id === draft.id && d.revision === draft.revision)
  const changed = !!savedDraft && ((savedDraft.message ?? savedDraft.follow_up) !== draft?.message ||
    savedDraft.observation !== draft?.observation || savedDraft.uncertainty !== draft?.uncertainty ||
    JSON.stringify(savedDraft.teaching_points) !== JSON.stringify(draft?.teaching_points) ||
    JSON.stringify(savedDraft.exercises) !== JSON.stringify(draft?.exercises))
  const review = savedDraft?.final_review || draft?.final_review
  const currentReview = !changed && review?.input_version === draft?.revision ? review : undefined
  const saveDraft = (approved: boolean) => perform(async () => {
    const saved = await api('/recommendations/' + draft!.id, json('PUT', { revision: draft!.revision,
      exercises: draft!.exercises.map((q: Row) => q.question_id), message: draft!.message,
      hints: Object.fromEntries(draft!.exercises.map((q: Row) => [q.question_id, q.hints])),
      teaching_texts: draft!.teaching_points?.map((item: Row) => item.text),
      observation: draft!.observation, uncertainty: draft!.uncertainty, approved }))
    setDraft(saved)
    await refresh()
    if (saved.approved) setPreview(await api('/recommendations/' + saved.id + '/preview'))
    else if (saved.review_run_id) { setDraft(undefined); openRun(saved.review_run_id) }
  })
  const activeSnapshot = report?.snapshot_id || ''
  const openProfile = (kind: ProfileTarget['kind'], id: string, node: string, snapshot: string, indicator: string) => {
    setTarget({ kind, id }); setNode(node); setProfileSnapshot(snapshot); setTopic('')
    setFocusIndicator(indicator); goTo('student')
  }
  const nodeChoice = <Select aria-label="测验节点" value={node} className="profile-node" onChange={value => { setNode(value); setFocusIndicator('') }}
    options={[{ value: '', label: '全部测验' }, ...(data.nodes || []).map((v: string) => ({ value: v, label: v }))]} />
  const profileChoices = [
    { label: '班级', options: groups.map(g => ({ value: 'group:' + g.group, label: g.group + ' · 班级' })) },
    { label: '学生', options: students.map(s => ({ value: 'student:' + s.学生编号, label: s.学生编号 + ' · ' + s.模拟班级编号 })) },
  ]
  const confirmClean = (detail: Row) => perform(async () => {
    await api('/imports/' + detail.payload.batch_id + '/confirm' + query({ run_id: detail.id }), post)
    setRunDetail(await api('/agent/runs/' + detail.id))
    await refresh()
    setOnboardingSignal(value => value + 1)
    message.success('已核对并启用这批数据')
  })
  const reopenOnboarding = () => perform(async () => {
    await api('/onboarding/reopen', post)
    setOnboardingSignal(value => value + 1)
    message.success('已重新显示第一次使用引导')
  })
  const navExpanded = mobile ? mobileOpen : !rail
  const navAction = navExpanded ? '收起导航' : '展开导航'
  const toggleNav = () => {
    if (mobile) { setMobileOpen(!mobileOpen); return }
    setRail(!rail)
    try { localStorage.setItem(sidebarPreference, String(!rail)) } catch { /* The toggle still works for this visit. */ }
  }
  return <Layout className="shell">
    <Layout.Sider width={!mobile && rail ? 72 : 216} className={'sidebar' + (!mobile && rail ? ' sidebar-rail' : '')}>
      <div className="brand">
        <span className="brand-symbol"><NavIcon name="agent" /></span>
        <div className="brand-name" aria-label="智慧学伴">智慧学伴</div>
        <Tooltip title={navAction} placement={mobile ? 'bottom' : 'right'}>
          <button type="button" className="nav-toggle" ref={navToggle} aria-label={navAction} aria-controls="workbench-navigation"
            aria-expanded={navExpanded} onClick={toggleNav}><NavIcon name="toggle" flipped={!navExpanded} /></button>
        </Tooltip>
      </div>
      <nav id="workbench-navigation" aria-label="工作台导航" hidden={mobile && !mobileOpen}>
        {pages.map(([key, label]) => <Tooltip key={key} title={!mobile && rail ? label : undefined} placement="right">
          <button type="button" className="nav-item" aria-current={page === key ? 'page' : undefined} aria-label={label} onClick={() => {
            goTo(key)
            if (mobile) { setMobileOpen(false); navToggle.current?.focus() }
          }}>
            <NavIcon name={key} /><span>{label}</span>
          </button>
        </Tooltip>)}
      </nav>
    </Layout.Sider>
    <Layout.Content className="content">
      {page !== 'overview' && <header className="page-head"><Title level={2}>{page === 'run' ? '任务详情' : pages.find(p => p[0] === page)?.[1]}</Title>
        {page === 'run' && <Button onClick={() => goTo('settings')}>返回设置中的任务记录</Button>}</header>}
      <Diagnostics active={page === 'overview'} data={data.groups && (!activeSnapshot || activeSnapshot === data.snapshot_id) ? data as Profile : undefined}
        count={students.length} attempts={report?.valid_scored} busy={busy} api={api}
        openImport={() => goTo('import')} openEvidence={target => void readEvidence(target)}
        openStudent={(id, node, snapshot, indicator) => openProfile('student', id, node, snapshot, indicator)}
        openGroup={(id, node, snapshot, indicator) => openProfile('group', id, node, snapshot, indicator)}
        launchGroup={({ group, node, snapshot_id }) => void launch({ group, node, snapshot_id })} />
      <div hidden={page !== 'chat'}>
        <LearningChat snapshot={activeSnapshot}
          students={(data.snapshot_id === activeSnapshot ? students : []) as Parameters<typeof LearningChat>[0]['students']}
          groups={(data.snapshot_id === activeSnapshot ? groups : []).map(group => group.group)}
          nodes={data.snapshot_id === activeSnapshot ? data.nodes : []} access={modelAccess} api={api}
          onboardingSignal={onboardingSignal}
          openAccess={() => goTo('settings')} openCitation={setCitation} openImport={() => goTo('import')}
          openRun={openRun} openDraft={id => { const found = drafts.find(d => d.id === id); if (found) openDraft(found); else message.info('建议正在同步，请稍后再试') }}
          openProfile={(kind, id, node, indicator, snapshot) => openProfile(kind, id, node, snapshot || activeSnapshot, indicator)}
          openPage={goTo}
          onTaskStarted={() => { void refresh(); setOnboardingSignal(value => value + 1) }} />
      </div>
      {page === 'run' && <AgentWorkbench key={runId} jobs={jobs} detail={runDetail?.id === runId ? runDetail : undefined} runId={runId}
        caseAvailable={caseAvailable}
        error={runError} loading={busy} legacy={!!report?.legacy_pending}
        open={openRun} openImport={() => goTo('import')}
        openReplay={openReplay}
        openDraft={key => { const found = drafts.find(d => d.id === key); if (found) openDraft(found); else message.info('建议正在同步，请稍后再试') }}
        migrate={() => perform(async () => { openRun((await api('/imports/migrate', post)).run_id) })}
        retry={() => perform(async () => { setRunDetail(await api('/jobs/' + runId + '/retry', post)) })}
        rebase={() => perform(async () => { openRun((await api('/imports/' + runDetail?.payload.batch_id + '/rebase', post)).run_id) })}
        confirm={() => perform(async () => {
          await api('/imports/' + runDetail?.payload.batch_id + '/confirm' + query({ run_id: runId }), post)
          setRunDetail(await api('/agent/runs/' + runId)); await refresh(); message.success('已启用快照')
        })} />}
      {page === 'import' && <>
        <Card title="当前步骤" extra={latestClean && <Button onClick={() => openRun(latestClean.id)}>查看任务详情</Button>}>
          <Steps current={latestClean?.state === 'failed' ? 1 : cleanDetail?.stage === 'confirm' && cleanDetail.state === 'done' ? 2 :
            report?.snapshot_id ? 3 : latestClean ? 1 : 0} items={[
            { title: '选择文件' }, { title: '清洗与模型复核' }, { title: '核对并确认' }, { title: '快照生效' }]} />
          <div className="spaced">
            {latestClean?.state === 'failed' ? <Alert type="error" title={'本批清洗失败：' + (latestClean.error || '请查看任务详情')}
              action={<Button onClick={() => openRun(latestClean.id)}>查看并重试</Button>} />
              : cleanDetail?.stage === 'confirm' && cleanDetail.state === 'done' ? <Alert type="warning" title="清洗复核已完成，等待教师核对"
                description={<CleaningCounts report={cleanDetail.report as Parameters<typeof CleaningCounts>[0]['report']} />}
                action={<Button type="primary" loading={busy} onClick={() => confirmClean(cleanDetail)}>核对并确认本批数据</Button>} />
              : latestClean && ['queued', 'running'].includes(latestClean.state) ? <Alert type="info" title="本批数据正在清洗"
                description="可以查看真实计数、异常和复核状态。" action={<Button onClick={() => openRun(latestClean.id)}>查看本批进度</Button>} />
              : report?.snapshot_id ? <Space wrap><Tag color="success">已确认快照</Tag>
                <Button type="primary" onClick={() => goTo('chat')}>回到对话，问第一个问题</Button>
                <Button onClick={() => goTo('materials')}>前往课程与资料</Button></Space>
              : <Text type="secondary">选择 XLSX 工作簿，或一次选中配套 CSV 文件。</Text>}
          </div>
        </Card>
        <Card title="导入学情文件">
          <input ref={importInput} type="file" hidden multiple accept=".csv,.xlsx" onChange={e => setFiles(Array.from(e.target.files || []))} />
          <Space wrap><Button onClick={() => importInput.current?.click()}>选择文件</Button>
            <Text>{files.length ? '已选 ' + files.length + ' 个文件' : '尚未选择文件'}</Text>
            <Button type="primary" loading={busy} disabled={!files.length} onClick={importFiles}>开始清洗</Button>
          </Space>
          {!!files.length && <ul>{files.map((file, index) => <li key={file.name + index}>
            {file.name} <Button type="link" onClick={() => setFiles(current => current.filter((_, i) => i !== index))}>移除</Button>
          </li>)}</ul>}
          <details className="spaced"><summary>自定义列映射</summary><Input.TextArea value={mapping} onChange={e => setMapping(e.target.value)} placeholder='{"学号":"学生编号"}' /></details>
          <Paragraph type="secondary" className="spaced">可上传 XLSX 工作簿或成组 CSV，也支持名册和总分。缺少文件时，清洗报告会列出无法关联的原始行。</Paragraph>
        </Card>
        {report?.legacy_pending && <Alert type="info" title="已有历史批次，需迁移为快照后启用"
          action={<Button onClick={() => goTo('settings')}>打开任务记录</Button>} />}
        {report && <Card title="清洗计数"><CleaningCounts report={report as Parameters<typeof CleaningCounts>[0]['report']} /></Card>}
        {cleaningAudit?.trace?.length > 0 && <Card title="Agent 清洗记录">
          <ReviewPanel review={cleaningAudit} collapseDetails={cleaningAudit.decision === 'ready'} />
          <details><summary>查看清洗工具输出</summary><pre className="preserve">{JSON.stringify(cleaningAudit.trace, null, 2)}</pre></details>
        </Card>}
        <Card title="已确认快照的清洗报告" extra={<Button href="/api/imports/issues.csv">下载异常 CSV</Button>}>
          <Space wrap>{Object.entries(report?.states || {}).map(([k, v]) => <Tag key={k}>{k} {String(v)}</Tag>)}</Space>
          <Table<Row> size="small" rowKey="id" dataSource={report?.issues || []}
            columns={[{ title: '文件', dataIndex: 'source' }, { title: '行', dataIndex: 'line' }, { title: '字段', dataIndex: 'field' },
              { title: '原值', dataIndex: 'original', render: v => typeof v === 'string' ? v : JSON.stringify(v) }, { title: '原因', dataIndex: 'reason', ...issueFilters(report?.issues || []) },
              { title: '', render: (_, r) => (r.source.includes('作答记录') || r.sheet === '作答记录' || r.source.startsWith('教师修订-')) && <Button onClick={() => { setCorrection(r); setCorrected('{}') }}>修正</Button> }]} />
        </Card>
      </>}
      {page === 'materials' && <>
        <Card title="课程地图"><Table<Row> size="small" rowKey="指标编号" dataSource={curriculum.indicators || []} pagination={false}
          expandable={{ expandedRowRender: indicator => <Space wrap>
            {(curriculum.questions || []).filter((q: Row) => q.指标编号?.split('、').includes(indicator.指标编号)).map((q: Row) =>
              <Button key={q.材料编号} title={q.题干或内容} onClick={() => perform(async () => {
                const matches = await api('/materials/search' + query({query:q.材料编号}))
                const source = matches.find((m: Row) => m.locator === q.材料编号)
                if (source) setCitation(source)
                else message.info(q.题干或内容 || q.来源或资料文件 || '请先发布题库')
              })}>{q.材料编号}</Button>)}
          </Space> }}
          columns={['主题', '观察指标', '课标整理编号', '课标文件页／正文页', '教材正文页'].map(k => ({ title: k, dataIndex: k }))} /></Card>
        <Card title="教学资料" extra={<Button onClick={() => perform(async () => { const r = await api('/materials/seed-text', post); await refresh(); await openDoc(r.id) })}>整理已导入题库</Button>}>
          <Input type="file" accept=".pdf,.doc,.docx,.png,.jpeg,.jpg,.webp" onChange={e => { const file = e.target.files?.[0]; if (file) void perform(async () => {
            const body = new FormData(); body.append('file', file); await api('/materials', { ...post, body }); await refresh()
          }) }} />
          <div className="books">{materials.map(d => <button key={d.id} className="book" onClick={() => openDoc(d.id)}><BookOutlined /> <strong>{d.name}</strong><small>{({ processing: '解析中', review: '待校对', indexing: '建索引中', published: '已发布', replaced: '已替代' } as Row)[d.state] || d.state}</small></button>)}</div>
        </Card>
        <Card title="检索已发布资料"><Input.Search value={lookup} onChange={e => setLookup(e.target.value)} onSearch={v => perform(async () => setHits(await api('/materials/search' + query({query: v})) ))} enterButton="查找" />
          {hits.map(h => <Paragraph key={h.id} className="spaced"><Button type="link" onClick={() => setCitation(h)}>{h.title} · {h.locator}</Button> {h.text.slice(0, 130)}</Paragraph>)}</Card>
        <Card title="下一步" size="small"><Space wrap><Button type="primary" onClick={() => goTo('overview')}>前往班级诊断</Button>
          <Button onClick={() => goTo('chat')}>回到学情对话</Button><Text type="secondary">课程与资料可以跳过，之后随时补充和发布。</Text></Space></Card>
      </>}
      {page === 'student' && <>
        <Card className="student-profile-card" title={target ? (student ? '学生 ' : '班级 ') + target.id : '学习表现'}
          extra={<Space wrap><Select aria-label="选择画像对象" placeholder="搜索学生或班级" className="profile-target" allowClear
            showSearch={{ optionFilterProp: 'label' }} value={target && target.kind + ':' + target.id} options={profileChoices}
            onChange={(value?: string) => {
              const kind = value?.startsWith('student:') ? 'student' : 'group'
              setTarget(value ? { kind, id: value.slice(kind.length + 1) } : undefined)
              setProfileSnapshot(''); setTopic(''); setFocusIndicator('')
            }} />{nodeChoice}</Space>}>
          {!target ? <Empty description="选择学生或班级查看画像" /> : !profileReady
            ? profileError ? <Alert type="error" title={profileError} /> : <Skeleton active /> : <>
            <Space wrap><Button type={!topic ? 'primary' : 'default'} onClick={() => setTopic('')}>主题总览</Button>
              {(current.topics || []).map((a: Axis) => <Button key={a.id} type={topic === a.id ? 'primary' : 'default'} onClick={() => setTopic(a.id)}>{a.label}</Button>)}</Space>
            {!!axes.length && <div className="student-profile-grid">
              <ProfileRadar label={student ? '学生' : '班级'} axes={topic ? axes : current.topics || noAxes}
                compare={topic ? compare : comparison.topics || noAxes} onSelect={chooseEvidence} />
              {student ? <Comparison axes={axes} compare={compare} select={chooseEvidence} /> :
                <section className="class-indicators" aria-label="班级指标与覆盖">
                  <Title level={5}>指标与覆盖</Title>
                  {axes.map(axis => <div className="class-indicator" id={'indicator-' + axis.id} key={axis.id}>
                    <Button type="link" className="comparison-label" onClick={() => chooseEvidence(axis)}>{axis.label}</Button>
                    <strong>{axis.score == null ? '暂无得分' : axis.score + '%'}</strong>
                    <Text type="secondary">覆盖 {axis.coverage}/{axis.population} 人 · {axis.n} 条作答</Text>
                  </div>)}
                </section>}
            </div>}
            {!!student && !!current.totals?.length && <Table rowKey="节点编号" dataSource={current.totals} pagination={false} columns={[{ title: '测验', dataIndex: '节点编号' }, { title: '总分', dataIndex: '测验总分' }]} />}
            {!!data.snapshot_id && <Space wrap className="spaced">
              <Button type="primary" loading={busy} onClick={() => launch({ student, group: profileGroup, node, snapshot_id: data.snapshot_id })}>
                {student ? '生成学习建议' : '生成班级建议'}
              </Button>
              {student && <Button loading={busy} onClick={() => launch({ student, node, variant: true, snapshot_id: data.snapshot_id })}>生成建议与变式题</Button>}
            </Space>}
          </>}
        </Card>
      </>}
      {page === 'drafts' && <>
        <Card title="学习建议" extra={<Button disabled={!report?.snapshot_id} href="/api/export?formal=true">导出已确认成果</Button>}><Table rowKey="id" dataSource={drafts} columns={[
          { title: '对象', render: (_, d) => d.student || d.group }, { title: '测验', render: (_, d) => d.node || '全部' },
          { title: '学情概述', dataIndex: 'observation' }, { title: '状态', render: (_, d) => !d.snapshot_id ? '历史记录 · 版本未登记' : d.approved ? '已确认' : ({ pass: '待确认', revise: '已退回', pending: '待终审' } as Row)[d.review_state] },
          { title: '', render: (_, d) => <Button onClick={() => openDraft(d)}>打开</Button> },
        ]} /></Card>
        {!!deliveries.length && <Card title="投递记录"><Table rowKey="id" dataSource={deliveries} columns={['student','group','state','message'].map((k,i) => ({ dataIndex: k, title: ['学生','收件群','状态','备注'][i] }))} /></Card>}
      </>}
      {page === 'settings' && <>
        <Card id="model-access" title="模型接入">
          <ModelAccessPage access={modelAccess} api={api} update={setModelAccess} />
        </Card>
        <Card title="后台任务" extra={<Text type="secondary">清洗与建议正在发起的位置同步显示进度</Text>}>
          <Table<Row> rowKey="id" dataSource={jobs} columns={[{ title: '类型', render: (_,j) => ({ extract:'解析资料', publish:'建立索引', ...runNames } as Row)[j.kind] || j.kind },
            { title: '对象', render: (_,j) => j.payload?.label || j.payload?.student || j.payload?.group || '' },
            { title: '状态', render: (_,j) => <Tag color={j.state === 'failed' ? 'error' : j.state === 'done' ? 'success' : 'processing'}>{({ queued:'排队', running:'处理中', done:'完成', failed:'失败' } as Row)[j.state]}</Tag> },
            { title: '错误', dataIndex: 'error' },
            { title: '', render: (_,j) => <Space wrap>{['clean', 'generate', 'recommendation_review'].includes(j.kind) && <Button onClick={() => openRun(j.id)}>查看运行</Button>}
              {j.state === 'failed' && <Button onClick={() => perform(() => api('/jobs/' + j.id + '/retry', post))}>重试</Button>}</Space> }]} />
        </Card>
        <Card title="运行环境"><Space wrap>
          <Tag color={health.model_configured ? 'success' : 'warning'}>模型 {health.model_configured ? '已配置' : '待配置'}</Tag>
          <Tag color={health.ocr_configured ? 'success' : 'warning'}>PaddleOCR {health.ocr_configured ? '已配置' : '待安装'}</Tag>
          <Tag>SQLite 本机业务库</Tag>
          <Tag>Windows 原生部署</Tag>
        </Space></Card>
        <Card title="使用引导"><Space wrap><Button onClick={reopenOnboarding}>重新显示「第一次使用」</Button>
          <Text type="secondary">「稍后」只在本次会话隐藏，「已了解」跨刷新记忆。</Text></Space></Card>
      </>}
    </Layout.Content>
    <Drawer open={!!replay} title="演示串讲 · Q015" size="min(1100px, 100vw)" onClose={() => setReplay(undefined)}
      styles={{ body: { display: 'flex', flexDirection: 'column', overflow: 'hidden' } }}>
      {replay && <CaseReplay data={replay} openCitation={setCitation} />}
    </Drawer>
    <Drawer open={!!correction} title="修正作答记录" size={500} onClose={() => setCorrection(undefined)}>
      {correction && <><Paragraph>{correction.source} · 第 {correction.line} 行 · {correction.reason}</Paragraph>
        <Input.TextArea aria-label="修订字段" value={corrected} onChange={e => setCorrected(e.target.value)} rows={6} placeholder='{"得分":"2"}' />
        <Button type="primary" className="spaced" loading={busy} onClick={() => perform(async () => {
          const {source, sheet, line, file_id} = correction
          const result = await api('/imports/corrections', json('POST', {source, sheet, line, file_id, changes: JSON.parse(corrected)}))
          setCorrection(undefined); openRun(result.run_id)
        })}>保存修订</Button></>}
    </Drawer>
    <Drawer open={!!selected} title="作答证据" size="min(980px, 100vw)" onClose={() => { evidenceRequest.current++; setSelected(undefined) }}>
      {selected && <><Space wrap><Tag>{selected.student || selected.group}</Tag><Tag>{selected.node || '全部测验'}</Tag>
        <Text>{selected.axis.label} · {selected.rows.length} 条作答</Text></Space>
        <details><summary>数据快照</summary><code>{selected.snapshot_id}</code></details>
        {selected.rows.length > 1 && <Select className="spaced" aria-label="选择作答" value={answerId} onChange={setAnswerId}
          options={selected.rows.map((row: Row) => ({ value: row.作答事件编号, label: row.节点编号 + ' · ' + row.材料编号 + ' · ' + row.得分 + '/' + row.满分 }))} />}
        {selectedAnswer ? <div className="spaced">
          <AnswerEvidence key={answerId} answer={selectedAnswer as Parameters<typeof AnswerEvidence>[0]['answer']} />
          <Space wrap className="spaced">
            <Button type="primary" loading={busy} onClick={() => {
              if (linkedDraft) openDraft(linkedDraft)
              else void launch({ student: selectedAnswer.学生编号, node: selectedAnswer.节点编号, snapshot_id: selected.snapshot_id, anchor_event: answerId },
                () => setSelected(undefined))
            }}>{linkedDraft ? '查看对应练习' : '基于本题生成建议'}</Button>
            {teachingSource && <Button onClick={() => setCitation(teachingSource)}>查看资料原文</Button>}
          </Space>
        </div> : <Empty description={selected.axis.status} />}
      </>}
    </Drawer>
    <Drawer open={!!doc} title={doc?.name} size={850} onClose={() => setDoc(undefined)}>
      {doc && <><Space><Tag>{doc.state}</Tag><Button disabled={!['review','published'].includes(doc.state) || !Object.keys(changes).length} onClick={() => perform(async () => {
        const result = await api('/materials/' + doc.id, json('PUT', { changes })); await openDoc(result.id); await refresh()
      })}>保存校订</Button><Button type="primary" disabled={doc.state !== 'review' || !!Object.keys(changes).length} onClick={() => perform(async () => {
        await api('/materials/' + doc.id + '/publish', post); setDoc(undefined); await refresh()
      })}>确认发布</Button></Space>
      {doc.blocks.map((c: Row) => <Card size="small" className="spaced" key={c.id} title={c.locator}
        extra={<Button type="link" onClick={() => setCitation(c)}>查看出处</Button>}>
        <Input.TextArea value={changes[c.id] ?? c.text} onChange={e => setChanges({ ...changes, [c.id]: e.target.value })} autoSize={{ minRows: 2, maxRows: 8 }} />
      </Card>)}</>}
    </Drawer>
    <Drawer open={!!citation} title={citation?.title + ' · ' + citation?.locator} size={730} onClose={() => setCitation(undefined)}>
      {citation && <><Paragraph className="preserve">{citation.text}</Paragraph><SourcePage key={citation.id} citation={citation} /></>}
    </Drawer>
    <Drawer open={!!draft} title={'教学安排 · ' + (draft?.student || draft?.group || '')} size="min(860px, 100vw)" onClose={() => setDraft(undefined)}
      footer={draft && <Space wrap className="draft-controls">
        {changed ? <Button type="primary" loading={busy} onClick={() => saveDraft(false)}>保存修订并重新终审</Button>
          : draft.approved ? <Button type="primary" loading={busy} onClick={() => perform(async () => setPreview(await api('/recommendations/' + draft.id + '/preview')))}>查看学生练习单</Button>
          : <Button type="primary" loading={busy} disabled={currentReview?.decision !== 'pass'} onClick={() => saveDraft(true)}>确认并预览</Button>}
        {(savedDraft?.review_run_id || draft.run_id) && <Button onClick={() => {
          openRun(savedDraft?.review_run_id || draft.run_id); setDraft(undefined); setSelected(undefined)
        }}>查看运行</Button>}
        {!changed && !draft.approved && currentReview?.decision !== 'pass' && <Text type="secondary">当前修订需通过终审</Text>}
      </Space>}>
      {draft && <><Title level={5}>学情与依据</Title>
        <Paragraph editable={draft.snapshot_id ? { onChange: observation => { setDraft({ ...draft, observation, approved: false }); setPreview(undefined) } } : false}>{draft.observation}</Paragraph>
        <Paragraph editable={draft.snapshot_id ? { onChange: uncertainty => { setDraft({ ...draft, uncertainty, approved: false }); setPreview(undefined) } } : false}>{draft.uncertainty}</Paragraph>
        {draft.evidence.map((r: Row) => <Paragraph key={r.id}>{r.材料编号} · {r.得分}/{r.满分} · {r.阶段 || r.节点编号} · {r.评分点得分}<br />{r.解答过程}</Paragraph>)}
        {draft.teaching_points?.length ? <><Title level={5}>教学说明与原文</Title>
          {draft.teaching_points.map((point: Row, index: number) => <Card size="small" className="spaced" key={index}>
            <Paragraph editable={{ onChange: value => {
              const teaching_points = draft.teaching_points.map((before: Row, n: number) =>
                n === index ? { ...before, text: value } : before)
              setDraft({ ...draft, teaching_points, approved: false }); setPreview(undefined)
            } }}>{point.text}</Paragraph>
            {draft.citations.filter((c: Row) => point.citation_ids.includes(c.id)).map((c: Row) =>
              <Button key={c.id} type="link" onClick={() => setCitation(c)}>{c.title} · {c.locator}</Button>)}
          </Card>)}</> : draft.citations.map((c: Row) =>
          <Button key={c.id} type="link" onClick={() => setCitation(c)}>{c.title} · {c.locator}</Button>)}
        <Title level={5}>练习顺序</Title>
        {draft.exercises.map((q: Row, index: number) => <Card size="small" className="spaced" key={q.question_id} title={(index + 1) + ' · ' + q.question_id} extra={<Space>
          {[-1, 1].map(delta => <Button key={delta} size="small" disabled={index + delta < 0 || index + delta >= draft.exercises.length} onClick={() => {
            const items = [...draft.exercises]; const item = items.splice(index, 1)[0]; items.splice(index + delta, 0, item); setDraft({ ...draft, exercises: items, approved: false }); setPreview(undefined)
          }}>{delta < 0 ? '上移' : '下移'}</Button>)}</Space>}>
          <Paragraph className="practice-question">{draft.questions[q.question_id]?.题干或内容}</Paragraph>{q.purpose && <Text type="secondary">{q.purpose}</Text>}
          {!!q.evidence_ids?.length && <details className="spaced"><summary>查看练习依据</summary>
            {draft.evidence.filter((row: Row) => q.evidence_ids.includes(row.id)).map((row: Row) =>
              <Paragraph key={row.id}>{row.材料编号} · {row.得分}/{row.满分} · {row.评分点得分}<br />{row.解答过程}</Paragraph>)}
          </details>}
          <ol>{q.hints.map((h: string, hintIndex: number) => <li key={hintIndex}>
            <Paragraph editable={draft.snapshot_id ? { onChange: hint => {
              const exercises = draft.exercises.map((exercise: Row) => exercise.question_id === q.question_id
                ? { ...exercise, hints: exercise.hints.map((before: string, n: number) => n === hintIndex ? hint : before) } : exercise)
              setDraft({ ...draft, exercises, approved: false }); setPreview(undefined)
            } } : false}>{h}</Paragraph>
          </li>)}</ol></Card>)}
        {draft.checked_variant && <Alert className="spaced" type="success" message={draft.checked_variant.question} description={'教师参考答案：' + draft.checked_variant.answer} />}
        <Title level={5}>给学生的话</Title><Input.TextArea value={draft.message} onChange={e => { setDraft({ ...draft, message: e.target.value, approved: false }); setPreview(undefined) }} rows={4} />
        <div className="spaced"><Tag>修订 {draft.revision}</Tag><Tag>{changed ? '有未保存修改' : draft.approved ? '已确认' : currentReview?.decision === 'pass' ? '通过终审' : currentReview?.decision === 'revise' ? '退回修订' : draft.snapshot_id ? '待终审' : '历史记录'}</Tag></div>
        {currentReview && <Card size="small" className="spaced" title="模型终审"><ReviewPanel review={currentReview}
          canOpenRef={ref => draft.citations.some((item: Row) => item.id === ref)}
          openRef={ref => setCitation(draft.citations.find((item: Row) => item.id === ref))} /></Card>}
        {preview && <div ref={practicePreview}><Card className="spaced" size="small" title="学生练习单"><Paragraph className="preserve">{preview.text}</Paragraph></Card></div>}
        {draft.student && <details className="spaced"><summary>配置学生专属飞书群</summary><Space orientation="vertical" className="wide">
          <Input placeholder="群标签（请与学生确认）" value={binding.label} onChange={e => setBinding({ ...binding, label: e.target.value })} />
          <Input.Password placeholder="Webhook 地址" value={binding.webhook} onChange={e => setBinding({ ...binding, webhook: e.target.value })} />
          <Input.Password placeholder="签名密钥（可选）" value={binding.secret} onChange={e => setBinding({ ...binding, secret: e.target.value })} />
          <Space><Button disabled={!binding.label || !binding.webhook} onClick={() => perform(async () => {
            await api('/deliveries/binding/' + param(draft.student), json('PUT', binding)); setBinding({ label:'', webhook:'', secret:'' }); setPreview(undefined); message.success('已保存绑定')
          })}>保存绑定</Button><Button onClick={() => perform(async () => {
            const r = await api('/deliveries/test/' + param(draft.student), post); message.info(r.message || '测试卡已发出，请核对群组')
          })}>发送测试卡</Button></Space></Space></details>}
        {draft.student && <Button className="spaced" type="primary" loading={busy} disabled={!draft.approved || !preview?.group} onClick={() => perform(async () => {
          const r = await api('/deliveries/send/' + draft.id + query({ revision: String(draft.revision) }), post); message.info(r.message || '投递状态：' + r.state); await refresh()
        })}>发送到 {preview?.group || '学生专属群'}</Button>}
      </>}
    </Drawer>
  </Layout>
}

applyWorkbenchPalette()
createRoot(document.getElementById('root')!).render(<ConfigProvider locale={zhCN} theme={workbenchTheme}><App><Workbench /></App></ConfigProvider>)
