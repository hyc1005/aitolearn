import { useEffect, useState } from 'react'
import { Alert, Button, Drawer, Empty, Select, Space, Spin } from 'antd'
import { AnswerEvidence, type Answer } from './AnswerEvidence'
import type { Api } from './ModelAccess'

type Spread = { count: number; low: number; q1: number; median: number; q3: number; high: number; points: number[] }
type Axis = {
  id: string; label: string; topic: string; topic_label: string; score: number | null; status: string
  n: number; coverage: number; population: number; nodes: number; zero_count: number; distribution: Spread | null
  trend: { node: string; date: string; score: number; n: number }[]
  questions: { id: string; source: string }[]; references: string[]
}
type Diagnosis = { snapshot_id: string; group: string; student: string; node: string; indicators: Axis[] }
type Events = {
  total: number; items: string[]; nodes: string[]
  timeline: { node: string; date: string; count: number }[]
  rows: { id: string; item: string; node: string; date: string; score: number; maximum: number }[]
}
const PAGE_SIZE = 10

function ScorePlot({ axis }: { axis: Axis }) {
  const spread = axis.distribution
  if (axis.trend.length) return <ol className="diagnosis-sessions">
    {axis.trend.map(point => <li key={point.node}><div>{point.date || '日期未登记'} · {point.node}</div>
      <div className="diagnosis-rail" aria-hidden="true"><i style={{ width: point.score + '%' }} /></div>
      <strong>{point.score}%</strong><span> · {point.n} 条有效作答</span></li>)}
  </ol>
  if (!spread) return <p>{axis.status}</p>
  return <>
    <div className="diagnosis-spread" aria-hidden="true">
      {spread.points.length ? spread.points.map((point, index) =>
        <i key={index} style={{ left: point + '%', top: (6 + index * 6) + 'px' }} />) : <>
        <b style={{ left: spread.low + '%', width: spread.high - spread.low + '%' }} />
        <span style={{ left: spread.q1 + '%', width: spread.q3 - spread.q1 + '%' }} />
        <i style={{ left: spread.median + '%' }} />
      </>}
    </div>
    <p>{spread.points.length
      ? `${spread.count} 人 · ${spread.points.map(point => point + '%').join('、')}`
      : `${spread.count} 人 · 最低 ${spread.low}% · 下四分位 ${spread.q1}% · 中位数 ${spread.median}% · 上四分位 ${spread.q3}% · 最高 ${spread.high}%`}</p>
  </>
}

export function DiagnosisBoard({ runId, api }: { runId: string; api: Api }) {
  const base = '/agent/runs/' + encodeURIComponent(runId) + '/diagnosis'
  const [data, setData] = useState<Diagnosis>()
  const [failure, setFailure] = useState('')
  const [events, setEvents] = useState<Events>()
  const [eventsError, setEventsError] = useState('')
  const [axisId, setAxisId] = useState('')
  const [node, setNode] = useState('')
  const [item, setItem] = useState('')
  const [page, setPage] = useState(0)
  const [sort, setSort] = useState('topic')
  const [topicOpen, setTopicOpen] = useState('')
  const [answer, setAnswer] = useState<Answer>()
  const [answerError, setAnswerError] = useState('')
  useEffect(() => {
    const request = new AbortController()
    void api<Diagnosis>(base, { signal: request.signal }).then(setData).catch(error => {
      if (!request.signal.aborted) setFailure(error.message)
    })
    return () => request.abort()
  }, [api, base])
  useEffect(() => {
    if (!data) return
    const request = new AbortController()
    const query = new URLSearchParams({ indicator: axisId, node, item, offset: String(page * PAGE_SIZE) })
    void api<Events>(base + '/zero-events?' + query, { signal: request.signal }).then(setEvents).catch(error => {
      if (!request.signal.aborted) setEventsError(error.message)
    })
    return () => request.abort()
  }, [api, base, data, axisId, node, item, page])
  function selectAxis(value: string) {
    if (value === axisId && !item && !node && page === 0) return
    setAxisId(value); setItem(''); setNode(''); setPage(0); setEvents(undefined); setEventsError('')
  }
  async function openAnswer(id: string) {
    setAnswerError('')
    try {
      setAnswer(await api<Answer>(base + '/zero-events?' + new URLSearchParams({ event: id, indicator: axisId })))
    } catch (error) { setAnswerError((error as Error).message) }
  }
  if (failure) return <Alert type="warning" title={failure} />
  if (!data) return <Spin />
  const enough = (axis: Axis) => data.student ? axis.n >= 2 :
    axis.coverage >= Math.min(5, axis.population) && axis.coverage * 2 >= axis.population
  const ordered = [...data.indicators].sort((a, b) => (a.score ?? Infinity) - (b.score ?? Infinity))
  const weak = ordered.find(axis => axis.score != null && axis.score < 50 && enough(axis))
  const strong = [...ordered].reverse().find(axis => axis.score != null && axis.score >= 50 && enough(axis))
  const selected = data.indicators.find(axis => axis.id === axisId) || weak || ordered[0]
  const rows = sort === 'score' ? ordered : data.indicators
  const visibleAxes = sort === 'score' ? [{ name: '', rows }] : Array.from(new Set(rows.map(axis => axis.topic))).map(topic => ({
    name: rows.find(axis => axis.topic === topic)?.topic_label || topic, rows: rows.filter(axis => axis.topic === topic),
  }))
  return <div className="diagnosis-board">
    <Space wrap>
      {weak && <span className="diagnosis-metric">优先查看 <strong>{weak.id} · {weak.score}%</strong></span>}
      {strong && <span className="diagnosis-metric">相对优势 <strong>{strong.id} · {strong.score}%</strong></span>}
    </Space>
    <p>{weak ? `${weak.label}得分率为 ${weak.score}%，覆盖 ${weak.coverage}/${weak.population} 人，可先核对作答。`
      : '当前范围没有达到样本要求且低于 50% 的指标。'}</p>
    <div className="diagnosis-heading"><h3>指标得分率</h3>
      <Select aria-label="指标排序" value={sort} onChange={setSort} options={[
        { value: 'topic', label: '按主题分组' }, { value: 'score', label: '按得分排序' },
      ]} />
    </div>
    <p>主题均值按有效指标等权平均，真实零分计入，缺失不计。</p>
    <div className="diagnosis-scale"><span>0%</span><span>50% 参考线</span><span>100%</span></div>
    {visibleAxes.map(group => <section key={group.name}>
      {group.name && (() => {
        const scored = group.rows.filter(a => a.score != null)
        const mean = scored.length ? (scored.reduce((s, a) => s + a.score!, 0) / scored.length).toFixed(1) + '%' : '无有效评分'
        return <button type="button" className="diagnosis-topic" aria-expanded={topicOpen === group.name} onClick={() => setTopicOpen(topicOpen === group.name ? '' : group.name)}>
          <strong>{group.name}</strong><span>{topicOpen !== group.name && `指标均值 ${mean} · ${scored.length}/${group.rows.length} 项有效 · `}{topicOpen === group.name ? '▴' : '▾'}</span>
        </button>
      })()}
      {(!group.name || topicOpen === group.name) && group.rows.map(axis => <button type="button" className="diagnosis-bar-row" key={axis.id}
        data-priority={axis.id === weak?.id ? 'review' : axis.id === strong?.id ? 'strength' : undefined}
        aria-pressed={selected?.id === axis.id} onClick={() => selectAxis(axis.id)}>
        <span><strong>{axis.id}</strong> {axis.label}</span>
        <span className="diagnosis-rail" aria-hidden="true">
          {axis.score != null && <i style={{ width: axis.score + '%' }} />}
        </span>
        <span>{axis.score == null ? axis.status : axis.score + '%'} · {axis.n} 条 · 覆盖 {axis.coverage}/{axis.population} 人 · {axis.nodes} 场
          {axis.n > 0 && !enough(axis) && ' · 证据不足'}</span>
      </button>)}
    </section>)}
    {selected && <section>
      <h3>{selected.id} · {data.student ? '各场测验' : '学生得分率分布'}</h3>
      <ScorePlot axis={selected} />
    </section>}
    <section>
      <h3>零分事件溯源</h3>
      <Space wrap>
        <Select aria-label="零分指标" value={axisId} onChange={selectAxis} options={[
          { value: '', label: '全部指标' }, ...data.indicators.map(axis => ({ value: axis.id, label: axis.id })),
        ]} />
        <Select aria-label="零分测验" value={node} onChange={value => { setNode(value); setPage(0); setEventsError('') }}
          options={[{ value: '', label: '全部测验' }, ...(events?.nodes || []).map(value => ({ value, label: value }))]} />
        <Select aria-label="零分题号" value={item} onChange={value => { setItem(value); setPage(0); setEventsError('') }}
          options={[{ value: '', label: '全部题目' }, ...(events?.items || []).map(value => ({ value, label: value }))]} />
      </Space>
      {eventsError ? <Alert type="error" title={eventsError} /> : !events ? <Spin /> : <>
        <p>{events.total} 条有效已评分零分事件 · {events.timeline.length} 场测验</p>
        {!!events.timeline.length && <ol className="diagnosis-trend">{events.timeline.map(entry => <li key={entry.node}>
          <Button aria-pressed={node === entry.node} onClick={() => { setNode(node === entry.node ? '' : entry.node); setPage(0) }}>
            {entry.date || '日期未登记'} · {entry.node} · {entry.count} 条
          </Button>
        </li>)}</ol>}
        {!events.rows.length && <Empty description="当前筛选范围无零分事件" />}
        {events.rows.map(row => <div className="diagnosis-event" key={row.id}>
          <span>{row.date || '日期未登记'} · {row.node} · {row.item} · {row.score}/{row.maximum}</span>
          <Button onClick={() => void openAnswer(row.id)}>查看原始作答</Button>
        </div>)}
        {events.total > PAGE_SIZE && <Space wrap className="spaced">
          <Button disabled={page === 0} onClick={() => setPage(page - 1)}>上一页</Button>
          <span>第 {page + 1} / {Math.ceil(events.total / PAGE_SIZE)} 页</span>
          <Button disabled={(page + 1) * PAGE_SIZE >= events.total} onClick={() => setPage(page + 1)}>下一页</Button>
        </Space>}
      </>}
      {answerError && <Alert type="error" title={answerError} />}
    </section>
    {selected && <>
      {selected.n > 0 && !enough(selected) && <Alert type="warning" title="证据局限"
        description={`${selected.id} 的覆盖或样本量较低，先核对原始作答，再判断是否需要扩大观察范围。`} />}
      {weak && <Alert type="info" title="优先行动" description={
        <Button type="link" onClick={() => selectAxis(weak.id)}>核对 {weak.id} 的测验证据与相关题目</Button>} />}
      <details><summary>课程主题与证据关系</summary>
        <p>{selected.topic_label || selected.topic} → {selected.id} {selected.label}</p>
        {!!selected.references.length && <p>{selected.references.join(' · ')}</p>}
        <ul>{selected.questions.map(question => <li key={question.id}>{question.id} {question.source}</li>)}</ul>
      </details>
    </>}
    <Drawer open={!!answer} title="原始作答" size="min(850px, 100vw)" onClose={() => setAnswer(undefined)}>
      {answer && <AnswerEvidence answer={answer} />}
    </Drawer>
  </div>
}
