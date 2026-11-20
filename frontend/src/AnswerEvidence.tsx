import { useState } from 'react'
import { Button, Space } from 'antd'

export type Answer = {
  学生编号?: string
  节点编号?: string
  材料编号?: string
  得分: number | string
  满分: number | string
  解答过程?: string
  学生最终作答?: string
  评分点得分?: string
  题目: { 题干或内容?: string; 评分点?: string }
  来源: { source: string; sheet?: string; line: number }
}

export function AnswerEvidence({ answer, onInspect }: { answer: Answer; onInspect?: () => void }) {
  const [point, setPoint] = useState('')
  const process = answer.解答过程 || ''
  const result = answer.学生最终作答 || ''
  const score = new Map((answer.评分点得分 || '').split(/[；;、\n]/).map(entry => {
    const [id, value] = entry.split('=')
    return [id.trim(), value?.trim()] as const
  }))
  const points = (answer.题目.评分点 || '').split(/\r?\n/).map(line => line.trim().match(/^(\S+)\s+(.+?)\s+(\d+(?:\.\d+)?)\s*分$/)).filter(p => p !== null)
  const worked = process.match(/4a\s*[-−]\s*7b/g)
  const final = result.match(/^\s*(4a)(\s*\+\s*)(7b)\s*$/)
  const align = answer.材料编号 === 'Q015' && worked?.length === 1 && final
  const processFragment = align ? worked[0].match(/^(4a)(\s*[-−]\s*)(7b)$/) : null
  function select(id: string) {
    setPoint(current => current === id ? '' : id)
    onInspect?.()
  }
  return <article className="answer-evidence">
    <header className="answer-heading">
      <div><div className="small-label">{[answer.学生编号, answer.节点编号, answer.材料编号].filter(Boolean).join(' · ')}</div>
        <h3>{answer.题目.题干或内容 || '题干待补充'}</h3></div>
      <strong className="answer-score" aria-label={'得分 ' + answer.得分 + '/' + answer.满分}>{answer.得分}<small> / {answer.满分}</small></strong>
    </header>
    <div className="answer-grid">
      <section className="answer-paper">
        {processFragment && final ? <>
          <div className="formula-label">解题过程</div>
          <div className="formula-line">{processFragment[1]}<mark data-active={point === 'Q015P3'}>{processFragment[2]}</mark>{processFragment[3]}</div>
          <div className="formula-label">最终填写</div>
          <div className="formula-line">{final[1]}<mark data-active={point === 'Q015P3'}>{final[2]}</mark>{final[3]}</div>
          <Button size="small" onClick={() => select('Q015P3')} aria-pressed={point === 'Q015P3'}>对照最终填写</Button>
        </> : <><div className="formula-label">解题过程</div><p className="preserve">{process || '无过程记录'}</p>
          <div className="formula-label">最终填写</div><p className="preserve">{result || '无填写记录'}</p></>}
        <details className="spaced" open={point && point !== 'Q015P3' ? true : undefined}
          onToggle={event => { if (event.currentTarget.open) onInspect?.() }}>
          <summary>查看完整作答与原始行</summary>
          <p className="preserve">{process}</p><p>最终填写 · {result || '—'}</p>
          <small>{answer.来源.source} · {answer.来源.sheet} · 第 {answer.来源.line} 行</small>
        </details>
      </section>
      <section className="score-points" aria-label="评分点">
        <h4>评分依据</h4>
        {points.length ? points.map(([, id, label, maximum]) =>
          <button key={id} className="score-point" aria-pressed={point === id} onClick={() => select(id)}>
            <span>{label}<small>{id}</small></span><strong>{score.get(id) ?? '—'}/{maximum}</strong>
          </button>
        ) : <p className="preserve">{answer.评分点得分 || '无分项评分记录'}</p>}
        {point && <Space className="point-note">{point === 'Q015P3' && align ? '最终填写与解题过程的符号不同' : '请查看对应原始作答'}</Space>}
      </section>
    </div>
  </article>
}
