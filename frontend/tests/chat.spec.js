import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.route('**/api/conversation', route => route.fulfill({ json: {
    conversation: { id: '11111111-1111-4111-8111-111111111111', mode: 'ai', version: 0, event_seq: 0 }, messages: [],
  } }))
  await page.route('**/api/conversations/*/events?*', route => route.fulfill({ contentType: 'text/event-stream', body: ': heartbeat\n\n' }))
  await page.route('**/api/auth/me', route => route.fulfill({ json: { username: 'alice' } }))
  await page.route('**/api/chat/stream', route => route.fulfill({
    contentType: 'text/event-stream',
    body: [
      ['start', {}],
      ['node', { id: 'prepare-1', name: 'prepare_context', label: '准备本轮状态', status: 'running' }],
      ['node', { id: 'prepare-1', name: 'prepare_context', label: '准备本轮状态', status: 'done', elapsed_ms: 1 }],
      ['node', { id: 'intent-1', name: 'recognize_intent', label: 'LLM 意图识别', status: 'running' }],
      ['intent', { intent: '咨询', source: 'LLM', reason: '询问信息', context_used: false }],
      ['node', { id: 'intent-1', name: 'recognize_intent', label: 'LLM 意图识别', status: 'done', elapsed_ms: 50 }],
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
  await expect(page).toHaveTitle('服务通智能客服')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('服务通智能客服')
  const input = page.getByRole('textbox', { name: '输入你的问题' })
  const send = page.getByRole('button', { name: '发送' })
  const messages = page.getByRole('article')

  await expect(messages).toHaveCount(1)
  await expect(send).toBeDisabled()
  const commonQuestions = page.getByRole('list', { name: '常见问题', exact: true })
  await expect(commonQuestions).toBeHidden()
  await input.click()
  await expect(commonQuestions).toBeVisible()
  await page.getByRole('heading', { level: 1 }).click()
  await expect(commonQuestions).toBeHidden()
  await input.focus()
  await expect(page.getByRole('complementary', { name: '服务导航' })).toBeVisible()
  await expect(page.getByText('场景示例', { exact: true })).toHaveCount(0)
  await expect(commonQuestions.getByRole('button')).toHaveCount(11)
  await commonQuestions.getByRole('button', { name: '小米手机保修多久？', exact: true }).click()
  await expect(input).toHaveValue('小米手机保修多久？')
  await expect(input).toBeFocused()
  await expect(commonQuestions).toBeHidden()
  await expect(messages).toHaveCount(1)
  for (const question of [
    '我要投诉，小米15 手机的售后问题一直没有人处理',
    '我要申请蓝牙耳机维修，原因是无法开机。',
    '帮我查询最近一笔订单的物流状态。',
    '小米15 手机的问题我想找真人处理，请帮我转人工客服',
    '你好，我是 小米 手机用户，来打个招呼',
  ]) {
    await input.fill('')
    await commonQuestions.getByRole('button', { name: question, exact: true }).click()
    await expect(input).toHaveValue(question)
    await expect(input).toBeFocused()
  }
  await expect(messages).toHaveCount(1)
  await input.fill('   ')
  await expect(commonQuestions).toBeVisible()
  await input.press('Enter')
  await expect(messages).toHaveCount(1)
  await input.fill('  如何查询订单？  ')
  const reply = page.waitForResponse(response => response.url().endsWith('/api/chat/stream'))
  await send.click()
  const response = await reply
  expect(response.request().method()).toBe('POST')
  expect(response.request().postDataJSON()).toMatchObject({ message: '如何查询订单？', conversation_id: '11111111-1111-4111-8111-111111111111' })
  expect(response.request().postDataJSON().client_message_id).toMatch(/^[0-9a-f-]{36}$/)
  expect(response.status()).toBe(200)
  expect(response.headers()['content-type']).toContain('text/event-stream')
  await expect(messages).toHaveCount(3)
  await expect(messages.nth(1)).toContainText('如何查询订单？')
  await expect(messages.nth(2)).toContainText('测试客服回复')
  await messages.nth(2).locator('.stream-progress summary').click()
  await expect(messages.nth(2)).toContainText('识别意图：咨询')
  await expect(messages.nth(2)).toContainText('意图来源：LLM')
  await expect(messages.nth(2)).not.toContainText('BERT 分类')
  const trace = messages.nth(2).getByRole('list', { name: 'Graph 节点执行轨迹' })
  await expect(trace.locator('code')).toHaveText(['prepare_context', 'recognize_intent', 'retrieve_knowledge', 'generate_answer', 'validate_answer'])
  await expect(trace.getByRole('listitem')).toHaveCount(5)
  await expect(trace).toContainText('LLM 意图识别（recognize_intent）')
  await expect(trace).toContainText('知识检索（暂未接入）（retrieve_knowledge）')
  await expect(trace).toContainText('0.10 秒')
  await expect(trace).not.toContainText('select_business_route')
  await expect(messages.nth(2)).toContainText('检查点已保存（非节点） · 完成')
  await expect(messages.nth(2)).not.toContainText('数据来源')
  await expect(messages.nth(2).getByRole('link', { name: '资料来源' })).toHaveCount(0)
  await expect(input).toHaveValue('')

  await input.fill('退货怎么办？')
  await input.press('Enter')
  await expect(messages).toHaveCount(5)
  await expect(commonQuestions).toBeHidden()
  await input.click()
  await expect(commonQuestions).toBeVisible()

  await input.fill('中文输入中')
  await input.dispatchEvent('keydown', { key: 'Enter', isComposing: true })
  await expect(messages).toHaveCount(5)
  await page.route('**/api/conversation', route => route.fulfill({ json: {
    conversation: { id: '11111111-1111-4111-8111-111111111111', mode: 'ai', version: 0, event_seq: 2 },
    messages: [{ id: '1', role: 'user', text: '已保存的问题' }, { id: '2', role: 'assistant', text: '已保存的回复' }],
  } }))
  await page.reload()
  await expect(messages).toHaveCount(3)
  await expect(messages.last()).toContainText('已保存的回复')
})

test('售后确认按钮发送确认提交，旧确认失效且提交期间不会重复发送', async ({ page }) => {
  const requests = []
  let completeSubmission
  const submissionGate = new Promise(resolve => { completeSubmission = resolve })
  await page.route('**/api/chat/stream', async route => {
    const { message } = route.request().postDataJSON()
    requests.push(message)
    if (message === '确认提交') await submissionGate
    const events = message === '申请维修'
      ? [['after_sales_confirmation', { required: true }], ['delta', { text: '请核对订单后确认提交。' }]]
      : [['delta', { text: message === '确认提交' ? '已提交申请，待审核。' : '您可以修改信息后再确认提交。' }]]
    await route.fulfill({
      contentType: 'text/event-stream',
      body: [['start', {}], ...events, ['done', {}]]
        .map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join(''),
    })
  })
  await page.goto('/')
  const input = page.getByRole('textbox', { name: '输入你的问题' })
  const confirm = page.getByRole('button', { name: '确认提交', exact: true })
  await input.fill('先咨询')
  await input.press('Enter')
  await expect(page.getByRole('article').last()).toContainText('再确认提交')
  await expect(confirm).toHaveCount(0)
  await input.fill('申请维修')
  await input.press('Enter')
  await expect(confirm).toBeEnabled()
  await input.fill('订单不对')
  await input.press('Enter')
  await expect(confirm).toHaveCount(0)
  await expect(page.getByRole('article').last()).toContainText('修改信息')
  await input.fill('申请维修')
  await input.press('Enter')
  await expect(confirm).toBeEnabled()
  await confirm.evaluate(button => { button.click(); button.click() })
  await expect(page.getByRole('article').last()).toContainText('正在处理')
  await expect(confirm).toHaveCount(0)
  await expect(page.getByRole('button', { name: '发送中…' })).toBeDisabled()
  expect(requests.filter(message => message === '确认提交')).toHaveLength(1)
  completeSubmission()
  await expect(page.getByRole('article').last()).toContainText('已提交申请，待审核')
  await expect(page.getByRole('article').nth(-2)).toHaveText(/确认提交/)
  await expect(confirm).toHaveCount(0)
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

test('登录接口无响应时退出加载状态，并允许恢复后重试登录', async ({ page }) => {
  await page.route('**/api/auth/me', () => {})
  await page.route('**/api/auth/login', () => {})
  await page.goto('/')
  await expect(page.getByRole('status')).toHaveText('正在读取登录状态…')
  await expect(page.getByRole('heading', { name: '登录服务通智能客服' })).toBeVisible({ timeout: 12000 })
  await expect(page.getByRole('alert')).toContainText('读取登录状态超时')

  await page.getByLabel('账号', { exact: true }).fill('test-user')
  await page.getByLabel('密码', { exact: true }).fill('test-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('登录请求超时', { timeout: 12000 })
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeEnabled()

  await page.route('**/api/auth/login', route => route.fulfill({ json: { username: 'test-user' } }))
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('region', { name: '客服对话' })).toBeVisible()
  await expect(page.getByRole('status')).toHaveCount(0)
})
