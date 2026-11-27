import React, { useEffect, useState } from 'react'
import { Alert, Button, Card, Input, Select, Space, Table, Typography, message } from 'antd'

type Row = Record<string, any>
const labels: Record<string, string> = { student_id: '学号', student_name: '姓名', score: '得分', maximum: '满分', question: '题号', activity: '活动', comment: '评价' }
async function api(path: string, options?: RequestInit) {
  const response = await fetch('/api/course-data' + path, options)
  const body = await response.json()
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '操作失败')
  return body
}
export function CourseImport() {
  const [courses, setCourses] = useState<Row[]>([]), [course, setCourse] = useState('')
  const [name, setName] = useState(''), [className, setClassName] = useState('')
  const [file, setFile] = useState<File>(), [preview, setPreview] = useState<Row>()
  const [sheets, setSheets] = useState<Row[]>([]), [choices, setChoices] = useState<Row>({})
  const [records, setRecords] = useState<Row[]>([]), [batches, setBatches] = useState<Row[]>([]), [busy, setBusy] = useState(false)
  async function run(fn: () => Promise<void>) { setBusy(true); try { await fn() } catch (error) { message.error((error as Error).message) } finally { setBusy(false) } }
  async function refresh() {
    setCourses(await api('/courses'))
    if (course) { setRecords(await api('/courses/' + course + '/records')); setBatches(await api('/courses/' + course + '/batches')) }
  }
  useEffect(() => { void refresh().catch(error => message.error(error.message)) }, [course])
  async function inspect(next?: Row[]) {
    if (!file) return
    if (!next) {
      const form = new FormData(); form.append('file', file)
      const result = await api('/inspect-workbook', {method:'POST', body:form})
      setSheets(result.sheets.map((sheet: Row) => ({...sheet, enabled:true, wide_columns:[], wide_maximums:{}, activity:''})))
      setPreview(undefined); setChoices({}); return
    }
    const config = {sheets: next.filter(sheet => sheet.enabled).map(({sheet, mapping, wide_columns, wide_maximums, activity}) => ({sheet, mapping, wide_columns, wide_maximums, activity}))}
    const form = new FormData(); form.append('file', file); form.append('config', JSON.stringify(config)); form.append('class_name', className)
    const result = await api('/preview-selection', {method:'POST', body:form})
    setPreview({...result, config, className, course}); setChoices({})
  }
  return <Card title="跨课程数据导入（初版）">
    <Alert type="info" showIcon title="课程数据" description="支持 CSV 和多工作表 XLSX，核对映射后可在课程画像与对话中使用。" />
    <Space wrap className="spaced">
      <Select aria-label="课程" placeholder="选择课程" value={course || undefined} style={{minWidth: 180}} options={courses.map(c => ({ value: c.id, label: c.name }))} onChange={setCourse} />
      <Input aria-label="新课程名称" placeholder="新课程名称" value={name} onChange={e => setName(e.target.value)} style={{width: 160}} />
      <Button loading={busy} onClick={() => run(async () => {
        const created = await api('/courses', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name})})
        setName(''); setCourse(created.id); await refresh()
      })}>创建课程</Button>
      <Input aria-label="班级" placeholder="班级名称" value={className} onChange={e => setClassName(e.target.value)} style={{width:160}} />
    </Space>
    <div className="spaced"><input aria-label="选择学情表格" type="file" accept=".csv,.xlsx" onChange={e => {setFile(e.target.files?.[0]); setPreview(undefined); setSheets([])}} />
      <Button loading={busy} disabled={!file} onClick={() => run(() => inspect())}>预览</Button></div>
    {sheets.map((sheet, index) => <Card size="small" key={sheet.sheet} title={sheet.sheet || 'CSV'} className="spaced">
      <label><input type="checkbox" checked={sheet.enabled} onChange={e => setSheets(current => current.map((item, i) => i === index ? {...item, enabled:e.target.checked} : item))} /> 导入此表</label>
      {sheet.enabled && <><Space wrap>{Object.entries(labels).map(([key, label]) => <label key={key}>{label} <Select allowClear value={sheet.mapping[key]} style={{width:140}} placeholder="未映射" options={sheet.headers.filter(Boolean).map((h: string) => ({label:h,value:h}))} onChange={value => setSheets(current => current.map((item, i) => {if (i !== index) return item; const mapping={...item.mapping}; if (value) mapping[key]=value; else delete mapping[key]; return {...item, mapping}}))} /></label>)}</Space>
      <div className="spaced">横向题目列 <Select mode="multiple" aria-label={(sheet.sheet || 'CSV') + '横向题目列'} value={sheet.wide_columns} style={{minWidth:240}} options={sheet.headers.filter(Boolean).map((h: string) => ({label:h,value:h}))} onChange={value => setSheets(current => current.map((item, i) => i === index ? {...item, wide_columns:value, wide_maximums:Object.fromEntries(Object.entries(item.wide_maximums).filter(([key]) => value.includes(key)))} : item))} /></div>
      <Space wrap><Input placeholder="默认活动名称（可选）" value={sheet.activity} onChange={e => setSheets(current => current.map((item,i) => i === index ? {...item,activity:e.target.value} : item))} />
        {sheet.wide_columns.map((column: string) => <Input key={column} aria-label={column + '满分'} placeholder={column + '满分（可选）'} value={sheet.wide_maximums[column] || ''} onChange={e => setSheets(current => current.map((item,i) => i === index ? {...item,wide_maximums:{...item.wide_maximums,[column]:e.target.value}} : item))} />)}</Space>
      <Button loading={busy} onClick={() => run(async () => {const result = await api('/suggest-mapping', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({headers:sheet.headers})}); setSheets(current => current.map((item,i) => i === index ? {...item,mapping:result.mapping} : item))})}>模型建议列映射</Button></>}
    </Card>)}
    {!!sheets.length && <Button loading={busy} disabled={!course || !className.trim()} onClick={() => run(() => inspect(sheets))}>生成导入预览</Button>}
    {preview && <>
      {!!preview.missing.length && <Alert type="warning" title={'缺少映射：' + preview.missing.map((m: Row) => (m.sheet || 'CSV') + ' ' + m.fields.join('、')).join('；')} />}
      {!!preview.issues.length && <Alert type="error" title={'有 ' + preview.issues.length + ' 行异常'} description={preview.issues.slice(0,10).map((i: Row) => `${i.sheet || 'CSV'} 第 ${i.line} 行：${i.reason}`).join('；')} />}
      <Typography.Paragraph>可导入 {preview.valid_count} 条记录，预览前 100 条。</Typography.Paragraph>
      <Table<Row> size="small" rowKey={(_, i) => String(i)} dataSource={preview.rows} pagination={false} scroll={{x:true}} columns={[
        {title:'工作表',dataIndex:'sheet'}, {title:'行',dataIndex:'line'}, {title:'学号',dataIndex:'student_id'}, {title:'姓名',dataIndex:'student_name'}, {title:'题号',dataIndex:'question'}, {title:'得分',dataIndex:'score'}, {title:'评价',dataIndex:'comment'}]} />
      <Typography.Title level={5}>核对学生身份</Typography.Title>
      <Table<Row> rowKey="key" size="small" dataSource={preview.identities} columns={[
        {title:'工作表',dataIndex:'sheet'}, {title:'行',dataIndex:'line'}, {title:'学号',dataIndex:'student_id'}, {title:'姓名',dataIndex:'student_name'},
        {title:'身份',render: (_, row) => <Select aria-label={(row.sheet || 'CSV') + '第' + row.line + '行学生身份'} style={{minWidth:220}} value={choices[row.key]} placeholder={row.candidates.length ? '请选择或核对' : '默认新建'} options={[...row.candidates.map((candidate: Row) => ({value:candidate.id,label:(candidate.name || '未填写姓名') + ' · ' + (candidate.external_id || '无学号')})), {value:'new',label:'新建学生'}]} onChange={value => setChoices(current => ({...current,[row.key]:value}))} /> }]} />
      <Button className="spaced" type="primary" loading={busy} disabled={!course || preview.course !== course || preview.className !== className || !!preview.missing.length || !!preview.issues.length || !preview.valid_count || JSON.stringify(preview.config.sheets) !== JSON.stringify(sheets.filter(s => s.enabled).map(({sheet,mapping,wide_columns,wide_maximums,activity}) => ({sheet,mapping,wide_columns,wide_maximums,activity}))) || preview.identities.some((row: Row) => row.candidates.length && !choices[row.key])} onClick={() => run(async () => {
        if (!file) return
        const form = new FormData(); form.append('file', file); form.append('course_id', course); form.append('class_name', className); form.append('mapping', JSON.stringify(preview.config)); form.append('sha256', preview.sha256); form.append('identities', JSON.stringify(choices))
        const result = await api('/imports', {method:'POST',body:form}); message.success(`已导入 ${result.count} 条记录`); setPreview(undefined); await refresh()
      })}>确认导入</Button>
    </>}
    {course && <><Typography.Title level={5}>导入批次</Typography.Title><Table<Row> rowKey="id" size="small" dataSource={batches} pagination={false} columns={[
      {title:'文件',dataIndex:'filename'}, {title:'班级',dataIndex:'class_name'}, {title:'记录数',dataIndex:'count'}, {title:'状态',render: (_, r) => r.deleted ? '回收站' : '可用'},
      {title:'操作',render: (_, r) => <Button loading={busy} onClick={() => run(async () => {await api('/batches/' + r.id + '/' + (r.deleted ? 'restore' : 'trash'), {method:'POST'}); await refresh()})}>{r.deleted ? '恢复' : '移入回收站'}</Button>}]} />
      <Typography.Title level={5}>课程记录</Typography.Title><Table<Row> rowKey="id" size="small" dataSource={records} columns={[
        {title:'班级',dataIndex:'class_name'}, {title:'学号',dataIndex:'external_id'}, {title:'姓名',dataIndex:'student_name'}, {title:'活动',dataIndex:'activity'}, {title:'题号',dataIndex:'question'}, {title:'得分',dataIndex:'score'}, {title:'满分',dataIndex:'maximum'}, {title:'评价',dataIndex:'comment'}]} />
    </>}
  </Card>
}
