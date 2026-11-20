import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { Button, Drawer, Empty, Grid, Select, Segmented, Skeleton, Space } from 'antd'
import { ArrowRightOutlined, CloseOutlined, PlayCircleOutlined, UploadOutlined } from '@ant-design/icons'

export type DiagnosticAxis = {
  id: string
  label: string
  score: number | null
  n: number
  coverage: number
  population: number
  status: string
  question_ids?: string[]
}
type Group = { group: string; axes: DiagnosticAxis[] }
type Curriculum = { 指标编号: string; 主题编号: string; 主题?: string }
type Node = { 节点编号: string; 模拟测验日期?: string }
export type Profile = {
  groups: Group[]
  nodes: string[]
  node_details: Node[]
  curriculum: Curriculum[]
  snapshot_id: string | null
}
type Point = { student: string; score: number; n: number }
export type DiagnosticSelection = { group: string; node: string; indicator: string; snapshot_id: string }
type CellKey = Omit<DiagnosticSelection, 'snapshot_id'>
type Mode = '课程顺序' | '班级差异' | '覆盖情况'

export function Diagnostics({ data, count, attempts, active, busy, api, openReplay, openImport, openEvidence, openStudent, launchGroup }: {
  data?: Profile; count: number; attempts?: number; active: boolean; busy: boolean
  api: <T>(url: string, options?: RequestInit) => Promise<T>
  openReplay: () => void; openImport: () => void
  openEvidence: (target: DiagnosticSelection & { student: string; axis: DiagnosticAxis }) => void
  openStudent: (student: string, node: string, snapshot: string, indicator: string) => void
  launchGroup: (target: DiagnosticSelection) => void
}) {
  const [group, setGroup] = useState('')
  const [node, setNode] = useState('')
  const [mode, setMode] = useState<Mode>('课程顺序')
  const [cell, setCell] = useState<CellKey>()
  const [student, setStudent] = useState('')
  const [matrix, setMatrix] = useState<{ snapshot: string; profiles: Profile[] }>()
  const [distribution, setDistribution] = useState<{ key: string; points: Point[] }>()
  const [matrixError, setMatrixError] = useState('')
  const [pointError, setPointError] = useState('')
  const [reload, setReload] = useState(0)
  const snapshot = data?.snapshot_id
  const nodes = useMemo(() => {
    const details = data?.node_details || []
    return [...(data?.nodes || [])].sort((a, b) =>
      (details.find(item => item.节点编号 === a)?.模拟测验日期 || '').localeCompare(
        details.find(item => item.节点编号 === b)?.模拟测验日期 || '') || a.localeCompare(b))
  }, [data?.nodes, data?.node_details])
  const nodeKey = nodes.join(',')
  useEffect(() => {
    if (!active || !snapshot || !nodeKey) return
    const request = new AbortController()
    setMatrixError('')
    void Promise.all(nodeKey.split(',').map(node =>
      api<Profile>('/profiles?' + new URLSearchParams({ node, snapshot_id: snapshot }), { signal: request.signal })))
      .then(profiles => { if (!request.signal.aborted) setMatrix({ snapshot, profiles }) })
      .catch(error => { if (!request.signal.aborted) setMatrixError(error.message) })
    return () => request.abort()
  }, [api, active, snapshot, nodeKey, reload])

  const groups = (data?.groups || []).map(item => item.group)
  const visibleGroup = groups.includes(group) ? group : ''
  const visibleNode = nodes.includes(node) ? node : ''
  const visibleMode = visibleGroup && mode === '班级差异' ? '课程顺序' : mode
  const columns = nodes.filter(item => !visibleNode || item === visibleNode)
    .flatMap(node => groups.filter(item => !visibleGroup || item === visibleGroup).map(group => ({ node, group })))
  const currentMatrix = matrix?.snapshot === snapshot ? matrix : undefined
  const axesByColumn = useMemo(() => new Map(currentMatrix?.profiles.flatMap(
    (profile, index) => profile.groups.map(group => [JSON.stringify([nodes[index], group.group]), new Map(group.axes.map(axis => [axis.id, axis]))] as const))),
  [currentMatrix, nodes])
  const axisFor = ({ node, group }: {node: string; group: string}, indicator: string) =>
    axesByColumn.get(JSON.stringify([node, group]))?.get(indicator)
  const validCell = cell && columns.some(column => column.node === cell.node && column.group === cell.group) ? cell : undefined
  const selectedAxis = validCell && axisFor(validCell, validCell.indicator)
  const selectionKey = validCell && snapshot ? JSON.stringify([snapshot, validCell.group, validCell.node, validCell.indicator]) : ''
  useEffect(() => {
    if (!active || !selectionKey) return
    const request = new AbortController()
    const [snapshot_id, group, node, indicator] = JSON.parse(selectionKey) as string[]
    setPointError('')
    void api<{ students: Point[] }>('/profiles/distribution?' + new URLSearchParams({ snapshot_id, group, node, indicator }), { signal: request.signal })
      .then(result => { if (!request.signal.aborted) setDistribution({ key: selectionKey, points: result.students }) })
      .catch(error => { if (!request.signal.aborted) setPointError(error.message) })
    return () => request.abort()
  }, [api, active, selectionKey, reload])
  const points = distribution?.key === selectionKey ? distribution.points : undefined
  const selectedPoint = points?.find(point => point.student === student)
  const rows = [...(data?.curriculum || [])]
  function difference(id: string) {
    return Math.max(0, ...nodes.map(node => {
      const observed = groups.flatMap(group => {
        const score = axisFor({ node, group }, id)?.score
        return score == null ? [] : [score]
      })
      return (visibleNode && node !== visibleNode) || observed.length < 2 ? 0 : Math.max(...observed) - Math.min(...observed)
    }))
  }
  function coverage(id: string) {
    return Math.min(2, ...columns.flatMap(column => {
      const axis = axisFor(column, id)
      return !axis || axis.status === '未教学' ? [] : [axis.population ? axis.coverage / axis.population : 0]
    }))
  }
  if (visibleMode === '班级差异') rows.sort((a, b) => difference(b.指标编号) - difference(a.指标编号))
  if (visibleMode === '覆盖情况') rows.sort((a, b) => coverage(a.指标编号) - coverage(b.指标编号))
  const opener = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLElement>(null)
  const screens = Grid.useBreakpoint()
  const useDrawer = !!screens.lg && !screens.xl
  const recent = (point: CellKey) => {
    const previousNode = nodes[nodes.indexOf(point.node) - 1]
    return previousNode ? axisFor({ node: previousNode, group: point.group }, point.indicator) : undefined
  }
  const status = (point: CellKey, axis?: DiagnosticAxis) =>
    axis?.status === '未教学' && recent(point)?.question_ids?.length ? '本次未测量' : axis?.status || '无数据'
  function closePanel() {
    setCell(undefined)
    opener.current?.focus()
  }

  const detail = validCell && selectedAxis && <aside ref={panelRef} tabIndex={-1} className={'evidence-panel' + (useDrawer ? '' : ' diagnostic-panel')} aria-label="班级作答证据">
    {!useDrawer && <header className="diagnostic-toolbar"><span className="small-label">{validCell.group} · {validCell.node}</span>
      <Button type="text" icon={<CloseOutlined />} aria-label="关闭班级证据" onClick={closePanel} /></header>}
    <h2>{selectedAxis.label}</h2>
    <div className="indicator-kpis"><div><strong>{selectedAxis.score == null ? '—' : selectedAxis.score + '%'}</strong><small>得分率</small></div>
      <div><strong>{selectedAxis.coverage}/{selectedAxis.population}</strong><small>覆盖人数</small></div>
      <div><strong>{selectedAxis.n}</strong><small>有效作答</small></div></div>
    <NodeComparison cell={validCell} nodes={nodes} axisFor={axisFor} />
    <h3>学生分布</h3>
    {pointError ? <><p role="alert">{pointError}</p><Button onClick={() => setReload(value => value + 1)}>重新读取</Button></> :
      !points ? <Skeleton active paragraph={{ rows: 2 }} /> :
      points.length ? <StudentDots points={points} mean={selectedAxis.score} student={student} select={setStudent} /> :
      <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={status(validCell, selectedAxis)} />}
    {selectedPoint && snapshot && <div className="selected-student">
      <strong>{selectedPoint.student}</strong> · {selectedPoint.score}% · {selectedPoint.n} 条作答
      <Space wrap className="spaced">
        <Button type="primary" icon={<ArrowRightOutlined />} iconPlacement="end" onClick={() => openEvidence({ ...validCell, snapshot_id: snapshot, student, axis: selectedAxis })}>打开作答</Button>
        <Button onClick={() => openStudent(student, validCell.node, snapshot, validCell.indicator)}>学生画像</Button>
      </Space>
    </div>}
    {snapshot && !selectedPoint && selectedAxis.n > 0 && <Button loading={busy} className="spaced" onClick={() => launchGroup({ ...validCell, snapshot_id: snapshot })}>生成班级建议</Button>}
  </aside>

  return <div hidden={!active} className="diagnostics">
    <section className="diagnostics-top">
      <div><h1>班级诊断</h1><p>{count} 名学生 <span>·</span> {groups.length} 个班级 <span>·</span> {nodes.length} 次测验
        {attempts !== undefined && <> <span>·</span> {attempts} 条有效作答</>}
        {snapshot && <span className="snapshot-status">已确认快照</span>}</p></div>
      <Space wrap><Button icon={<UploadOutlined />} onClick={openImport}>导入学情</Button>
        <Button type="primary" loading={busy} icon={<PlayCircleOutlined />} onClick={openReplay}>进入案例串讲</Button></Space>
    </section>
    {!snapshot ? <div className="diagnostic-panel"><Empty description={data ? '导入作答并确认快照后查看班级诊断' : '正在读取班级诊断'} /></div> :
    <div className={'diagnostic-layout' + (selectedAxis && !useDrawer ? ' has-evidence' : '')}>
      <section className="diagnostic-panel matrix-panel" aria-label="课程指标矩阵">
        <header className="diagnostic-toolbar">
          <h2>课程指标矩阵</h2>
          <Space wrap>
            <Select aria-label="班级" value={visibleGroup} onChange={value => { setGroup(value); setStudent('') }}
              options={[{value: '', label: '全部班级'}, ...groups.map(value => ({value, label: value}))]} />
            <Select aria-label="测验范围" value={visibleNode} onChange={value => { setNode(value); setStudent('') }}
              options={[{value: '', label: '全部测验'}, ...nodes.map(value => ({value, label: value}))]} />
          </Space>
        </header>
        <Segmented<Mode> className="matrix-modes" aria-label="矩阵视图" value={visibleMode} onChange={setMode}
          options={visibleGroup ? ['课程顺序','覆盖情况'] : ['课程顺序','班级差异','覆盖情况']} />
        {matrixError ? <Empty description={matrixError}><Button onClick={() => setReload(value => value + 1)}>重新读取</Button></Empty>
          : !currentMatrix ? <Skeleton active /> : <div className="matrix-scroll">
            <table className="curriculum-matrix">
              <thead>
                <tr><th scope="col" rowSpan={2}>课程指标</th>{[...new Set(columns.map(column => column.node))].map(node =>
                  <th key={node} colSpan={columns.filter(column => column.node === node).length}
                    className="matrix-node" data-active={validCell?.node === node}>{node}</th>)}</tr>
                <tr>{columns.map(column => <th scope="col" key={column.node + column.group}
                  data-active={validCell?.node === column.node && validCell.group === column.group}>{column.group}</th>)}</tr>
              </thead>
              <tbody>{rows.map((row, index) => <tr key={row.指标编号} data-active={validCell?.indicator === row.指标编号}
                data-topic-start={index === 0 || row.主题编号 !== rows[index - 1].主题编号}>
                <th scope="row">{(index === 0 || row.主题编号 !== rows[index - 1].主题编号) && <small>{row.主题 || row.主题编号}</small>}
                  <span>{axisFor(columns[0], row.指标编号)?.label || row.指标编号}</span>
                  {visibleMode === '班级差异' && difference(row.指标编号) > 0 && <small>最大差 {difference(row.指标编号).toFixed(1)} 个百分点</small>}
                </th>
                {columns.map(column => {
                  const axis = axisFor(column, row.指标编号)
                  const previous = recent({ ...column, indicator: row.指标编号 })
                  const isSelected = validCell?.group === column.group && validCell.node === column.node && validCell.indicator === row.指标编号
                  const low = !!axis?.population && axis.coverage / axis.population < 0.5 && axis.status !== '未教学'
                  const showCoverage = visibleMode === '覆盖情况' && axis?.status !== '未教学'
                  const value = showCoverage && axis ? axis.coverage + '/' + axis.population : axis?.score == null ? status({ ...column, indicator: row.指标编号 }, axis) : axis.score + '%'
                  const strength = showCoverage && axis?.population ? 100 * axis.coverage / axis.population : axis?.score
                  return <td key={column.node + column.group}><button type="button" className="matrix-cell"
                    aria-label={column.group + ' ' + column.node + ' ' + (axis?.label || row.指标编号) + ' ' + value}
                    aria-pressed={isSelected} style={{ '--cell-strength': (strength ?? 0) + '%' } as CSSProperties}
                    data-scored={axis?.score != null} onFocus={event => {
                      const button = event.currentTarget
                      const scroller = button.closest('.matrix-scroll')
                      const rowLabel = button.closest('tr')?.querySelector('th')
                      if (scroller && rowLabel && button.getBoundingClientRect().left < rowLabel.getBoundingClientRect().right) {
                        scroller.scrollLeft -= rowLabel.getBoundingClientRect().right - button.getBoundingClientRect().left + 4
                      }
                    }} onClick={event => {
                      opener.current = event.currentTarget
                      setStudent('')
                      setCell({ ...column, indicator: row.指标编号 })
                      if (!screens.lg) requestAnimationFrame(() => panelRef.current?.focus())
                    }}>
                    <strong>{value}</strong>
                    {axis?.status !== '未教学' && <small>{previous?.status === '未教学' ? '本次开始测量' : low ? '低覆盖' : showCoverage ? (axis?.score == null ? '无有效评分' : axis.score + '%') : axis ? axis.coverage + '/' + axis.population : ''}</small>}
                  </button></td>
                })}
              </tr>)}</tbody>
            </table>
          </div>}
        <details className="matrix-definition"><summary>查看统计口径</summary>
          <p>班级得分率按学生得分率取平均。覆盖低于 50% 的格子标记低覆盖。跨测验点之间的差值为观测差。</p></details>
      </section>
      {useDrawer ? <Drawer open={active && !!detail} title={validCell ? validCell.group + ' · ' + validCell.node : '班级作答证据'}
        size={405} onClose={closePanel}>{detail}</Drawer> : detail}
    </div>}
  </div>
}

function NodeComparison({ cell, nodes, axisFor }: {
  cell: CellKey; nodes: string[]; axisFor: (column: {node: string; group: string}, indicator: string) => DiagnosticAxis | undefined
}) {
  const index = Math.max(nodes.indexOf(cell.node), 1)
  if (nodes.length < 2) return null
  const pair = nodes.slice(index - 1, index + 1).map(node => ({ node, axis: axisFor({ node, group: cell.group }, cell.indicator) }))
  const [first, last] = pair
  const delta = first.axis?.score == null || last.axis?.score == null ? null : last.axis.score - first.axis.score
  return <div className="node-comparison"><h3>测验观测</h3>
    {pair.map(({ node, axis }) => <div className="node-observation" key={node}>
      <span>{node}</span><div className="observation-track" aria-hidden="true">{axis?.score != null && <i style={{ left: axis.score + '%' }} />}</div>
      <strong>{axis?.score != null ? axis.score + '%' : node === last.node && axis?.status === '未教学'
        && first.axis?.question_ids?.length ? '本次未测量' : axis?.status || '—'}</strong>
    </div>)}
    {delta !== null && <p>观测差 {delta > 0 ? '+' : ''}{delta.toFixed(1)} 个百分点</p>}
    <details><summary>查看样本和题目范围</summary>{pair.map(({ node, axis }) =>
      <p key={node}>{node} · {axis?.coverage || 0}/{axis?.population || 0} 人 · {axis?.question_ids?.join('、') || '无可用题目'}</p>)}</details>
  </div>
}

function StudentDots({ points, mean, student, select }: { points: Point[]; mean: number | null; student: string; select: (student: string) => void }) {
  const stacks = new Map<number, number>()
  const dots = points.map(point => {
    const bucket = Math.round(point.score / 6)
    const level = stacks.get(bucket) || 0
    stacks.set(bucket, level + 1)
    return { ...point, level }
  })
  const height = Math.max(70, Math.max(...dots.map(dot => dot.level)) * 24 + 39)
  return <>
    <div className="point-plot" style={{ height }} aria-label="按学生得分率排列">
      {mean != null && <span className="plot-mean" style={{ left: mean + '%' }}><span>班均</span></span>}
      {dots.map(dot => <button key={dot.student} className="plot-point" aria-pressed={student === dot.student}
        aria-label={dot.student + ' · ' + dot.score + '% · ' + dot.n + ' 条作答'} title={dot.student + ' · ' + dot.score + '%'}
        style={{ left: dot.score + '%', bottom: dot.level * 24 + 4 }} onClick={() => select(dot.student)} />)}
    </div>
    <div className="plot-labels"><span>0%</span><span>50%</span><span>100%</span></div>
    <Select aria-label="按编号选择学生" showSearch value={student || undefined} placeholder="按编号选择学生"
      onChange={select} options={points.map(point => ({ value: point.student, label: point.student + ' · ' + point.score + '%' }))} />
  </>
}
