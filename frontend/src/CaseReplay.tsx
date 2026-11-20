import { useEffect, useRef, useState } from 'react'
import { Button, Empty, Select, Space, Tag } from 'antd'
import { LeftOutlined, PauseOutlined, PlayCircleOutlined, RightOutlined } from '@ant-design/icons'
import { ReviewPanel } from './AgentWorkbench'
import { AnswerEvidence, type Answer } from './AnswerEvidence'

type Citation = { id: string; title: string; locator: string }
type Exercise = { question_id: string; hints: string[]; purpose?: string; evidence_ids?: string[] }
type Suggestion = {
  revision: number
  student?: string
  node?: string
  approved?: boolean
  review_state?: string
  message: string
  exercises: Exercise[]
  citations?: Citation[]
  evidence?: (Omit<Answer, '题目'> & { id: string })[]
  questions?: Record<string, Answer['题目']>
}
export type CaseData = {
  snapshot_id: string | null
  indicator?: { 主题?: string; 观察指标?: string; 课标整理编号?: string; 教材正文页?: string } | null
  returned_question?: string | null
  comparisons?: Suggestion[]
  student_preview?: string | null
  steps: {
    title: string
    evidence?: Answer | null
    before?: Suggestion | null
    after?: Suggestion | null
    result?: { review: Parameters<typeof ReviewPanel>[0]['review']; confirmed: boolean; run_id: string } | null
  }[]
}

function ChangedText({ value, other }: { value: string; other: string }) {
  let start = 0
  let end = 0
  while (start < value.length && value[start] === other[start]) start++
  while (end < Math.min(value.length, other.length) - start && value.at(-end - 1) === other.at(-end - 1)) end++
  const stop = value.length - end
  return <>{value.slice(0, start)}<mark>{value.slice(start, stop)}</mark>{value.slice(stop)}</>
}

function RevisionComparison({ before, after, question }: { before: Suggestion; after: Suggestion; question?: string | null }) {
  const previous = new Map(before.exercises.map((item, index) => [item.question_id, { item, index }]))
  const current = new Map(after.exercises.map((item, index) => [item.question_id, { item, index }]))
  const ids = [...new Set([...current.keys(), ...previous.keys()])]
  const pairs = (oldText: string, newText: string, key: string | number) => <div className="revision-pair" key={key}>
    <div><span className="small-label">修订 {before.revision}</span><p>{oldText ? <ChangedText value={oldText} other={newText} /> : '本版无此条'}</p></div>
    <div><span className="small-label">修订 {after.revision}</span><p>{newText ? <ChangedText value={newText} other={oldText} /> : '本版已移除'}</p></div>
  </div>
  return <>
    {ids.map(id => {
      const oldItem = previous.get(id)
      const newItem = current.get(id)
      const oldHints = oldItem?.item.hints || []
      const newHints = newItem?.item.hints || []
      const changes = Array.from({ length: Math.max(oldHints.length, newHints.length) }, (_, index) => index)
        .filter(index => oldHints[index] !== newHints[index])
      if (!changes.length && oldItem?.index === newItem?.index) return null
      return <section key={id}>
        <h4>{id} {id === 'Q004' ? question : ''}</h4>
        {oldItem?.index !== newItem?.index && <Tag>{!oldItem ? '新增练习' : !newItem ? '移除练习' : '顺序 ' + (oldItem.index + 1) + ' → ' + (newItem.index + 1)}</Tag>}
        {changes.map(index => pairs(oldHints[index] || '', newHints[index] || '', index))}
      </section>
    })}
    {before.message !== after.message && <section><h4>给学生的话</h4>{pairs(before.message || '', after.message || '', 'message')}</section>}
  </>
}

const acts = ['课程要求', '作答差异', '初次终审', '修订与确认']

export function CaseReplay({ data, openCitation }: { data: CaseData; openCitation: (item: Citation) => void }) {
  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [comparison, setComparison] = useState('')
  const body = useRef<HTMLDivElement>(null)
  const last = acts.length - 1
  useEffect(() => { if (body.current) body.current.scrollTop = 0 }, [index, comparison])
  useEffect(() => {
    if (!playing || index >= last) return
    const timer = setTimeout(() => {
      setIndex(index + 1)
      if (index + 1 === last) setPlaying(false)
    }, 12000)
    return () => clearTimeout(timer)
  }, [playing, index, last])
  const jump = (to: number) => { setPlaying(false); setComparison(''); setIndex(to) }
  const pause = () => setPlaying(false)
  const answer = data.steps[0]?.evidence
  const before = data.steps[3]?.before || data.steps[1]?.before
  const after = data.steps[3]?.after
  const rejected = data.steps[2]?.result
  const reviewed = data.steps[4]?.result
  const alternate = data.comparisons?.find(item => item.student === comparison)
  const citations = (items?: Citation[]) => <Space wrap>{items?.map(item =>
    <Button key={item.id} size="small" onClick={() => { pause(); openCitation(item) }}>{item.title} · {item.locator}</Button>)}</Space>

  return <>
    <div className="replay-controls">
      <nav className="act-tabs" aria-label="串讲步骤">{acts.map((title, step) =>
        <button key={title} aria-current={!alternate && index === step ? 'step' : undefined} onClick={() => jump(step)}>
          <span>{String(step + 1).padStart(2, '0')}</span>{title}
        </button>)}</nav>
      <Space wrap className="spaced">
        <Button icon={<LeftOutlined />} disabled={index === 0 || !!alternate} onClick={() => jump(index - 1)}>上一步</Button>
        <Button icon={playing ? <PauseOutlined /> : <PlayCircleOutlined />} disabled={!!alternate} onClick={() => {
          if (playing) pause()
          else { if (index === last) setIndex(0); setPlaying(true) }
        }}>{playing ? '暂停' : '播放案例'}</Button>
        <Button type="primary" icon={<RightOutlined />} iconPlacement="end" disabled={index === last || !!alternate} onClick={() => jump(index + 1)}>下一步</Button>
        {!!data.comparisons?.length && <Select allowClear aria-label="比较另一名学生" placeholder="比较另一名学生"
          value={comparison || undefined} onChange={value => { pause(); setComparison(value || '') }} style={{ minWidth: 160 }}
          options={data.comparisons.map(item => ({ value: item.student, label: item.student + ' · ' + item.node }))} />}
      </Space>
    </div>
    <div className="replay-body case-stage" ref={body} key={alternate ? comparison : index}>
      {alternate ? <>
        <h2>{alternate.student} · {alternate.node}</h2>
        <Tag>{alternate.approved ? '教师已确认' : ({ pass: '通过终审 · 待确认', revise: '退回修订', pending: '待终审' } as Record<string, string>)[alternate.review_state || ''] || '历史记录'}</Tag>
        <div className="revision-pair spaced"><div><h4>D01 的练习顺序</h4>{after?.exercises.map(item => <p key={item.question_id}>{item.question_id} · {item.purpose}</p>)}</div>
          <div><h4>{alternate.student} 的练习顺序</h4>{alternate.exercises.map(item => <p key={item.question_id}>{item.question_id} · {item.purpose}</p>)}</div></div>
        {alternate.evidence?.map(row => <details key={row.id} className="spaced"><summary>查看 {row.材料编号} 作答</summary>
          <AnswerEvidence answer={{ ...row, 题目: alternate.questions?.[row.材料编号 || ''] || {} }} /></details>)}
        {citations(alternate.citations)}
      </> : <>
        {index === 0 && (answer ? <div className="course-stage">
          <span className="small-label">{data.indicator?.主题 || '七年级数学'} · {data.indicator?.课标整理编号}</span>
          <h2>{data.indicator?.观察指标 || '课程指标'}</h2>
          <div className="course-question">{answer.题目.题干或内容}</div>
          <p className="preserve">{answer.题目.评分点}</p>{citations(before?.citations)}
        </div> : <Empty description="缺少该快照中的 Q015 作答" />)}
        {index === 1 && (answer ? <AnswerEvidence answer={answer} onInspect={pause} /> : <Empty description="缺少该快照中的 Q015 作答" />)}
        {index === 2 && <>
          {before && <div className="revision-pair"><div><span className="small-label">Q004 题干</span><p>{data.returned_question || '题干未保存'}</p></div>
            <div><span className="small-label">初版提示</span><ol>{before.exercises.find(item => item.question_id === 'Q004')?.hints.map((hint, number) =>
              <li key={number}>{hint.split(/(去括号)/).map((fragment, index) => fragment === '去括号' ? <mark key={index}>{fragment}</mark> : fragment)}</li>)}</ol></div></div>}
          {rejected ? <div className="spaced"><ReviewPanel review={rejected.review} /></div> : <Empty description="缺少初次终审记录" />}
          {citations(before?.citations?.filter(item => item.locator === 'Q004'))}
        </>}
        {index === 3 && <>
          {before && after ? <RevisionComparison before={before} after={after} question={data.returned_question} /> : <Empty description="缺少建议修订记录" />}
          {reviewed && <div className="spaced"><ReviewPanel review={reviewed.review} />{reviewed.confirmed && <Tag color="success">教师已确认修订 {after?.revision}</Tag>}</div>}
          {data.student_preview && <details className="spaced" onToggle={event => { if (event.currentTarget.open) pause() }}>
            <summary>查看学生练习单</summary><p className="preserve">{data.student_preview}</p></details>}
          {citations(after?.citations)}
        </>}
      </>}
      {data.snapshot_id && <details className="spaced"><summary>数据快照</summary><code>{data.snapshot_id}</code></details>}
    </div>
  </>
}
