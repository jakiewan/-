<script setup>
import { nextTick, onMounted, ref } from 'vue'
import LoginForm from './LoginForm.vue'

const user = ref(null)
const checkingSession = ref(true)
const switching = ref(false)
const authError = ref('')
const question = ref('')
const sending = ref(false)
const conversation = ref(null)
const intentExamples = [
  ['咨询', '这款笔记本支持外接 4K 显示器吗？'],
  ['投诉', '我要投诉，客服一直没有处理我的问题'],
  ['售后', '我买的耳机有故障，想申请换货'],
  ['订单查询', '帮我查询订单 123 的物流状态'],
  ['转人工', '请帮我转人工客服'],
  ['闲聊', '你好，今天过得怎么样？'],
]
const messages = ref([
  { role: 'assistant', text: '你好，欢迎使用电商智能客服 👋\n关于手机、电脑、数码配件的选购、使用和售后问题，都可以在这里咨询。' },
])

function changeUser(value) {
  user.value = value
  messages.value.splice(1)
  question.value = ''
  authError.value = ''
}

onMounted(async () => {
  try {
    const response = await fetch('/api/auth/me')
    if (response.ok) changeUser(await response.json())
    else if (response.status !== 401) authError.value = '读取登录状态失败，请重试登录'
  } catch {
    authError.value = '无法连接服务，请检查后端是否启动'
  } finally {
    checkingSession.value = false
  }
})

async function logout() {
  if (sending.value || switching.value) return
  switching.value = true
  try {
    const response = await fetch('/api/auth/logout', { method: 'POST' })
    if (!response.ok) throw new Error('退出失败')
    changeUser(null)
  } catch {
    authError.value = '退出失败，请检查连接后重试'
  } finally {
    switching.value = false
  }
}

async function sendMessage() {
  const text = question.value.trim()
  if (!text || sending.value || switching.value || !user.value) return

  sending.value = true
  messages.value.push({ role: 'user', text })
  const replyIndex = messages.value.push({ role: 'assistant', text: '', steps: [], nodes: [], sources: [], orders: [], pending: true }) - 1
  const reply = messages.value[replyIndex]
  const startedAt = performance.now()
  question.value = ''
  await nextTick()
  conversation.value.scrollTop = conversation.value.scrollHeight

  try {
    const response = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ message: text }),
    })
    if (response.status === 401) {
      changeUser(null)
      authError.value = '登录已失效，请重新登录'
      return
    }
    if (!response.ok) throw new Error(`请求失败：${response.status}`)
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let completed = false
    try {
      while (true) {
        const { value, done } = await reader.read()
        buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')
        let boundary
        while ((boundary = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, boundary)
          buffer = buffer.slice(boundary + 2)
          const lines = frame.split('\n')
          const name = lines.find(line => line.startsWith('event:'))?.slice(6).trim()
          const raw = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
          if (!raw) continue
          const data = JSON.parse(raw)
          if (name === 'intent') reply.intent = data
          if (name === 'sources') {
            for (const source of data.sources) {
              if (!reply.sources.some(item => item.url === source.url)) reply.sources.push(source)
            }
          }
          if (name === 'orders') reply.orders = data.orders
          if (name === 'node') {
            const node = reply.nodes.find(item => item.id === data.id)
            if (node) Object.assign(node, data)
            else reply.nodes.push(data)
          }
          if (name === 'progress') {
            const step = reply.steps.find(item => item.step === data.step)
            if (step) Object.assign(step, data)
            else reply.steps.push(data)
          }
          if (name === 'delta' && data.text) {
            reply.firstTokenMs ??= Math.round(performance.now() - startedAt)
            reply.text += data.text
          }
          if (name === 'error') throw new Error(data.message)
          if (name === 'done') {
            completed = true
            reply.elapsedMs = Math.round(performance.now() - startedAt)
          }
        }
        await nextTick()
        if (conversation.value) conversation.value.scrollTop = conversation.value.scrollHeight
        if (done) break
      }
      if (!completed) throw new Error('连接中断，回复可能不完整，请重试。')
    } finally {
      await reader.cancel().catch(() => {})
      reader.releaseLock()
    }
  } catch (error) {
    reply.error = `暂时无法获取回复。${error.message}`
    for (const step of [...reply.steps, ...reply.nodes]) {
      if (step.status === 'running') step.status = 'error'
    }
  } finally {
    reply.pending = false
    sending.value = false
    await nextTick()
    if (conversation.value) {
      const orderCard = conversation.value.querySelector('article:last-child .order-card')
      conversation.value.scrollTop = orderCard
        ? conversation.value.scrollTop + orderCard.getBoundingClientRect().top - conversation.value.getBoundingClientRect().top - 12
        : conversation.value.scrollHeight
    }
  }
}
</script>

<template>
  <main class="page">
    <div class="page-heading">
      <span class="eyebrow">E-COMMERCE AI SUPPORT</span>
      <h1>企业级电商智能客服</h1>
      <p>电子产品选购与售后咨询，从这里开始。</p>
    </div>

    <p v-if="checkingSession" role="status">正在读取登录状态…</p>
    <LoginForm v-else-if="!user" :message="authError" @logged-in="changeUser" />

    <section v-else class="chat" aria-label="客服对话">
      <header class="chat-header">
        <div class="avatar service-avatar" aria-hidden="true">客</div>
        <div>
          <h2>电商智能客服</h2>
          <p><span class="status-dot"></span>很高兴为你服务</p>
        </div>
        <div class="account-controls">
          <span class="account-name">{{ user.username }}</span>
          <button class="switch-account" type="button" :disabled="sending || switching" @click="logout">{{ switching ? '退出中…' : '退出 / 切换账号' }}</button>
        </div>
      </header>
      <p v-if="authError" class="auth-error" role="alert">{{ authError }}</p>

      <div class="intent-examples" aria-label="意图测试示例">
        <span>意图测试</span>
        <button v-for="[intent, example] in intentExamples" :key="intent" type="button" :disabled="sending" :title="example" @click="question = example">{{ intent }}</button>
      </div>

      <div ref="conversation" class="conversation" role="log" aria-label="聊天记录" aria-live="polite" tabindex="0">
        <div class="conversation-note">本次对话 · 随时为你解答</div>
        <article v-for="(message, index) in messages" :key="index" class="message" :class="message.role">
          <div class="avatar" :class="message.role === 'assistant' ? 'service-avatar' : 'user-avatar'" aria-hidden="true">
            {{ message.role === 'assistant' ? '客' : '我' }}
          </div>
          <div class="message-content">
            <span class="sender">{{ message.role === 'assistant' ? '电商客服助手' : '你' }}</span>
            <div v-if="message.steps" class="stream-progress">
              <p v-if="message.intent" class="intent-result"><strong>识别意图：{{ message.intent.intent }}</strong><span>{{ message.intent.reason }}</span></p>
              <div v-if="message.nodes.length" class="node-trace">
                <strong>Graph 节点执行轨迹</strong>
                <ol aria-label="Graph 节点执行轨迹">
                  <li v-for="(node, nodeIndex) in message.nodes" :key="node.id" :class="node.status">
                    <span>{{ nodeIndex + 1 }}.</span>
                    <span>{{ node.label }}（<code>{{ node.name }}</code>）<template v-if="node.tools?.length">：{{ node.tools.join('、') }}</template></span>
                    <span>· {{ { running: '进行中', done: '完成', error: '失败' }[node.status] }}</span>
                    <span v-if="node.elapsed_ms !== undefined">{{ (node.elapsed_ms / 1000).toFixed(2) }} 秒</span>
                  </li>
                </ol>
              </div>
              <ol aria-label="处理进度">
                <li v-for="step in message.steps" :key="step.step" :class="step.status">
                  <span aria-hidden="true">{{ step.status === 'done' ? '✓' : step.status === 'running' ? '◌' : step.status === 'error' ? '!' : '○' }}</span>
                  {{ step.label }} · {{ { pending: '等待', running: '进行中', done: '完成', error: '失败' }[step.status] }}
                </li>
              </ol>
              <p v-if="message.firstTokenMs" class="stream-timing">首字 {{ (message.firstTokenMs / 1000).toFixed(2) }} 秒<span v-if="message.elapsedMs"> · 总耗时 {{ (message.elapsedMs / 1000).toFixed(2) }} 秒</span></p>
            </div>
            <p v-if="message.text" class="bubble">{{ message.text }}<span v-if="message.pending" class="stream-cursor" aria-hidden="true">▍</span></p>
            <p v-else-if="message.pending" class="bubble">正在处理…</p>
            <section v-for="order in message.orders" :key="order.order_no" class="order-card" :aria-label="`订单 ${order.order_no}`">
              <header class="order-card-heading">
                <h3>订单 {{ order.order_no }}</h3>
                <span>{{ order.status_label }}</span>
              </header>
              <p v-if="order.is_demo" class="order-demo">演示订单 · 商品和物流数据仅用于功能演示</p>
              <dl class="order-details">
                <div><dt>下单时间</dt><dd>{{ new Date(order.created_at).toLocaleString('zh-CN') }}</dd></div>
                <div><dt>订单金额</dt><dd>{{ order.currency === 'CNY' ? '¥' : order.currency }}{{ order.total_amount }}</dd></div>
              </dl>
              <ul class="order-items" aria-label="商品信息">
                <li v-for="(item, itemIndex) in order.items" :key="`${item.sku}-${itemIndex}`">
                  <div><strong>{{ item.name }}</strong><p>{{ item.spec }} · 数量 {{ item.quantity }}</p></div>
                  <span>{{ order.currency === 'CNY' ? '¥' : order.currency }}{{ item.unit_price }}</span>
                </li>
              </ul>
              <dl class="order-details">
                <div><dt>物流状态</dt><dd>{{ order.logistics_status || '暂无物流信息' }}</dd></div>
                <div><dt>承运方</dt><dd>{{ order.carrier || '暂无' }}</dd></div>
                <div><dt>运单号</dt><dd>{{ order.tracking_no || '暂无' }}</dd></div>
              </dl>
              <ol v-if="order.logistics_events.length" class="logistics-events" aria-label="物流轨迹">
                <li v-for="(event, eventIndex) in order.logistics_events" :key="eventIndex">
                  <time>{{ new Date(event.occurred_at).toLocaleString('zh-CN') }}</time>
                  <span>{{ event.description }}</span>
                </li>
              </ol>
            </section>
            <p v-if="message.sources?.length" class="data-sources">
              数据来源：
              <template v-for="source in message.sources" :key="source.url">
                <a v-if="/^https?:\/\//.test(source.url)" :href="source.url" target="_blank" rel="noopener noreferrer">{{ source.label }}</a>
                <span v-else>{{ source.label || source.url }}</span>
              </template>
            </p>
            <p v-if="message.error" class="auth-error" role="alert">{{ message.error }}</p>
          </div>
        </article>
      </div>

      <footer class="chat-footer">
        <form class="composer" @submit.prevent="sendMessage">
          <label class="sr-only" for="question">输入你的问题</label>
          <input id="question" v-model="question" type="text" placeholder="输入你的问题…" autocomplete="off" @keydown.enter="($event.isComposing || $event.keyCode === 229) && $event.preventDefault()" />
          <button type="submit" :disabled="sending || switching || !question.trim()">{{ sending ? '发送中…' : '发送' }} <span aria-hidden="true">↗</span></button>
        </form>
        <p class="input-hint">按 Enter 发送 <span>记忆按账号保存，刷新仅清空显示</span></p>
      </footer>
    </section>
    <p class="page-footer">让每一个问题，都得到回应。</p>
  </main>
</template>
