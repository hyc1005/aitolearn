import { useEffect, useState } from 'react'
import { Alert, Button, Card, Checkbox, Select, Space, Typography } from 'antd'
import { AnswerEvidence, type Answer } from './AnswerEvidence'

type Api = <T = any>(path: string, options?: RequestInit) => Promise<T>
type Direction = { id: string; kind: string; label: string; questions: string[]; topics: string[] }
type Measurement = {
  node: string; date: string; score: number; maximum: number; rate: number | null; count: number
  status: string; states: Record<string, number>; attempts: (Answer & { 作答事件编号: string })[]
}
type Comparison = { direction: Direction; baseline: Measurement; observations: Measurement[] }
type Assignment = {
  draft_id: string; revision: number; active_snapshot_id: string; undated_nodes: string[]; results: Comparison[]
}
type Options = { directions: Direction[]; nodes: { id: string; date: string }[]; default_node: string; assignment?: Assignment }

function MeasurementSummary({ item }: { item: Measurement }) {
  const status: Record<string, string> = { uncovered: '本次测验未覆盖', missing: '无该生作答记录', unscored: '无有效评分' }
  return <div><strong>{item.node} · {item.date}</strong><div>{item.rate === null ? status[item.status]
    : `${item.score}/${item.maximum}（${item.rate.toFixed(1)}%，${item.count} 题）`}</div>
    {Object.entries(item.states).filter(([key]) => key !== '已评分').map(([key, count]) =>
      <Typography.Text key={key} type="secondary">{key} {count} 条 </Typography.Text>)}</div>
}

function DirectionComparison({ entry }: { entry: Comparison }) {
  const [selection, setSelection] = useState('')
  const { direction, baseline, observations } = entry
  const next = observations.find(item => item.node === selection)
    ?? [...observations].reverse().find(item => item.rate !== null) ?? observations.at(-1)
  const delta = next?.rate != null && baseline.rate != null ? Number((next.rate - baseline.rate).toFixed(1)) : undefined
  const same = next?.attempts.some(row => baseline.attempts.some(base => row.材料编号 === base.材料编号))
  return <Card size="small" className="spaced" title={direction.label}>
    <Typography.Text type="secondary">{direction.kind === 'indicator' ? '技能点 · 关联题目得分率' : '题型 · ' + direction.topics.join('、')} · 练习 {direction.questions.join('、')}</Typography.Text>
    {observations.length > 1 && <div className="spaced"><Select aria-label="后续测验" value={next?.node}
      onChange={setSelection} options={observations.map(item => ({ value: item.node, label: item.node + ' · ' + item.date }))} /></div>}
    <div className="follow-up-pair spaced"><div><small>基线</small><MeasurementSummary item={baseline} /></div>
      {next ? <div><small>后续测验</small><MeasurementSummary item={next} /></div> : <Typography.Text type="secondary">等待下次测验</Typography.Text>}</div>
    {delta !== undefined && <p>{delta === 0 ? '得分率持平' : `得分率${delta > 0 ? '上升' : '下降'} ${Math.abs(delta).toFixed(1)} 个百分点`}
      {' · '}{same ? '含同题复测' : '同方向不同题'}</p>}
    <details><summary>看前后作答</summary><div className="follow-up-pair">
      {[baseline, next].map((item, index) => <section key={index}><h4>{index ? '后续作答' : '基线作答'}</h4>
        {item?.attempts.length ? item.attempts.map(row => <AnswerEvidence key={row.作答事件编号} answer={row} />) : <p>暂无作答</p>}</section>)}
    </div></details>
  </Card>
}

function AssignmentResults({ item }: { item: Assignment }) {
  return <>
    {item.undated_nodes.length > 0 && <Alert type="info" title={'测验日期缺失或与基线相同：' + item.undated_nodes.join('、')} />}
    {item.results.map(entry => <DirectionComparison key={entry.direction.id} entry={entry} />)}
    <details className="spaced"><summary>查看结果快照</summary><p className="preserve">{item.active_snapshot_id}</p></details>
  </>
}

export function FollowUpEditor({ api, id, revision, approved, snapshot, onAssigned }: {
  api: Api; id: string; revision: number; approved: boolean; snapshot?: string; onAssigned: () => void
}) {
  const [data, setData] = useState<Options>()
  const [chosen, setChosen] = useState<string[]>([])
  const [node, setNode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const path = '/recommendations/' + encodeURIComponent(id) + '/follow-up'
  useEffect(() => {
    const controller = new AbortController()
    void api<Options>(path + '?revision=' + revision, { signal: controller.signal }).then(value => {
      setData(value)
      setNode(current => current || value.default_node)
    }).catch(err => { if (!controller.signal.aborted) setError(err.message) })
    return () => controller.abort()
  }, [api, path, revision, snapshot])
  async function assign() {
    setBusy(true)
    setError('')
    try {
      const assignment = await api<Assignment>(path, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ revision, baseline_node: node, directions: chosen }),
      })
      setData(current => current && { ...current, assignment })
      onAssigned()
    } catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }
  return <Card size="small" className="spaced" title="下次想观察什么" loading={!data && !error}>
    {error && <Alert type="error" title={error} />}
    {data?.assignment ? <AssignmentResults item={data.assignment} /> : data && <>
      {data.directions.length === 0 && <p>本版练习没有可跟踪的技能点或题型。</p>}
      <Checkbox.Group value={chosen} onChange={setChosen} className="wide">
        <Space orientation="vertical">{['indicator', 'form'].map(kind => {
          const options = data.directions.filter(item => item.kind === kind)
          return options.length > 0 && <section key={kind}><h4>{kind === 'indicator' ? '技能点' : '题型'}</h4>
            <Space wrap>{options.map(item => <Checkbox key={item.id} value={item.id}>
              {item.label} · {item.questions.join('、')}</Checkbox>)}</Space></section>
        })}</Space>
      </Checkbox.Group>
      {!!data.directions.length && <Space wrap className="spaced">
        <Select aria-label="基线测验" placeholder="选择基线测验" value={node || undefined} onChange={setNode}
          options={data.nodes.map(item => ({ value: item.id, label: item.id + ' · ' + item.date }))} />
        <Button disabled={!approved || !node || !chosen.length} loading={busy} onClick={assign}>标记已布置并开始跟踪</Button>
        {!data.nodes.length ? <Typography.Text type="secondary">缺少有日期的基线测验</Typography.Text>
          : !approved && <Typography.Text type="secondary">确认建议后可登记布置</Typography.Text>}
      </Space>}
    </>}
  </Card>
}

export function StudentFollowUps({ api, student, snapshot }: { api: Api; student: string; snapshot?: string }) {
  const [items, setItems] = useState<Assignment[]>()
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    void api<Assignment[]>('/students/' + encodeURIComponent(student) + '/follow-ups', { signal: controller.signal })
      .then(setItems).catch(err => { if (!controller.signal.aborted) setError(err.message) })
    return () => controller.abort()
  }, [api, student, snapshot])
  return <Card size="small" className="spaced" title="练后回看" loading={!items && !error}>
    {error && <Alert type="error" title={error} />}
    {items?.length === 0 && <Typography.Text type="secondary">尚未登记跟踪的学习建议。</Typography.Text>}
    {items?.map(item => <section key={item.draft_id + ':' + item.revision}>
      <h4>基线测验 {item.results[0]?.baseline.node} · 建议版本 {item.revision}</h4>
      <AssignmentResults item={item} />
    </section>)}
  </Card>
}
