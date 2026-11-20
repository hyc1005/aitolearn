import { useEffect, useRef, useState } from 'react'
import { Button, Card, Empty, Space, Tag, Typography } from 'antd'
import { ReviewPanel } from './AgentWorkbench'

type Citation = { id: string; title: string; locator: string }
type Suggestion = {
  revision: number
  observation: string
  message: string
  exercises: { question_id: string; hints: string[] }[]
  citations?: Citation[]
}
type Evidence = {
  得分: number; 满分: number; 解答过程: string; 学生最终作答: string
  题目: { 题干或内容?: string }; 来源: { source: string; sheet?: string; line: number }
}
export type CaseData = {
  snapshot_id: string | null
  steps: {
    title: string
    evidence?: Evidence | null
    before?: Suggestion | null
    after?: Suggestion | null
    result?: { review: Parameters<typeof ReviewPanel>[0]['review']; confirmed: boolean; run_id: string } | null
  }[]
}

export function CaseReplay({ data, openCitation }: { data: CaseData; openCitation: (item: Citation) => void }) {
  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(false)
  const body = useRef<HTMLDivElement>(null)
  const last = data.steps.length - 1
  useEffect(() => { if (body.current) body.current.scrollTop = 0 }, [index])
  useEffect(() => {
    if (!playing || index >= last) return
    const timer = setTimeout(() => {
      setIndex(index + 1)
      if (index + 1 === last) setPlaying(false)
    }, 12000)
    return () => clearTimeout(timer)
  }, [playing, index, last])
  const step = data.steps[index]
  const jump = (to: number) => { setPlaying(false); setIndex(to) }
  const suggestion = (draft: Suggestion | null, label: string) => <Card size="small" className="spaced" title={label}>
    {draft ? <>
      <Typography.Paragraph>{draft.observation}</Typography.Paragraph>
      {draft.exercises.map(item => <details key={item.question_id} onToggle={event => {
        if (event.currentTarget.open) setPlaying(false)
      }}><summary>{item.question_id} 练习提示</summary>
        <ol>{item.hints.map((hint, i) => <li key={i}>{hint}</li>)}</ol>
      </details>)}
      <Typography.Paragraph className="spaced">给学生的话 · {draft.message}</Typography.Paragraph>
      {draft.citations?.map(item => <Button key={item.id} type="link" onClick={() => {
        setPlaying(false)
        openCitation(item)
      }}>{item.title} · {item.locator}</Button>)}
    </> : <Empty description="缺少该版本的建议记录" />}
  </Card>
  return <>
    <Space wrap aria-label="回放步骤">
      {data.steps.map((item, to) => <Button key={item.title} size="small" type={index === to ? 'primary' : 'default'}
        aria-current={index === to ? 'step' : undefined} onClick={() => jump(to)}>{to + 1}. {item.title}</Button>)}
    </Space>
    <Space wrap className="spaced">
      <Button disabled={index === 0} onClick={() => jump(index - 1)}>上一步</Button>
      <Button onClick={() => {
        if (playing) setPlaying(false)
        else { if (index === last) setIndex(0); setPlaying(true) }
      }}>{playing ? '暂停' : '播放'}</Button>
      <Button disabled={index === last} onClick={() => jump(index + 1)}>下一步</Button>
    </Space>
    <div className="replay-body" ref={body}>
    <Card className="spaced" title={step.title} key={step.title}>
      {step.evidence !== undefined && (step.evidence ? <>
        <Tag>得分 {step.evidence.得分}/{step.evidence.满分}</Tag>
        <Typography.Paragraph className="spaced">{step.evidence.题目.题干或内容}</Typography.Paragraph>
        <details onToggle={event => { if (event.currentTarget.open) setPlaying(false) }}>
          <summary>查看原始作答</summary>
          <Typography.Paragraph className="preserve">{step.evidence.解答过程}</Typography.Paragraph>
          <Typography.Paragraph>最终作答 · {step.evidence.学生最终作答}</Typography.Paragraph>
          <Typography.Text type="secondary">{step.evidence.来源.source} · {step.evidence.来源.sheet} · 第 {step.evidence.来源.line} 行</Typography.Text>
        </details>
      </> : <Empty description="缺少该快照中的 Q015 作答" />)}
      {step.before !== undefined && suggestion(step.before, '修订 1')}
      {step.after !== undefined && suggestion(step.after, '修订 2')}
      {step.result !== undefined && (step.result ? <>
        <ReviewPanel review={step.result.review} />
        {step.result.confirmed && <Tag color="green">教师已确认</Tag>}
        <details><summary>运行编号</summary><code>{step.result.run_id}</code></details>
      </> : <Empty description="缺少本次终审记录" />)}
    </Card>
    {data.snapshot_id && <details className="spaced"><summary>数据快照</summary><code>{data.snapshot_id}</code></details>}
    </div>
  </>
}
