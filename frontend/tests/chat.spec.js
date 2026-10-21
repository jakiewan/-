import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.route('**/api/auth/me', route => route.fulfill({ json: { username: 'alice' } }))
  await page.route('**/api/chat/stream', route => route.fulfill({
    contentType: 'text/event-stream',
    body: [
      ['start', {}],
      ['node', { id: 'prepare-1', name: 'prepare_context', label: '准备本轮状态', status: 'running' }],
      ['node', { id: 'prepare-1', name: 'prepare_context', label: '准备本轮状态', status: 'done', elapsed_ms: 1 }],
      ['node', { id: 'intent-1', name: 'recognize_intent', label: 'BERT 意图识别', status: 'running' }],
      ['intent', { intent: '咨询', reason: '询问信息' }],
      ['node', { id: 'intent-1', name: 'recognize_intent', label: 'BERT 意图识别', status: 'done', elapsed_ms: 50 }],
      ['node', { id: 'retrieve-1', name: 'retrieve_knowledge', label: '知识检索（暂未接入）', status: 'running' }],
      ['node', { id: 'retrieve-1', name: 'retrieve_knowledge', label: '知识检索（暂未接入）', status: 'done', elapsed_ms: 1 }],
      ['node', { id: 'answer-1', name: 'generate_answer', label: '生成回复候选', status: 'running' }],
      ['node', { id: 'answer-1', name: 'generate_answer', label: '生成回复候选', status: 'done', elapsed_ms: 100 }],
      ['node', { id: 'validate-1', name: 'validate_answer', label: '校验回复', status: 'running' }],
      ['sources', { sources: [{ label: '资料来源', url: 'https://example.com/product' }] }],
      ['delta', { text: '测试客服' }],
      ['delta', { text: '回复' }],
      ['node', { id: 'validate-1', name: 'validate_answer', label: '校验回复', status: 'done', elapsed_ms: 1 }],
      ['progress', { step: 'save', label: '检查点已保存（非节点）', status: 'done' }],
      ['done', {}],
    ].map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join(''),
  }))
})

test('输入、空白校验、按钮和回车发送、接口结果展示', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveTitle('企业级电商智能客服')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('企业级电商智能客服')
  const input = page.getByRole('textbox', { name: '输入你的问题' })
  const send = page.getByRole('button', { name: '发送' })
  const messages = page.getByRole('article')

  await expect(messages).toHaveCount(1)
  await expect(send).toBeDisabled()
  await input.fill('   ')
  await input.press('Enter')
  await expect(messages).toHaveCount(1)
  await input.fill('  如何查询订单？  ')
  const reply = page.waitForResponse(response => response.url().endsWith('/api/chat/stream'))
  await send.click()
  const response = await reply
  expect(response.request().method()).toBe('POST')
  expect(response.request().postDataJSON()).toEqual({ message: '如何查询订单？' })
  expect(response.status()).toBe(200)
  expect(response.headers()['content-type']).toContain('text/event-stream')
  await expect(messages).toHaveCount(3)
  await expect(messages.nth(1)).toContainText('如何查询订单？')
  await expect(messages.nth(2)).toContainText('测试客服回复')
  await expect(messages.nth(2)).toContainText('识别意图：咨询')
  const trace = messages.nth(2).getByRole('list', { name: 'Graph 节点执行轨迹' })
  await expect(trace.locator('code')).toHaveText(['prepare_context', 'recognize_intent', 'retrieve_knowledge', 'generate_answer', 'validate_answer'])
  await expect(trace.getByRole('listitem')).toHaveCount(5)
  await expect(trace).toContainText('BERT 意图识别（recognize_intent）')
  await expect(trace).toContainText('知识检索（暂未接入）（retrieve_knowledge）')
  await expect(trace).toContainText('0.10 秒')
  await expect(trace).not.toContainText('select_business_route')
  await expect(messages.nth(2)).toContainText('检查点已保存（非节点） · 完成')
  await expect(messages.nth(2).getByRole('link', { name: '资料来源' })).toHaveAttribute('href', 'https://example.com/product')
  await expect(input).toHaveValue('')

  await input.fill('退货怎么办？')
  await input.press('Enter')
  await expect(messages).toHaveCount(5)

  await input.fill('中文输入中')
  await input.dispatchEvent('keydown', { key: 'Enter', isComposing: true })
  await expect(messages).toHaveCount(5)
  await page.reload()
  await expect(messages).toHaveCount(1)
})

test('手机宽度下页面不横向溢出，输入按文本显示', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto('/')
  await page.getByRole('textbox').fill('<img src=x onerror=alert(1)>')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.getByRole('article')).toHaveCount(3)
  await expect(page.locator('.user .bubble')).toHaveText('<img src=x onerror=alert(1)>')
  await expect(page.locator('.user img')).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

test('接口拒绝未登录请求，服务出错后可以再次提问', async ({ page, request }) => {
  const invalid = await request.post('/api/chat/stream', { data: { message: '   ' } })
  expect(invalid.status()).toBe(401)

  const fail = route => route.fulfill({ status: 503 })
  await page.route('**/api/chat/stream', fail)
  await page.goto('/')
  await page.getByRole('textbox').fill('你好')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.getByRole('article').last()).toContainText('暂时无法获取回复')

  await page.unroute('**/api/chat/stream', fail)
  await page.getByRole('textbox').fill('再次提问')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.getByRole('article').last()).toContainText('测试客服回复')
})
