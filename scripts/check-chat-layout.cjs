// Run against a built local app. Install playwright-core separately; set PLAYWRIGHT_MODULE and BROWSER_PATH if needed.
const assert = require('node:assert/strict')
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core')
const url = process.env.APP_URL || 'http://127.0.0.1:8765'
const long = 'a-very-long-model-identifier-which-must-not-squeeze-the-send-button'
const fixed = { connection_id: 'local', model_id: long, effort: 'default' }
const model = id => ({ id, enabled: true, efforts: ['default', 'low', 'high'], verified: ['default', 'low', 'high'] })

async function check(browser, width) {
  const page = await browser.newPage({ viewport: { width, height: 900 } })
  let selection = fixed
  let turns = []
  await page.route('**/api/models/connections', route => route.fulfill({ json: {
    default: fixed, background: 'opencode', connections: [{ id: 'local', adapter: 'custom',
      name: 'Test', configured: true, catalog: [], models: [model(long), model('other')] }],
  } }))
  await page.route('**/api/learning-chat', route => {
    const body = route.request().postDataJSON()
    selection = { connection_id: body.connection_id, model_id: body.model_id, effort: body.effort }
    const turn = { question: body.question, answer: 'Recorded UI test answer', model_call: selection }
    turns.push(turn)
    return route.fulfill({ json: { ...turn, selection, session_id: 'example' } })
  })
  await page.route('**/api/chat/sessions/example/selection', route => {
    selection = route.request().postDataJSON()
    return route.fulfill({ json: selection })
  })
  await page.route('**/api/chat/sessions/example', route => route.fulfill({ json: { id: 'example', selection, turns } }))
  await page.goto(url)
  const composer = page.locator('.chat-composer')
  const send = page.getByRole('button', { name: '发送', exact: true })
  const menu = page.getByRole('button', { name: /当前会话模型/ })
  const effort = page.getByRole('button', { name: /思考强度：/ })
  const input = page.getByRole('textbox', { name: '输入学情问题' })
  await menu.waitFor()
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
  await page.getByText('Recorded UI test answer', { exact: true }).waitFor()
  assert.equal(selection.effort, 'high')
  await layout()
  await page.reload()
  await page.getByRole('button', { name: '思考强度：高', exact: true }).waitFor()
  await page.getByText('Recorded UI test answer', { exact: true }).waitFor()
  await layout()
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
