// Run against a built local app. Install playwright-core separately; set PLAYWRIGHT_MODULE and BROWSER_PATH if needed.
const assert = require('node:assert/strict')
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core')
const url = process.env.APP_URL || 'http://127.0.0.1:8765'
const long = 'a-very-long-model-identifier-which-must-not-squeeze-the-send-button'
const fixed = { connection_id: 'local', model_id: long, effort: 'default' }
const model = id => ({ id, enabled: true, efforts: ['default', 'low', 'high'], verified: ['default', 'low', 'high'] })
const answer = '总体情况：覆盖 8 项指标。薄弱：方程 0 分（2 题）；中等：运算 50 分（覆盖 18/19 人）；较强：不等式 100 分（T03I1/T03I2 均 100 分）。样本有限（仅 2 题，样本有限）。'

async function check(browser, width) {
  const page = await browser.newPage({ viewport: { width, height: 900 } })
  let selection = fixed
  let turns = []
  await page.route('**/api/models/connections', route => route.fulfill({ json: {
    default: fixed, background: 'opencode', connections: [{ id: 'local', adapter: 'custom',
      name: 'Test', configured: true, catalog: [], models: [model(long), model('other')] }],
  } }))
  await page.route('**/api/learning-chat/stream', route => {
    const body = route.request().postDataJSON()
    selection = { connection_id: body.connection_id, model_id: body.model_id, effort: body.effort }
    const trace = [{tool:'read_profile', status:'ok'}, {tool:'read_profile', status:'failed'}]
    const turn = { question: body.question, answer, model_call: selection, trace }
    turns.push(turn)
    const events = [
      {type:'reasoning', round_id:'1.1', text:'测试思考'},
      {type:'tool_start', call_id:'1', tool:'read_profile'},
      {type:'tool_end', call_id:'1', ...trace[0]},
      {type:'reasoning', round_id:'2.1', text:'根据返回结果继续'},
      {type:'tool_start', call_id:'2', tool:'read_profile'},
      {type:'tool_end', call_id:'2', ...trace[1]},
      {type:'complete', result:{...turn, selection, session_id:'example', mode:'tools'}},
    ]
    return route.fulfill({ contentType:'text/event-stream', body:events.map(event => 'data: ' + JSON.stringify(event) + '\n\n').join('') })
  })
  await page.route('**/api/chat/sessions/example/selection', route => {
    selection = route.request().postDataJSON()
    return route.fulfill({ json: selection })
  })
  await page.route('**/api/chat/sessions/example', route => route.fulfill({ json: { id: 'example', title:'已重命名会话', selection, turns } }))
  await page.goto(url)
  const composer = page.locator('.chat-composer')
  const send = page.getByRole('button', { name: '发送', exact: true })
  const menu = page.getByRole('button', { name: /当前会话模型/ })
  const effort = page.getByRole('button', { name: /思考强度：/ })
  const input = page.getByRole('textbox', { name: '输入学情问题' })
  await menu.waitFor()
  await page.getByRole('heading', {name:'今天想了解什么？'}).waitFor()
  assert.equal(await page.locator('.chat-turn-markers').count(), 0, 'empty chat must not show turn markers')
  const initialBox = await composer.boundingBox()
  async function layout() {
    assert.equal(await composer.evaluate(el => getComputedStyle(el).borderTopWidth), '0px')
    assert.equal(await input.evaluate(el => getComputedStyle(el).borderTopWidth), '0px')
    assert.equal(await menu.evaluate(el => getComputedStyle(el).borderTopWidth), '0px')
    assert.equal(await effort.evaluate(el => getComputedStyle(el).borderTopWidth), '0px')
    const box = await send.boundingBox()
    assert.deepEqual([box.width, box.height], [44, 44])
    assert.ok(box.x >= 0 && box.x + box.width <= width)
    assert.ok(await send.evaluate(el => parseFloat(getComputedStyle(el).borderTopLeftRadius) >= 22))
  }
  await layout()
  if (width === 390) {
    assert.equal(await page.locator('.sidebar').getAttribute('aria-hidden'), 'true')
    await page.getByRole('button', {name:'展开导航'}).click()
    await page.locator('.brand-name:visible').waitFor()
    await page.setViewportSize({width, height:620})
    const keyboardBox = await composer.boundingBox()
    assert.ok(keyboardBox.y >= 0 && keyboardBox.y + keyboardBox.height <= 620, `composer stays visible when viewport shrinks: ${JSON.stringify(keyboardBox)}`)
    await page.setViewportSize({width, height:900})
    await page.getByRole('button', {name:'收起导航'}).click()
    await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') === '展开导航')
  }
  await menu.focus()
  await page.keyboard.press('ArrowDown')
  const first = page.getByRole('menuitemradio', { name: long })
  await first.waitFor()
  assert.equal(await first.getAttribute('aria-checked'), 'true')
  assert.ok(await first.evaluate(el => el === document.activeElement && getComputedStyle(el).outlineStyle !== 'none'))
  await page.keyboard.press('ArrowDown')
  assert.ok(await page.getByRole('menuitemradio', { name: 'other' }).evaluate(el => el === document.activeElement))
  await page.keyboard.press('Escape')
  assert.ok(await menu.evaluate(el => el === document.activeElement))
  await effort.press('ArrowUp')
  await page.getByRole('menuitemradio', { name: '高', exact: true }).waitFor()
  await page.keyboard.press('Enter')
  await input.fill('Test message')
  await send.click()
  await page.locator('.chat-reply-text').first().waitFor()
  assert.equal(await page.locator('.chat-answer-line').count(), 4)
  assert.deepEqual(await page.locator('.chat-answer-metadata').allTextContents(), ['（2 题）', '（覆盖 18/19 人）', '（T03I1/T03I2 均 100 分）'])
  assert.equal(await page.locator('.chat-answer-metadata').filter({hasText:'样本有限'}).count(), 0)
  assert.match(await page.locator('.chat-reply-text').innerText(), /0 分/)
  const threadWidth = await page.locator('.chat-thread').evaluate(el => el.getBoundingClientRect().width)
  const turnWidth = await page.locator('.chat-turn').first().evaluate(el => el.getBoundingClientRect().width)
  assert.ok(threadWidth >= Math.min(width - 100, 700), `conversation must have usable width: ${threadWidth}`)
  assert.ok(turnWidth >= threadWidth - 2, `turn must fill conversation width: ${turnWidth}/${threadWidth}`)
  assert.equal(await page.getByRole('heading', {name:'今天想了解什么？'}).count(), 0)
  assert.equal(await page.locator('.chat-turn-markers button').count(), 1)
  const afterBox = await composer.boundingBox()
  assert.ok(Math.abs(initialBox.y + initialBox.height - afterBox.y - afterBox.height) < 2, 'composer bottom must stay fixed')
  await page.getByRole('heading', {name:'Test message', exact:true}).waitFor()
  assert.equal(await page.getByText('教学工作台', {exact:true}).count(), 0)
  await page.getByText('执行工具 2 次', {exact:true}).click()
  assert.deepEqual(await page.locator('.chat-process-timeline li').allTextContents(),
    ['测试思考', '读取指标和覆盖人数 · 已完成', '根据返回结果继续', '读取指标和覆盖人数 · 任务失败'])
  assert.equal(selection.effort, 'high')
  await layout()
  await page.reload()
  await page.getByRole('button', { name: '思考强度：高', exact: true }).waitFor()
  await page.locator('.chat-reply-text').first().waitFor()
  assert.equal(await page.getByText('测试思考', {exact:true}).count(), 0, 'reasoning must not survive reload')
  await page.getByRole('heading', {name:'已重命名会话', exact:true}).waitFor()
  await page.getByText('执行工具 2 次', {exact:true}).click()
  await page.getByText('读取指标和覆盖人数 · 任务失败', {exact:true}).waitFor()
  await layout()
  await page.getByRole('button', {name:'新对话'}).click()
  await page.getByRole('heading', {name:'今天想了解什么？'}).waitFor()
  assert.equal(await page.locator('.chat-turn-markers').count(), 0, 'new chat must not show turn markers')
  await page.close()
  console.log(`Composer checks passed at ${width}px`)
}

// Optional end-to-end walkthrough against the staged demo data, without mocking any API.
async function demo(browser) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  await page.goto(url)
  const modelMenu = page.getByRole('button', { name: /当前会话模型/ })
  await modelMenu.press('Enter')
  await page.getByRole('menuitemradio', { checked: true }).click()
  const input = page.getByRole('textbox', { name: '输入学情问题' })
  await input.fill('D01 去括号有进步吗？')
  await input.press('Enter')
  await page.getByText(/\+50\.0 个百分点/).waitFor()
  for (const [node, event] of [['N01', 'E1'], ['N02', 'E2']]) {
    await page.getByRole('button', { name: `${node} · 原始作答` }).click()
    await page.getByText('查看完整作答与原始行', { exact: true }).click()
    await page.getByText(`事件 ${event} · 修订 ${event === 'E1' ? '2' : '1'}`).waitFor()
    await page.locator('.ant-drawer-close:visible').click()
  }
  await input.fill('教材怎么讲这个知识点？')
  await input.press('Enter')
  await page.getByRole('button', { name: '整式教材 · PDF 第 2 页' }).click()
  await page.locator('.ant-drawer-body:visible').getByText('去括号前先看括号前的符号。', { exact: true }).waitFor()
  await page.close()
  console.log('End-to-end demo passed: model menu → SQL comparison → two original responses → textbook source')
}

;(async () => {
  const browser = await chromium.launch({ executablePath: process.env.BROWSER_PATH, headless: true })
  try {
    for (const width of [1440, 390]) await check(browser, width)
    if (process.env.CHECK_DEMO === '1') await demo(browser)
  } finally { await browser.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })
