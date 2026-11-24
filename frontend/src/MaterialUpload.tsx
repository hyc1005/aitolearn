import { useState } from 'react'
import { Alert } from 'antd'
import { ActiveStatus } from './ActiveStatus'
import type { Api } from './ModelAccess'

type UploadResult = { name: string; error?: string }

export function MaterialUpload({ api, refresh }: { api: Api; refresh: () => Promise<void> }) {
  const [progress, setProgress] = useState<{ sent: number; total: number }>()
  const [results, setResults] = useState<UploadResult[]>([])
  const [refreshError, setRefreshError] = useState('')

  async function upload(files: File[]) {
    if (!files.length || progress) return
    const completed: UploadResult[] = []
    setResults([])
    setRefreshError('')
    setProgress({ sent: 0, total: files.length })
    try {
      for (const file of files) {
        const form = new FormData()
        form.append('file', file)
        try {
          await api('/materials', { method: 'POST', body: form })
          completed.push({ name: file.name })
        } catch (error) {
          completed.push({ name: file.name, error: (error as Error).message || '上传失败' })
        }
        setProgress({ sent: completed.length, total: files.length })
      }
      setResults(completed)
      if (completed.some(item => !item.error)) {
        try { await refresh() }
        catch { setRefreshError('文件已提交，列表暂未同步，请刷新页面，不必重复上传。') }
      }
    } finally {
      setProgress(undefined)
    }
  }

  const accepted = results.filter(item => !item.error).length
  return <div>
    <label htmlFor="teaching-material-files">选择教材文件（可多选）</label>
    <p><input id="teaching-material-files" type="file" multiple disabled={!!progress} accept=".pdf,.doc,.docx,.png,.jpeg,.jpg,.webp"
      onChange={event => {
        const files = Array.from(event.currentTarget.files || [])
        event.currentTarget.value = ''
        void upload(files)
      }} /></p>
    {progress && <ActiveStatus label={`正在提交教材 · 已处理 ${progress.sent}/${progress.total} 份`} />}
    {!!results.length && <Alert showIcon type={accepted === results.length ? 'success' : 'warning'}
      title={`${accepted} 份已提交解析${accepted < results.length ? `，${results.length - accepted} 份未提交` : ''}`}
      description={accepted < results.length ? <ul>{results.filter(item => item.error).map((item, index) =>
        <li key={index}>{item.name}：{item.error}</li>)}</ul> : '解析完成后仍需校对与发布。'} />}
    {refreshError && <Alert type="warning" title={refreshError} />}
  </div>
}
