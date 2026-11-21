import { useRef, useState, type FormEvent } from 'react'
import { Button, Drawer, Input, Select, Space } from 'antd'
import { ArrowUpOutlined, PlusOutlined } from '@ant-design/icons'
import { AnswerEvidence, type Answer } from './AnswerEvidence'
import type { Api, ModelAccess } from './ModelAccess'

type Citation = { id: string; title: string; locator: string; material: string; text: string; bbox?: number[] }
type Source = { title: string; locator: string } & (
  { kind: 'material'; citation: Citation } | { kind: 'answer'; attempt_id: string; answer: Answer }
)
type Result = { answer: string; parts: { quote: string; source: Source }[]; model: string }
type Turn = { question: string; result?: Result; error?: string }

export function LearningChat({ snapshot, students, nodes, access, api, openAccess, openCitation }: {
  snapshot?: string; students: { 学生编号: string; 模拟班级编号: string }[]; nodes: string[]
  access?: ModelAccess; api: Api; openAccess: () => void; openCitation: (source: Citation) => void
}) {
  const [student, setStudent] = useState('')
  const [node, setNode] = useState('')
  const [draft, setDraft] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [busy, setBusy] = useState(false)
  const [source, setSource] = useState<Answer>()
  const bottom = useRef<HTMLDivElement>(null)
  const provider = access?.providers.find(item => item.id === access.active)

  async function send(question: string, previous = turns) {
    question = question.trim()
    if (!question || (student && !snapshot) || !provider?.configured || busy) return
    setBusy(true)
    setDraft('')
    setTurns([...previous, { question }])
    requestAnimationFrame(() => bottom.current?.scrollIntoView({ block: 'nearest' }))
    try {
      const result = await api<Result>('/learning-chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question, snapshot_id: student ? snapshot : '', student, node: student ? node : '',
          history: previous.filter(turn => turn.result).slice(-5).map(turn => ({
            question: turn.question, answer: turn.result!.answer.slice(0, 1200),
          })),
        }),
      })
      setTurns([...previous, { question, result }])
    } catch (error) { setTurns([...previous, { question, error: (error as Error).message }]) }
    finally { setBusy(false) }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    void send(draft)
  }

  return <section className="learning-chat">
    <div className="chat-toolbar">
      <Space wrap>
        <Select className="chat-student" aria-label="对话学生（可选）" showSearch={{ optionFilterProp: 'label' }} value={student}
          disabled={busy || !snapshot} onChange={value => { setStudent(value); setNode(''); setTurns([]) }}
          options={[{ value: '', label: '添加学生（可选）' }, ...students.map(row => ({ value: row.学生编号, label: row.学生编号 + ' · ' + row.模拟班级编号 }))]} />
        <Select aria-label="对话测验" value={node} style={{ minWidth: 150 }} disabled={busy || !student}
          onChange={value => { setNode(value); setTurns([]) }}
          options={[{ value: '', label: '全部测验' }, ...nodes.map(value => ({ value, label: value }))]} />
      </Space>
      <Space wrap>
        <Button type="text" onClick={openAccess}>{provider ? provider.model + (provider.configured ? '' : ' · 待配置') : '模型接入'}</Button>
        <Button icon={<PlusOutlined />} disabled={busy || !turns.length} onClick={() => { setTurns([]); setDraft('') }}>新对话</Button>
      </Space>
    </div>
    {!turns.length && <div className="chat-empty">
      <h2>今天想聊什么？</h2>
      <p>{student ? '对话可按需查阅已发布资料和该学生的已评分作答。' : '可以直接提问，回答会在相关时引用已发布资料。'}</p>
      {provider && !provider.configured && <Button onClick={openAccess}>配置模型</Button>}
    </div>}
    <div className="chat-thread" aria-label="学情对话记录">
      {turns.map((turn, index) => <article key={index} className="chat-turn">
        <p className="chat-question">{turn.question}</p>
        {turn.error ? <div><p role="alert">{turn.error}</p>
          {index === turns.length - 1 && <Button disabled={busy} onClick={() => void send(turn.question, turns.slice(0, index))}>重试</Button>}</div>
          : turn.result ? <div>
            {turn.result.answer && <p className="preserve">{turn.result.answer}</p>}
            {turn.result.parts.map(({ quote, source }, id) =>
            <blockquote key={id}>
              <p className="preserve">{quote}</p>
              <Button type="link" onClick={() => source.kind === 'answer' ? setSource(source.answer) : openCitation(source.citation)}>
                {source.title} · {source.locator}
              </Button>
            </blockquote>)}
          </div> : <p role="status">正在回答…</p>}
      </article>)}
      <div ref={bottom} />
    </div>
    <form className="chat-composer" onSubmit={submit}>
      <Input.TextArea aria-label="输入学情问题" placeholder={student ? '问一个问题，或输入题号查看作答' : '问一个问题…'}
        maxLength={1200} autoSize={{ minRows: 2, maxRows: 5 }} value={draft} onChange={event => setDraft(event.target.value)}
        onPressEnter={event => {
          if (!event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(draft) }
        }} />
      <Button type="primary" htmlType="submit" loading={busy} icon={<ArrowUpOutlined />} disabled={!draft.trim() || (!!student && !snapshot) || !provider?.configured}>发送</Button>
    </form>
    {provider && <p className="chat-provider-note">当前问题、近期对话和相关原文片段由 {provider.name} 处理。</p>}
    <Drawer open={!!source} title="学生原始作答" size="min(850px, 100vw)" onClose={() => setSource(undefined)}>
      {source && <AnswerEvidence answer={source} />}
    </Drawer>
  </section>
}
