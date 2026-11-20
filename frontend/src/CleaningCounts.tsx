import { Descriptions } from 'antd'
import type { Key } from 'react'

type Report = {
  raw_attempts: number
  events: number
  valid_scored: number
  states: Record<string, number>
  issues: unknown[]
  input_attempts?: number
  duplicate_attempts?: number
}

export function issueFilters(issues: { reason: string }[]) {
  const counts = new Map<string, number>()
  for (const { reason } of issues) counts.set(reason, (counts.get(reason) ?? 0) + 1)
  return {
    filters: [...counts].map(([reason, count]) => ({ text: reason + '（' + count + '）', value: reason })),
    onFilter: (value: Key | boolean, row: Record<string, unknown>) => row.reason === value,
  }
}

export function CleaningCounts({ report }: { report: Report }) {
  return <>
    <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 3 }} items={[
      { key: 'raw', label: '原始作答', children: report.raw_attempts },
      { key: 'events', label: '归并事件', children: report.events },
      { key: 'scored', label: '已评分事件', children: report.states['已评分'] ?? 0 },
      { key: 'valid', label: '有效已评分事件', children: report.valid_scored },
      { key: 'issues', label: '异常明细', children: report.issues.length },
    ]} />
    <details className="spaced">
      <summary>查看计数口径</summary>
      <dl>
        <dt>原始作答</dt><dd>剔除内容完全相同的输入后保留的作答记录行数。
          {!!report.duplicate_attempts && ' 本批输入 ' + report.input_attempts + ' 行，其中完全重复 ' + report.duplicate_attempts + ' 行。'}</dd>
        <dt>归并事件</dt><dd>将同一作答事件的多条记录归并后的数量。</dd>
        <dt>已评分事件</dt><dd>归并事件中标记为已评分的数量。</dd>
        <dt>有效已评分事件</dt><dd>已评分事件中通过数据校验并计入画像的数量。</dd>
        <dt>异常明细</dt><dd>清洗报告记录的异常条数。</dd>
      </dl>
    </details>
  </>
}
