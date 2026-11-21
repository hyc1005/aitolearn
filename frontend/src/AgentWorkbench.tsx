import React from 'react'
import { Alert, Button, Card, Empty, Space, Steps, Table, Tag, Typography } from 'antd'
import { CleaningCounts, issueFilters } from './CleaningCounts'

type Row = Record<string, any>

export const runNames: Record<string, string> = {
  clean: '清洗学情', generate: '生成建议', recommendation_review: '修订后终审',
}
const stageNames: Record<string, string> = {
  received: '文件已接收',
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
  if (run.state === 'queued') return '排队'
  return stageNames[run.stage] || '处理中'
}

export function ReviewPanel({ review, openRef, canOpenRef, collapseDetails }: {
  review: Row; openRef?: (ref: string) => void; canOpenRef?: (ref: string) => boolean; collapseDetails?: boolean
}) {
  const details = <>
    <Typography.Paragraph className="spaced preserve">{review.summary}</Typography.Paragraph>
    {(review.findings || review.claims || []).map((finding: Row, index: number) =>
      <div key={index} className="spaced">
        <Typography.Paragraph>{finding.detail}</Typography.Paragraph>
        <Space wrap>{finding.refs.map((ref: string) => openRef && canOpenRef?.(ref)
          ? <Button key={ref} size="small" type="link" onClick={() => openRef(ref)}>{ref}</Button>
          : <Tag key={ref}>{ref}</Tag>)}</Space>
      </div>)}
  </>
  return <>
    <Space wrap>
      {review.model && <Tag>{review.model}</Tag>}
      {review.decision && <Tag color={['pass', 'ready'].includes(review.decision) ? 'success' : 'warning'}>{reviewNames[review.decision]}</Tag>}
      {Object.entries(review.checks || {}).map(([key, value]) =>
        <Tag key={key} color={value === 'pass' ? 'success' : 'warning'}>{checks[key]} · {value === 'pass' ? '通过' : '退回'}</Tag>)}
    </Space>
    {collapseDetails ? <details className="spaced"><summary>查看模型检查意见</summary>{details}</details> : details}
  </>
}

type Props = {
  jobs: Row[]; detail?: Row; runId: string; loading: boolean; error: string; legacy: boolean
  caseAvailable: boolean
  open: (key: string) => void; migrate: () => void; confirm: () => void; rebase: () => void
  retry: () => void; openDraft: (key: string) => void; openImport: () => void; openReplay: () => void
}

export default function AgentWorkbench(props: Props) {
  const [activeRef, setActiveRef] = React.useState('')
  const { detail: run, runId } = props
  const priority = (item: Row) => item.stage === 'confirm' && item.state === 'done' ? 0 :
    ['running', 'queued'].includes(item.state) ? 1 : item.state === 'failed' ? 2 : 3
  if (!runId) return <Card title="运行记录" extra={<Space wrap>
    {props.caseAvailable && <Button onClick={props.openReplay}>Q015 案例回放</Button>}
    {props.legacy && <Button onClick={props.migrate} loading={props.loading}>迁移历史批次</Button>}
    <Button type="primary" onClick={props.openImport}>导入学情</Button>
  </Space>}>
    <Table<Row> rowKey="id" dataSource={props.jobs.filter(job => runNames[job.kind]).sort((a, b) => priority(a) - priority(b))}
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
  const order = clean ? ['received', 'python', 'clean_review', 'confirm', 'published'] :
    run.kind === 'generate' ? ['snapshot', 'interpret', 'generate', 'final_review', 'confirm'] : ['final_review', 'confirm']
  const stages = run.stages || {}
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
      <Steps current={current} items={order.map(stage => ({
        title: clean && stage === 'published' ? '快照生效' : stageNames[stage],
        status: run.stage === stage && run.state === 'failed' ? 'error' :
          stage === 'received' || stages[stage] || run.stage === 'published' ? 'finish' : undefined,
      }))} />
      <Space wrap className="spaced">
        <Tag>{runStatus(run)}</Tag>
        {run.state === 'failed' && <Button onClick={props.retry} loading={props.loading}>重试当前阶段</Button>}
        {clean && outdated && !run.correction && <Button onClick={props.rebase} loading={props.loading}>按当前数据重跑</Button>}
        {clean && run.state === 'done' && run.stage === 'confirm' && !outdated &&
          <Button type="primary" onClick={props.confirm} loading={props.loading}>确认并启用快照</Button>}
        {draftId && <Button type={run.stage === 'confirm' ? 'primary' : 'default'} onClick={() => props.openDraft(draftId)}>打开建议与依据</Button>}
      </Space>
      {run.error && <Alert className="spaced" type="error" message={run.error} />}
      {outdated && <Alert className="spaced" type="warning" message={run.correction ? '基础快照已变更，请在当前报告中重新发起修订' : '基础快照已变更，请按当前数据重跑'} />}
      <details className="spaced"><summary>运行编号与版本</summary>
        <div className="preserve">{run.id}</div>
        {run.payload.snapshot_id && <div className="preserve">数据快照 {run.payload.snapshot_id}</div>}
        {!!run.payload.material_ids?.length && <div>资料版本 {run.payload.material_ids.join('、')}</div>}
      </details>
    </Card>
    {report && <Card title="Python 清洗结果" extra={<Space wrap><Tag>规则 {stages.python.rules_version}</Tag>
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
    {['clean_review', 'interpret', 'final_review'].map(stage => stages[stage] && <Card title={stageNames[stage]} key={stage}>
      <ReviewPanel review={stages[stage]} openRef={setActiveRef}
        collapseDetails={stage === 'clean_review' && stages[stage].decision === 'ready'}
        canOpenRef={ref => stage !== 'final_review' &&
          !!(run.linked_evidence?.[ref] || report?.issues.some((r: Row) => r.id === ref))} />
      {stage === 'clean_review' && selectedIssue && <Alert className="spaced" message={selectedIssue.id + ' · ' + selectedIssue.reason}
        description={[selectedIssue.source, selectedIssue.sheet, '第 ' + selectedIssue.line + ' 行', selectedIssue.field].filter(Boolean).join(' · ')} />}
      {stage === 'interpret' && selectedEvidence && <Card type="inner" className="spaced"
        title={selectedEvidence.材料编号 + ' · ' + selectedEvidence.得分 + '/' + selectedEvidence.满分}>
        <Typography.Paragraph>{selectedEvidence.题目.题干或内容}</Typography.Paragraph>
        <Typography.Paragraph className="preserve">{selectedEvidence.解答过程}</Typography.Paragraph>
        <Typography.Paragraph>最终作答 {selectedEvidence.学生最终作答} · {selectedEvidence.评分点得分}</Typography.Paragraph>
        <Typography.Text type="secondary">{selectedEvidence.来源.source} · {selectedEvidence.来源.sheet} · 第 {selectedEvidence.来源.line} 行</Typography.Text>
      </Card>}
      {stages[stage].trace && <details><summary>查看工具输出</summary><pre className="preserve">{JSON.stringify(stages[stage].trace, null, 2)}</pre></details>}
    </Card>)}
    {stages.generate && <Card title="生成草稿" extra={<Tag>{stages.generate.model}</Tag>}>
      <details><summary>查看工具调用</summary><pre className="preserve">{JSON.stringify(stages.generate.trace, null, 2)}</pre></details>
    </Card>}
    {!!run.failures?.length && <Card title="失败记录">{run.failures.map((entry: Row, index: number) =>
      <Typography.Paragraph key={index}>{new Date(entry.at * 1000).toLocaleString('zh-CN')} · {stageNames[entry.stage]} · {entry.error}</Typography.Paragraph>)}</Card>}
  </>
}
