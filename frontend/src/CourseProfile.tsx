import React, { useEffect, useState } from 'react'
import { Alert, Button, Card, Input, Select, Space, Table, message } from 'antd'

type Row = Record<string, any>
type Props = { api: (path: string, options?: RequestInit) => Promise<any>; course: string; setCourse: (value: string) => void; student: string; setStudent: (value: string) => void }
export function CourseProfile({api, course, setCourse, student, setStudent}: Props) {
  const [courses, setCourses] = useState<Row[]>([])
  const [data, setData] = useState<Row>()
  const [error, setError] = useState('')
  const [advice, setAdvice] = useState<Row>()
  const [text, setText] = useState<Row>()
  const [busy, setBusy] = useState(false)
  const [className, setClassName] = useState('')
  const [activity, setActivity] = useState('')
  useEffect(() => { void api('/course-data/courses').then(setCourses).catch(e => setError(e.message)) }, [])
  useEffect(() => {
    setData(undefined)
    setAdvice(undefined); setText(undefined)
    setClassName(''); setActivity('')
    if (!course) return
    let current = true
    void api('/course-data/courses/' + encodeURIComponent(course) + '/profile' + (student ? '?student_id=' + encodeURIComponent(student) : ''))
      .then(result => { if (current) { setData(result); setError('') } }).catch(e => { if (current) setError(e.message) })
    return () => { current = false }
  }, [course, student])
  useEffect(() => {
    if (!course || !student) return
    let current = true
    void api('/course-data/courses/' + encodeURIComponent(course) + '/advice?student_id=' + encodeURIComponent(student)).then(items => {
      if (current && items.length) { setAdvice(items[0]); setText(items[0].text) }
    }).catch(e => { if (current) setError(e.message) })
    return () => { current = false }
  }, [course, student])
  async function action(fn: () => Promise<Row>) {
    setBusy(true)
    try { const result = await fn(); setAdvice(result); setText(result.text); setError('') }
    catch (e) { message.error((e as Error).message) }
    finally { setBusy(false) }
  }
  const json = (method: string, body: Row) => ({method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})
  return <Card title="课程学生记录" className="spaced">
    <Space wrap><Select aria-label="课程画像选择课程" placeholder="选择课程" allowClear value={course || undefined} style={{width:200}} options={courses.map(c => ({value:c.id,label:c.name}))} onChange={value => {setCourse(value || ''); setStudent('')}} />
      <Select aria-label="课程画像选择学生" placeholder="选择学生" allowClear showSearch optionFilterProp="label" value={student || undefined} style={{width:240}} options={(data?.students || []).map((s: Row) => ({value:s.id,label:[s.class_name,s.name,s.external_id].filter(Boolean).join(' · ')}))} onChange={value => setStudent(value || '')} /></Space>
    {error && <Alert className="spaced" type="error" title={error} />}
    {data && <Space wrap className="spaced"><Select aria-label="课程记录班级筛选" allowClear placeholder="全部班级" style={{width:160}} value={className || undefined} options={[...new Set<string>(data.records.map((r: Row) => r.class_name))].map(name => ({value:name,label:name}))} onChange={value => setClassName(value || '')} />
      <Select aria-label="课程记录活动筛选" allowClear placeholder="全部活动" style={{width:160}} value={activity || undefined} options={[...new Set<string>(data.records.map((r: Row) => r.activity).filter(Boolean))].map(name => ({value:name,label:name}))} onChange={value => setActivity(value || '')} /></Space>}
    {data && <Table<Row> className="spaced" rowKey="id" size="small" dataSource={data.records.filter((row: Row) => (!className || row.class_name === className) && (!activity || row.activity === activity))} scroll={{x:true}} columns={[
      {title:'班级',dataIndex:'class_name'}, {title:'学生',render:(_,r) => r.student_name || r.external_id}, {title:'活动',dataIndex:'activity'},
      {title:'类型',render:(_,r) => ({total:'总分',question:'题目',comment:'评价'} as Row)[r.kind] || r.kind}, {title:'题目',dataIndex:'question'},
      {title:'得分',dataIndex:'score',render:value => value ?? '—'}, {title:'满分',dataIndex:'maximum',render:value => value ?? '—'},
      {title:'评价',dataIndex:'comment'}, {title:'来源',render:(_,r) => [r.filename,r.sheet,'第 ' + r.line + ' 行'].filter(Boolean).join(' · ')}]} />}
    {course && student && <Card className="spaced" size="small" title="学习建议">
      <Button loading={busy} onClick={() => action(() => api('/course-data/courses/' + encodeURIComponent(course) + '/advice', json('POST', {student_id:student})))}>生成观察与建议</Button>
      {advice && text && <>
        <p>版本 {advice.revision} · 状态 {({draft:'待审核', reviewed:'终审通过', revision:'需修订', confirmed:'已确认'} as Row)[advice.state]}</p>
        {(['observation','suggestion','limitations'] as const).map((key, index) => <div key={key} className="spaced">
          <label>{['学情观察','学习建议','数据限制'][index]}<Input.TextArea aria-label={['学情观察','学习建议','数据限制'][index]} value={text[key]} disabled={advice.state === 'confirmed'} onChange={e => setText({...text, [key]:e.target.value})} autoSize={{minRows:2}} /></label>
        </div>)}
        {advice.review && <Alert type={advice.review.decision === 'pass' ? 'success' : 'warning'} title={'终审：' + advice.review.explanation} />}
        <Space wrap className="spaced">
          <Button loading={busy} disabled={advice.state === 'confirmed' || JSON.stringify(text) === JSON.stringify(advice.text)} onClick={() => action(() => api('/course-data/advice/' + advice.id, json('PUT', {...text, revision:advice.revision})))}>保存修改</Button>
          <Button loading={busy} disabled={advice.state === 'confirmed' || JSON.stringify(text) !== JSON.stringify(advice.text)} onClick={() => action(() => api('/course-data/advice/' + advice.id + '/review', json('POST', {revision:advice.revision})))}>提交终审</Button>
          <Button type="primary" loading={busy} disabled={advice.state !== 'reviewed' || JSON.stringify(text) !== JSON.stringify(advice.text)} onClick={() => action(() => api('/course-data/advice/' + advice.id + '/confirm', json('POST', {revision:advice.revision})))}>教师确认</Button>
        </Space>
      </>}
    </Card>}
  </Card>
}
