import { expect, test } from '@playwright/test'

test('输入、空白校验、按钮和回车发送、真实接口回复', async ({ page }) => {
  await page.goto('/')
  const input = page.getByRole('textbox', { name: '输入你的问题' })
  const send = page.getByRole('button', { name: '发送' })
  const messages = page.getByRole('article')

  await expect(messages).toHaveCount(1)
  await expect(send).toBeDisabled()
  await input.fill('   ')
  await input.press('Enter')
  await expect(messages).toHaveCount(1)
  await input.fill('  如何查询订单？  ')
  const reply = page.waitForResponse(response => response.url().endsWith('/api/chat'))
  await send.click()
  const response = await reply
  expect(response.request().method()).toBe('POST')
  expect(response.request().postDataJSON()).toEqual({ message: '如何查询订单？' })
  expect(response.status()).toBe(200)
  expect(await response.json()).toEqual({ reply: 'helloword' })
  await expect(messages).toHaveCount(3)
  await expect(messages.nth(1)).toContainText('如何查询订单？')
  await expect(messages.nth(2)).toContainText('helloword')
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

test('接口拒绝空白问题，服务出错后可以再次提问', async ({ page, request }) => {
  const invalid = await request.post('/api/chat', { data: { message: '   ' } })
  expect(invalid.status()).toBe(422)

  await page.route('**/api/chat', route => route.fulfill({ status: 503 }))
  await page.goto('/')
  await page.getByRole('textbox').fill('你好')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.getByRole('article').last()).toContainText('暂时无法获取回复')

  await page.unroute('**/api/chat')
  await page.getByRole('textbox').fill('再次提问')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.getByRole('article').last()).toContainText('helloword')
})
