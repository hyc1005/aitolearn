// UI walkthrough with browser-only fixtures. No real model calls, imports or writes.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core')
const url = process.env.APP_URL || 'http://127.0.0.1:8765'
const out = process.env.CHAT_SCREENSHOTS
const report = { snapshot_id: 'demo', valid_scored: 36 }
const students = [{ 学生编号: '示例01', 模拟班级编号: '示例班' }]
const response = '本次测验建议优先复习分数运算。先核对通分，再验证约分。\n\n原始作答提供了直接依据，教材示例适合用作下一节课的复习材料。'
const citation = { id:'cite', title:'赛题题库与学习卡', locator:'第 2 页', text:'复习之前，先核对分母是否一致。', material:'demo' }

async function screenshot(page, name) {
  if (out) { fs.mkdirSync(out, { recursive: true }); await page.screenshot({ path:path.join(out, name + '.png') }) }
}

async function check(browser, width) {
  const page = await browser.newPage({ viewport:{ width, height:900 }, hasTouch:width < 800, reducedMotion:'reduce' })
  page.setDefaultTimeout(7000)
  const scope = { student:'示例01', group:'', node:'N01', snapshot_id:'demo' }
  const selection = { connection_id:'local', model_id:'example-model', effort:'default' }
  let currentReport = {}, jobs = [], sent = 0
  let release
  await page.route('**/api/imports/report', route => route.fulfill({ json:currentReport }))
  await page.route('**/api/jobs', route => route.fulfill({ json:jobs }))
  await page.route('**/api/profiles?*', route => route.fulfill({ json:{ snapshot_id:'demo', students, groups:[], nodes:['N01'] } }))
  await page.route('**/api/models/connections', route => route.fulfill({ json:{ default:selection, connections:[{
    id:'local', name:'模拟连接', adapter:'custom', configured:true, models:[{ id:selection.model_id, enabled:true, efforts:['default'], verified:['default'] }],
  }] } }))
  await page.route('**/api/learning-chat/stream', async route => {
    sent++
    const body = route.request().postDataJSON()
    assert.equal(body.student, '示例01')
    if (sent === 1) return route.fulfill({ status:503, json:{ detail:'模拟服务暂不可用' } })
    if (sent === 4) await new Promise(resolve => { release = resolve })
    const answer = sent === 2 ? response : Array(12).fill(response).join('\n\n')
    const result = { answer, context:scope, elapsed_ms:1800, model_call:selection,
      parts:[{quote:'通分后再进行计算。', source:{kind:'material', title:citation.title, locator:citation.locator, citation}}],
      trace:[{tool:'search_materials', status:'ok', count:1}], cards:sent === 2 ? [{
        kind:'profile', target:{kind:'student',id:'示例01'}, indicators:[0,25,50,75,100,null].map((score, i) => ({
          id:'axis'+i, label:'指标'+i, score, earned:score === 100 ? 10 : 0, possible:10,
          n:1, coverage:1, population:1, status:'暂无评分'
        }))
      }, {
        kind:'profile', target:{kind:'group',id:'示例班'}, indicators:[0,50,100,null].map((score, i) => ({
          id:'group'+i, label:'班级指标'+i, score, n:2, coverage:2, population:10, status:'暂无评分'
        }))
      }] : [] }
    return route.fulfill({contentType:'text/event-stream',body:'data: '+JSON.stringify({type:'complete',result})+'\n\n'})
  })
  try {
    await page.goto(url)
    await page.getByRole('heading',{name:'还没有可用的学情数据'}).waitFor()
    await page.getByRole('button',{name:'导入学情数据'}).waitFor()
    assert.equal(await page.getByRole('button',{name:'查看学生表现'}).count(),0)
    await screenshot(page, `after-empty-${width}`)

    currentReport = report
    jobs = [{ id:'d00d', kind:'clean', state:'done', stage:'confirm', created:1 }]
    await page.reload()
    await page.getByRole('heading',{name:'本批数据待教师确认'}).waitFor()
    await page.getByText(/仍基于当前生效的旧快照/).waitFor()
    await screenshot(page, `after-pending-${width}`)

    jobs = []
    await page.reload()
    await page.getByRole('button',{name:'查看学生表现'}).waitFor()
    assert.equal(await page.getByRole('region',{name:'学情数据状态'}).count(),0)
    await page.getByRole('button',{name:'当前会话模型：example-model'}).waitFor()
    const input = page.getByRole('textbox',{name:'输入学情问题'})
    const send = page.getByRole('button',{name:'发送',exact:true})
    await screenshot(page, `after-ready-${width}`)
    await page.getByRole('button',{name:'生成学习建议',exact:true}).click()
    await page.getByRole('dialog',{name:'新建学习建议'}).waitFor()
    await page.locator('.ant-drawer-close:visible').click()
    await page.getByRole('dialog',{name:'新建学习建议'}).waitFor({state:'hidden'})
    await page.getByRole('button',{name:'查看学生表现'}).click()
    await page.getByRole('combobox',{name:'选择上下文对象'}).fill('示例01')
    await page.locator('.ant-select-item-option-content:visible').filter({hasText:'示例01'}).click()
    assert.match(await input.inputValue(), /示例01/)
    assert.equal(sent,0)
    await send.click()
    await page.getByRole('alert').filter({hasText:'模拟服务暂不可用'}).waitFor()
    await page.getByRole('button',{name:/^重\s*试$/}).click()
    await page.getByText('本次测验建议优先复习分数运算。先核对通分，再验证约分。').waitFor()
    const dots = page.locator('.chat-card').filter({hasText:'学生 示例01'}).locator('.chat-indicator-dot')
    assert.deepEqual(await dots.evaluateAll(els => els.map(el => getComputedStyle(el).backgroundColor)), [
      'rgb(255, 0, 0)', 'rgb(255, 128, 0)', 'rgb(255, 255, 0)',
      'rgb(156, 255, 40)', 'rgb(56, 255, 79)', await dots.nth(5).evaluate(el => getComputedStyle(el).backgroundColor)
    ])
    assert.equal(await page.locator('.chat-score-legend').count(), 0)
    const groupDots = page.locator('.chat-card').filter({hasText:'班级 示例班'}).locator('.chat-indicator-dot')
    assert.deepEqual((await groupDots.evaluateAll(els => els.map(el => getComputedStyle(el).backgroundColor))).slice(0,3),
      ['rgb(255, 0, 0)', 'rgb(255, 255, 0)', 'rgb(56, 255, 79)'])
    await page.getByText(/100% · 当前满分/).waitFor()
    assert.equal(await page.getByText('暂无评分', {exact:true}).count(), 2)
    if (await page.getByRole('button',{name:'展开导航'}).isVisible()) await page.getByRole('button',{name:'展开导航'}).click()
    const nav = page.locator('.sidebar:visible')
    await nav.getByText('智慧学伴').waitFor()
    assert.equal(await nav.locator('.nav-item[aria-current="page"]').textContent(), '学情对话')
    assert.equal(await nav.locator('.nav-item').count(), 8)
    assert.ok(await nav.evaluate(el => el.clientHeight >= Math.min(window.innerHeight, 600)), 'sidebar should use the available viewport height')
    await page.waitForFunction(() => {
      const content = document.querySelector('.shell > .content').getBoundingClientRect()
      return innerWidth <= 800 ? Math.abs(content.left) < 2 : Math.abs(content.left - 240) < 2
    })
    assert.equal(await page.locator('.nav-backdrop').count(), width <= 800 ? 1 : 0)
    await screenshot(page, `navigation-open-${width}`)
    await nav.getByRole('button',{name:'收起导航'}).click()
    await page.getByRole('button',{name:'展开导航'}).waitFor()
    await page.waitForFunction(() => Math.abs(document.querySelector('.shell > .content').getBoundingClientRect().left) < 2)
    const source = page.getByRole('button',{name:citation.title + ' · ' + citation.locator})
    await source.click()
    await page.getByRole('dialog').getByText(citation.text).waitFor()
    await page.locator('.ant-drawer-close:visible').click()
    await page.getByRole('dialog').waitFor({state:'hidden'})
    await page.waitForFunction(() => document.activeElement?.textContent?.includes('赛题题库与学习卡'))
    await page.getByText('执行工具 1 次',{exact:true}).click()
    await page.getByText(/检索资料 · 1 条 · 已完成/).waitFor()
    await screenshot(page, `after-answer-${width}`)

    await input.fill('继续说明')
    await send.click()
    await page.locator('.chat-reply-text').nth(1).waitFor()
    await input.fill('再继续说明')
    await send.click()
    await page.getByText('正在回答',{exact:false}).first().waitFor()
    for (let i = 0; i < 100 && !release; i++) await new Promise(resolve => setTimeout(resolve, 10))
    assert.ok(release, 'follow-up request should be waiting')
    await page.evaluate(() => window.scrollTo(0, 0))
    const jump = page.getByRole('button',{name:'回到最新消息'})
    await jump.waitFor()
    release()
    await page.locator('.chat-reply-text').nth(2).waitFor()
    assert.ok(await page.evaluate(() => window.scrollY < 10), 'new reply must not steal scroll position')
    await jump.click()
    await jump.waitFor({ state:'hidden' })
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    await page.route('**/api/chat/attachments/submit', route => {
      assert.equal(route.request().method(), 'POST')
      assert.match(route.request().postData(), /filename="sample.pdf"/)
      return route.fulfill({json:{ session_id:'auto-session', question:'上传附件：sample.pdf', answer:'附件已进入后台处理',
        cards:[{kind:'material_extract',run_id:'abc',material_id:'def',name:'sample.pdf',state:'queued'}],
        trace:[{tool:'queue_material_extract',origin:'agent',status:'ok',count:1}] }})
    })
    await page.route('**/api/agent/runs/abc', route => route.fulfill({json:{state:'done',stage:'extract',result:{extraction:{text_pages:2,ocr_units:0}}}}))
    await page.getByRole('button',{name:'添加上下文'}).click()
    await page.getByRole('button',{name:'上传文件'}).click()
    await page.getByLabel('选择本机文件').setInputFiles({name:'sample.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.7')})
    await page.getByText('解析完成 · 等待教师校对 · 文字层 2 页 · OCR 0 处').waitFor()
    await page.getByRole('button',{name:'校对资料'}).waitFor()
    const uploaded = []
    await page.route('**/api/materials', route => {
      if (route.request().method() !== 'POST') return route.fulfill({ json:[] })
      const name = /filename="([^"]+)"/.exec(route.request().postData())?.[1]
      uploaded.push(name)
      return name === 'broken.pdf' ? route.fulfill({status:400,json:{detail:'模拟上传失败'}})
        : route.fulfill({json:{id:'material-' + uploaded.length}})
    })
    await page.getByRole('button',{name:'展开导航'}).click()
    await page.locator('.sidebar:visible').getByRole('button',{name:'课程与资料'}).click()
    await page.getByRole('heading',{name:'课程与资料'}).waitFor()
    const files = page.getByLabel('选择教材文件（可多选）')
    await files.setInputFiles(['first.pdf', 'broken.pdf', 'third.pdf'].map(name => ({ name, mimeType:'application/pdf', buffer:Buffer.from('%PDF-1.7') })))
    await page.getByText('2 份已提交解析，1 份未提交').waitFor()
    await page.getByText('broken.pdf：模拟上传失败').waitFor()
    assert.deepEqual(uploaded, ['first.pdf', 'broken.pdf', 'third.pdf'])
    console.log(`Chat workspace and material upload checks passed at ${width}px`)
  } finally { release?.(); await page.close() }
}

;(async () => {
  const browser = await chromium.launch({ executablePath:process.env.BROWSER_PATH })
  try { for (const width of [1440,390]) await check(browser,width) }
  finally { await browser.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })
