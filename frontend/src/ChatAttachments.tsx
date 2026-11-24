import { useState } from 'react'
import { Alert, Button, Drawer, Input, Select, Space, Tag } from 'antd'
import { ActiveStatus } from './ActiveStatus'
import type { Api } from './ModelAccess'

type Inspection = { name: string; classification: string; reason: string; excerpt?: string }
export type Reference = { name: string; excerpt: string }
const names: Record<string,string> = { data:'学情数据', material:'教学资料', unknown:'需人工判断', reference:'本轮参考' }

export function ChatAttachments({api, close, useReferences, openRun, openImport, openMaterials, context}: {
  api: Api; close: () => void; useReferences: (rows: Reference[]) => void; openRun: (id: string) => void
  openImport: () => void; openMaterials: () => void; context: {student:string;group:string}
}) {
  const [mode, setMode] = useState('project')
  const [rows,setRows] = useState<{file:File; info:Inspection}[]>([])
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')
  const [continueKind,setContinueKind] = useState('')
  const [entity,setEntity] = useState('')
  const [done,setDone] = useState<string[]>([])
  const [dataRun,setDataRun] = useState('')
  const [mapping,setMapping] = useState<Record<string,string>>({})
  const [suggestion,setSuggestion] = useState<Record<string,string>>({})
  const [mappingTiming,setMappingTiming] = useState<{start:number;end?:number}>()
  const tables = rows.filter(r=>/\.(xlsx|csv)$/i.test(r.file.name))
  async function suggestMapping() {
    const start=Date.now(); setMappingTiming({start}); setError(''); setBusy(true)
    try {
      const form = new FormData(); tables.forEach(({file})=>form.append('files',file))
      form.append('mapping', JSON.stringify(mapping))
      const answer = await api<{mapping:Record<string,string>}>('/imports/suggest-mapping',{method:'POST',body:form})
      setSuggestion(answer.mapping)
      if (!Object.keys(answer.mapping).length) setError('模型没有找到明确的表头映射，请人工核对。')
    } catch (err) { setError((err as Error).message) }
    finally { setBusy(false); setMappingTiming({start,end:Date.now()}) }
  }
  const remaining = rows.filter(row => !done.includes(row.file.name))
  const data = remaining.filter(row => row.info.classification === 'data')
  const material = remaining.filter(row => row.info.classification === 'material')
  async function inspect(files: File[], applied: Record<string,string> = {}) {
    setMapping(applied); setSuggestion({})
    setError(''); setRows([]); setDone([]); setDataRun('')
    if (files.length > (mode === 'reference' ? 3 : 20) || new Set(files.map(f=>f.name)).size !== files.length) { setError('附件数量超出上限，或文件名重复。'); return }
    setBusy(true)
    try {
      const list = []
      for (const file of files) {
        const form = new FormData(); form.append('file',file); form.append('reference',String(mode === 'reference')); form.append('mapping',JSON.stringify(applied))
        let info: Inspection
        try { info = await api('/chat/attachments/inspect',{method:'POST',body:form}) }
        catch (err) { info = { name:file.name, classification:'unknown', reason:(err as Error).message } }
        list.push({file,info})
      }
      setRows(list)
    } finally { setBusy(false) }
  }
  async function submit() {
    setBusy(true); setError('')
    let run = dataRun
    try {
      if (data.length) {
        const form = new FormData(); data.forEach(({file}) => form.append('files',file)); form.append('mapping',JSON.stringify(mapping))
        if (continueKind) form.append('continue_target',continueKind + ':' + entity.trim())
        run = (await api('/imports',{method:'POST',body:form})).run_id
        setDataRun(run); setDone(old=>[...old,...data.map(r=>r.file.name)])
      }
      for (const {file} of material) {
        const form = new FormData(); form.append('file',file)
        await api('/materials',{method:'POST',body:form})
        setDone(old=>[...old,file.name])
      }
      close(); if (run) openRun(run); else openMaterials()
    } catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }
  const references = rows.flatMap(({info})=>info.excerpt ? [{name:info.name,excerpt:info.excerpt}] : [])
  return <Drawer open title="添加本机文件" onClose={busy ? undefined : close} closable={!busy} mask={{closable:!busy}}>
    <Space orientation="vertical" style={{width:'100%'}}>
      <Select aria-label="附件用途" value={mode} disabled={busy || !!done.length} onChange={value=>{setMode(value);setRows([]);setError('')}}
        options={[{value:'project',label:'导入项目（先分组预检）'},{value:'reference',label:'仅本轮参考（不入库）'}]} />
      <p>{mode === 'reference' ? '最多 3 个文件；仅发送界面显示的文字摘录给本轮模型。' : '只检查选中的文件。业务表与教材分开处理；无法识别的文件不会提交。'}</p>
      <input key={mode} type="file" multiple aria-label="选择本机文件" disabled={busy || !!done.length} onChange={e=>void inspect(Array.from(e.target.files || []))} />
      {busy && <ActiveStatus label="正在处理本批文件" />}
      {error && <Alert type="error" title={error} />}
      {rows.map(({info,file})=><section key={file.name} className="attachment-row"><Tag>{done.includes(file.name) ? '已提交' : names[info.classification]}</Tag><strong>{info.name}</strong><p>{info.reason}</p>
        {info.excerpt && <details><summary>查看将发送的摘录</summary><p className="preserve">{info.excerpt}</p></details>}</section>)}
      {mode === 'project' && !done.length && tables.some(r=>r.info.classification==='unknown' && /^(缺少必需列|未识别到)/.test(r.info.reason)) &&
        <Button disabled={busy} onClick={()=>void suggestMapping()}>用模型建议表头映射</Button>}
      {mappingTiming && (mappingTiming.end ? <small>列映射检查耗时 {((mappingTiming.end-mappingTiming.start)/1000).toFixed(1)} 秒</small>
        : <ActiveStatus label="等待表头映射" started={mappingTiming.start} />)}
      {!!Object.keys(suggestion).length && <Alert type="info" title="请核对表头映射，再重新预检"
        description={Object.entries(suggestion).map(([from,to])=>from+' → '+to).join('、')}
        action={<Button disabled={busy} onClick={()=>void inspect(rows.map(r=>r.file), {...mapping,...suggestion})}>应用并预检</Button>}/>}
      {mode === 'project' && !!data.length && <><Select aria-label="快照确认后继续" value={continueKind} disabled={busy} onChange={kind=>{setContinueKind(kind);setEntity(kind === 'student' ? context.student : kind === 'group' ? context.group : '')}}
        options={[{value:'',label:'只导入，暂不生成建议'},{value:'student',label:'快照确认后为指定学生生成建议'},{value:'group',label:'快照确认后为指定班级生成建议'}]} />
        {continueKind && <Input aria-label="学生或班级编号" value={entity} onChange={e=>setEntity(e.target.value)} placeholder={continueKind === 'student' ? '学生编号' : '班级编号'} />}</>}
      {mode === 'reference' ? <Button type="primary" disabled={busy || !references.length} onClick={()=>{useReferences(references);close()}}>添加到本轮</Button>
        : <Button type="primary" disabled={busy || (!data.length && !material.length) || (!!data.length && !!continueKind && !entity.trim())} onClick={()=>void submit()}>确认分组并提交已识别文件</Button>}
      {mode === 'project' && rows.some(r=>r.info.classification === 'unknown') && <Button disabled={busy} onClick={()=>{close();openImport()}}>去数据导入页手动处理表头</Button>}
      {dataRun && <Button disabled={busy} onClick={()=>{close();openRun(dataRun)}}>查看已提交的清洗任务</Button>}
    </Space>
  </Drawer>
}
