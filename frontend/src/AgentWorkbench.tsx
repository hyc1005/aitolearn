import React from 'react'
import { Alert, Button, Card, Empty, Space, Steps, Table, Tag, Typography } from 'antd'
import { AnswerEvidence } from './AnswerEvidence'
import { CleaningCounts, issueFilters } from './CleaningCounts'
import { ActiveStatus } from './ActiveStatus'
import { DiagnosisBoard } from './DiagnosisBoard'
import type { Api } from './ModelAccess'

type Row = Record<string, any>

export const runNames: Record<string, string> = {
  extract: '解析资料', publish: '建立资料索引', clean: '清洗学情', generate: '生成建议', recommendation_review: '修订后终审',
}
const stageNames: Record<string, string> = {
  received: '文件已接收', extract:'解析资料', publish:'建立索引',
  python: 'Python 清洗', clean_review: '模型检查', snapshot: '读取快照', interpret: '学情解读',
  generate: '生成草稿', final_review: '模型终审', confirm: '待教师确认', published: '教师已确认',
  revision: '需要修订', superseded: '已有新修订',
}
const reviewNames: Record<string, string> = {
  ready: '可供教师确认', needs_revision: '需要修订数据', pass: '通过终审', revise: '退回修订',
}
const checks: Record<string, string> = {
  sample: '样本与作答', source: '教学原文', exercise: '练习与变式', student_text: '学生可见内容',
}

export function runStatus(run: Row) {
  if (run.state === 'failed') return '失败 · ' + (stageNames[run.stage] || '等待执行')
  if (run.state === 'stopped') return '已停止'
  if (run.stop_requested) return '将在当前阶段结束后停止'
  if (run.state === 'queued') return '排队'
  return stageNames[run.stage] || '处理中'
}

export function ReviewPanel({ review, openRef, canOpenRef, collapseDetails, indicators }: {
  indicators?: Record<string, Row>; review: Row; openRef?: (ref: string) => void; canOpenRef?: (ref: string) => boolean; collapseDetails?: boolean
}) {
  const details = <div className="review-sections">
    <section><h4>核对结果</h4><Typography.Paragraph className="preserve">{review.summary}</Typography.Paragraph></section>
    <section><h4>需核对的事项和依据</h4>
    {!(review.findings || review.claims || []).length && <p>没有登记需要核对的事项。</p>}
    {(review.findings || review.claims || []).map((finding: Row, index: number) =>
      <div key={index} className="spaced">
        {indicators?.[finding.refs[0]] && (() => {
          const item = indicators[finding.refs[0]]
          return <Space wrap><Typography.Text strong>{item.label}</Typography.Text>
            <Tag>覆盖 {item.coverage}/{item.population} 人</Tag><Tag>{item.n} 条有效作答</Tag></Space>
        })()}
        <Typography.Paragraph>{finding.detail}</Typography.Paragraph>
        <Space wrap>{finding.refs.map((ref: string) => openRef && canOpenRef?.(ref)
          ? <Button key={ref} size="small" type="link" onClick={() => openRef(ref)}>{ref}</Button>
          : <Tag key={ref}>{ref}</Tag>)}</Space>
      </div>)}</section>
    <section><h4>下一步</h4><p>{['needs_revision','revise'].includes(review.decision) ? '按上述依据修订后重新检查。' : '核对以上依据，按当前阶段提示继续。'}</p></section>
  </div>
  return <>
    <Space wrap><strong>结论</strong>
      {review.model && <Tag>{review.model}</Tag>}
      {review.decision && <Tag color={['pass', 'ready'].includes(review.decision) ? 'success' : 'warning'}>{reviewNames[review.decision]}</Tag>}
      {Object.entries(review.checks || {}).map(([key, value]) =>
        <Tag key={key} color={value === 'pass' ? 'success' : 'warning'}>{checks[key]} · {value === 'pass' ? '通过' : '退回'}</Tag>)}
    </Space>
    {collapseDetails ? <details className="spaced"><summary>查看模型检查意见</summary>{details}</details> : details}
  </>
}

type Props = {
  api: Api; openSettings: () => void; openMaterials: () => void
  jobs: Row[]; detail?: Row; runId: string; loading: boolean; error: string; legacy: boolean
  caseAvailable: boolean
  open: (key: string) => void; migrate: () => void; confirm: () => void; rebase: () => void
  retry: () => void; control: (action: string) => void; openDraft: (key: string) => void; openImport: () => void; openReplay: () => void
}

export default function AgentWorkbench(props: Props) {
  const [activeRef, setActiveRef] = React.useState('')
  const [archived, setArchived] = React.useState(false)
  const [selectedStage, setSelectedStage] = React.useState<string>()
  const { detail: run, runId } = props
  const priority = (item: Row) => item.stage === 'confirm' && item.state === 'done' ? 0 :
    ['running', 'queued'].includes(item.state) ? 1 : item.state === 'failed' ? 2 : 3
  if (!runId) return <Card title="运行记录" extra={<Space wrap>
    {props.caseAvailable && <Button onClick={props.openReplay}>Q015 案例回放</Button>}
    {props.legacy && <Button onClick={props.migrate} loading={props.loading}>迁移历史批次</Button>}
    <Button onClick={() => setArchived(!archived)}>{archived ? "查看未归档" : "查看已归档"}</Button>
    <Button type="primary" onClick={props.openImport}>导入学情</Button>
  </Space>}>
    <Table<Row> rowKey="id" dataSource={props.jobs.filter(job => runNames[job.kind] && !!job.archived === archived).sort((a, b) => priority(a) - priority(b))}
      locale={{ emptyText: <Empty description="导入数据或生成建议后，这里会显示每个阶段。" /> }}
      columns={[
        { title: '任务', render: (_, row) => runNames[row.kind] },
        { title: '对象', render: (_, row) => row.payload.label || row.payload.student || row.payload.group },
        { title: '阶段', render: (_, row) => <Tag color={row.state === 'failed' ? 'error' : row.stage === 'confirm' ? 'warning' : 'processing'}>{runStatus(row)}</Tag> },
        { title: '创建时间', render: (_, row) => new Date(row.created * 1000).toLocaleString('zh-CN') },
        { title: '', render: (_, row) => <Button onClick={() => props.open(row.id)}>{row.stage === 'confirm' ? '核对并确认' : '查看运行'}</Button> },
      ]} />
  </Card>

  if (!run) return <Card loading={!props.error} extra={<Button onClick={() => props.open('')}>返回运行记录</Button>}>
    {props.error && <Alert type="error" message={props.error} />}
  </Card>
  const clean = run.kind === 'clean'
  const order = ['extract','publish'].includes(run.kind) ? [run.kind] : clean ? ['received', 'python', 'clean_review', 'confirm', 'published'] :
    run.kind === 'generate' ? ['snapshot', 'interpret', 'generate', 'final_review', 'confirm'] : ['final_review', 'confirm']
  const stages = run.stages || {}
  const shown = selectedStage || (['revision','superseded'].includes(run.stage) ? (clean ? 'clean_review' : 'final_review') : run.stage) || order[0]
  const current = ['revision', 'superseded'].includes(run.stage) ? order.indexOf(clean ? 'clean_review' : 'final_review') :
    run.stage === 'published' ? order.length - 1 : Math.max(0, order.indexOf(run.stage))
  const draftId = run.result?.draft || stages.generate?.draft_id || run.payload.draft
  const report = run.report
  const outdated = run.outdated && run.stage !== 'published'
  const selectedEvidence = run.linked_evidence?.[activeRef]
  const selectedIssue = report?.issues.find((issue: Row) => issue.id === activeRef)
  return <>
    <Card title={runNames[run.kind] + ' · ' + (run.payload.label || run.payload.student || run.payload.group || '')}
      extra={<Space wrap>{props.caseAvailable && <Button onClick={props.openReplay}>Q015 案例回放</Button>}
        <Button onClick={() => props.open('')}>返回运行记录</Button></Space>}>
      <Steps className={run.state === 'running' ? 'active-run-steps' : undefined} current={current} items={order.map(stage => ({
        title: clean && stage === 'published' ? '快照生效' : stageNames[stage],
        status: run.stage === stage && run.state === 'failed' ? 'error' :
          stage === 'received' || stages[stage] || run.stage === 'published' ? 'finish' : undefined,
      }))} />
      <p>下一步：{run.state === 'failed' ? '查看失败诊断并重试当前阶段' : run.stage === 'confirm' ? '核对结果后由教师确认' : run.stage === 'revision' ? '按复核意见修订' : run.state === 'done' ? '查看结果' : '等待当前任务完成'}</p>
      <Space wrap className="spaced">
        {run.state === 'running' ? <ActiveStatus started={run.stage_timings?.[run.stage]?.started_at ? run.stage_timings[run.stage].started_at * 1000 : undefined} label={runStatus(run) + (run.model_retrying ? ' · 正在重试' : '')} /> : <Tag>{runStatus(run)}</Tag>}
        {run.state === 'failed' && ['credentials', 'configuration', 'quota'].includes(run.model_error) &&
          <Button onClick={props.openSettings}>检查模型设置</Button>}
        {['queued', 'running'].includes(run.state) && <Button disabled={run.stop_requested} loading={props.loading}
          onClick={() => props.control('stop')}>{run.state === 'queued' ? '取消排队' : '完成当前阶段后停止'}</Button>}
        {['failed', 'stopped'].includes(run.state) && <Button onClick={props.retry} loading={props.loading}>
          {run.state === 'stopped' ? '继续任务' : '重试当前阶段'}</Button>}
        {['done', 'failed', 'stopped'].includes(run.state) && <Button loading={props.loading}
          onClick={() => props.control(run.archived ? 'unarchive' : 'archive')}>{run.archived ? '取消归档' : '归档'}</Button>}
        {clean && outdated && !run.correction && <Button onClick={props.rebase} loading={props.loading}>按当前数据重跑</Button>}
        {clean && run.state === 'done' && run.stage === 'confirm' && !outdated &&
          <Button type="primary" onClick={props.confirm} loading={props.loading}>确认并启用快照</Button>}
        {draftId && <Button type={run.stage === 'confirm' ? 'primary' : 'default'} onClick={() => props.openDraft(draftId)}>打开建议与依据</Button>}
      </Space>
      {run.error && <Alert className="spaced" type="error" message={run.error}
        description={run.stage === 'clean_review' && stages.python ? 'Python 清洗已完成，可只重试模型检查，无需重新上传。' : undefined} />}
      {outdated && <Alert className="spaced" type="warning" message={run.correction ? '基础快照已变更，请在当前报告中重新发起修订' : '基础快照已变更，请按当前数据重跑'} />}
      <details className="spaced"><summary>运行编号与版本</summary>
        <div className="preserve">{run.id}</div>
        {run.payload.snapshot_id && <div className="preserve">数据快照 {run.payload.snapshot_id}</div>}
        {!!run.payload.material_ids?.length && <div>资料版本 {run.payload.material_ids.join('、')}</div>}
      </details>
    </Card>
    <nav className="task-stage-nav" aria-label="任务阶段">{order.map(stage =>
      <Button key={stage} aria-pressed={shown === stage} type={shown === stage ? 'primary' : 'default'} onClick={() => { setSelectedStage(stage); setActiveRef('') }}>
        {stageNames[stage]}{run.stage_timings?.[stage]?.elapsed_ms != null && ' · ' + (run.stage_timings[stage].elapsed_ms / 1000).toFixed(1) + ' 秒'}
      </Button>)}</nav>
    {run.stage_timings?.[shown]?.model_requests > 0 && <small>本阶段模型请求 {run.stage_timings[shown].model_requests} 次
      {run.stage_timings[shown].retries ? ' · 传输重试 ' + run.stage_timings[shown].retries + ' 次' : ''}</small>}
    {shown === 'snapshot' && <Card title="核对范围"><p>{run.payload.student || run.payload.group} · {run.payload.node || '全部测验'}</p>
      <p>快照：{run.payload.snapshot_id}</p><p>固定资料版本：{run.payload.material_ids?.length || 0} 份</p></Card>}
    {shown === 'received' && <Card title="附件已接收"><p>{run.payload.label}</p><p>将在数据清洗与模型检查后等待确认。</p></Card>}
    {shown === 'published' && run.result?.recommendation_run_id && <Card title="后续建议"><Button onClick={() => props.open(run.result.recommendation_run_id)}>查看自动续跑的建议任务</Button></Card>}
    {shown === 'published' && run.result?.continuation_error && <Alert type="warning" title={'未自动生成建议：' + run.result.continuation_error} />}
    {shown === 'confirm' && <Card title="教师确认"><p>{clean ? '核对清洗结果和复核意见后启用本次快照。' : '打开建议查看学生可见版本，核对无误后确认。'}</p></Card>}
    {['extract','publish'].includes(shown) && <Card title={stageNames[shown]}><p>{runStatus(run)}</p>{run.payload.material && <Button onClick={props.openMaterials}>打开课程与资料</Button>}</Card>}
    {((shown === 'python' || shown === 'published') && report) && <Card title="Python 清洗结果" extra={<Space wrap><Tag>规则 {stages.python.rules_version}</Tag>
      <Button href={'/api/imports/issues.csv?run_id=' + run.id}>下载异常 CSV</Button></Space>}>
      <CleaningCounts report={report} />
      <Table<Row> size="small" rowKey="id" dataSource={report.issues} columns={[
        { title: '编号', dataIndex: 'id' }, { title: '文件与原始行', render: (_, row) => [row.source, row.sheet, '第 ' + row.line + ' 行'].filter(Boolean).join(' · ') },
        { title: '字段', dataIndex: 'field' }, { title: '原值', render: (_, row) => typeof row.original === 'object' ? <details><summary>展开</summary><pre className="preserve">{JSON.stringify(row.original, null, 2)}</pre></details> : row.original },
        { title: '排除原因', dataIndex: 'reason', ...issueFilters(report.issues) },
      ]} />
      <details><summary>来源清单与快照编号</summary><pre className="preserve">
        {stages.python.snapshot_id + '\n' + JSON.stringify(run.manifest, null, 2)}
      </pre></details>
    </Card>}
    {shown === 'interpret' && run.kind === 'generate' && run.payload.snapshot_id && <Card title="学情解读">
      <DiagnosisBoard runId={runId} api={props.api} />
    </Card>}
    {['clean_review', 'interpret', 'final_review'].map(stage => shown === stage && stages[stage] && <Card
      title={stage === 'interpret' ? '模型解读与引用样例' : stageNames[stage]} key={stage}>
      {stage === 'clean_review' && report && Object.keys(report.issue_counts || {}).length > 0 &&
        <div className="spaced">{Object.entries(report.issue_counts).map(([label, count]) =>
          <div className="issue-frequency" key={label}><span>{label}</span>
            <meter aria-label={label} min={0} max={report.issues.length} value={Number(count)} />
            <strong>{String(count)} 条</strong></div>)}</div>}
      <ReviewPanel review={stages[stage]} openRef={setActiveRef}
        indicators={stage === 'interpret' ? run.interpret_indicators : undefined}
        collapseDetails={stage === 'clean_review' && stages[stage].decision === 'ready'}
        canOpenRef={ref => stage !== 'final_review' &&
          !!(run.linked_evidence?.[ref] || report?.issues.some((r: Row) => r.id === ref))} />
      {stage === 'clean_review' && selectedIssue && <Alert className="spaced" message={selectedIssue.id + ' · ' + selectedIssue.reason}
        description={[selectedIssue.source, selectedIssue.sheet, '第 ' + selectedIssue.line + ' 行', selectedIssue.field].filter(Boolean).join(' · ')} />}
      {stage === 'interpret' && selectedEvidence && <AnswerEvidence key={activeRef} answer={selectedEvidence as Parameters<typeof AnswerEvidence>[0]['answer']} />}
      {stages[stage].trace && <details><summary>查看工具输出</summary><pre className="preserve">{JSON.stringify(stages[stage].trace, null, 2)}</pre></details>}
    </Card>)}
    {shown === 'generate' && stages.generate && <Card title="生成草稿" extra={<Tag>{stages.generate.model}</Tag>}>
      {draftId && <Button onClick={() => props.openDraft(draftId)}>查看建议正文与引用</Button>}
      <details><summary>查看工具调用</summary><pre className="preserve">{JSON.stringify(stages.generate.trace, null, 2)}</pre></details>
    </Card>}
    {!!run.failures?.length && <details className="task-diagnostics"><summary>诊断记录 · {run.failures.length} 次失败</summary>{run.failures.map((entry: Row, index: number) =>
      <div key={index} className="spaced">
        <div>{new Date(entry.at * 1000).toLocaleString('zh-CN')} · {stageNames[entry.stage]} · {entry.error}</div>
        {entry.request && <Typography.Text type="secondary">
          {[entry.request.provider, entry.request.model, entry.request.status && 'HTTP ' + entry.request.status,
            entry.request.request_id && '请求 ' + entry.request.request_id, entry.request.bytes + ' 字节',
            entry.request.max_tokens && '输出上限 ' + entry.request.max_tokens, entry.request.finish_reason,
            '第 ' + (entry.request.retry + 1) + ' 次请求'].filter(Boolean).join(' · ')}
        </Typography.Text>}
        {entry.detail && <p>{entry.detail}</p>}
      </div>)}</details>}
  </>
}
