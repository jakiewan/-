<script setup>
import { nextTick, onMounted, ref } from 'vue'
import LoginForm from './LoginForm.vue'

const user = ref(null)
const checkingSession = ref(true)
const switching = ref(false)
const authError = ref('')
const question = ref('')
const questionInput = ref(null)
const showCommonQuestions = ref(false)
const sending = ref(false)
const conversation = ref(null)
const commonQuestions = [
  '小米手机保修多久？',
  '小米手机进水能免费保修吗？',
  '小米手机保修需要什么材料？',
  '小米15的屏幕参数是什么？',
  '小米15支持多少瓦有线充电？',
  '小米15 Pro有哪些运行内存和存储版本？',
  '我要投诉，小米15 手机的售后问题一直没有人处理',
  '我要申请蓝牙耳机维修，原因是无法开机。',
  '帮我查询最近一笔订单的物流状态。',
  '小米15 手机的问题我想找真人处理，请帮我转人工客服',
  '你好，我是 小米 手机用户，来打个招呼',
]
const messages = ref([
  { role: 'assistant', text: '你好，欢迎使用服务通智能客服 👋\n关于手机、电脑、数码配件的选购、使用和售后问题，都可以在这里咨询。' },
])

function changeUser(value) {
  user.value = value
  messages.value.splice(1)
  question.value = ''
  showCommonQuestions.value = false
  authError.value = ''
}

function closeCommonQuestions(event) {
  if (!event.relatedTarget?.closest('#question, .question-suggestions')) {
    showCommonQuestions.value = false
  }
}

function selectQuestion(text) {
  question.value = text
  questionInput.value.focus()
}

onMounted(async () => {
  try {
    const response = await fetch('/api/auth/me', { signal: AbortSignal.timeout(8000) })
    if (response.ok) changeUser(await response.json())
    else if (response.status !== 401) authError.value = '读取登录状态失败，请重试登录'
  } catch (cause) {
    authError.value = cause.name === 'TimeoutError' ? '读取登录状态超时，请检查后端服务后重试登录' : '无法连接服务，请检查后端是否启动'
  } finally {
    checkingSession.value = false
  }
})

async function logout() {
  if (sending.value || switching.value) return
  switching.value = true
  try {
    const response = await fetch('/api/auth/logout', { method: 'POST', signal: AbortSignal.timeout(8000) })
    if (!response.ok) throw new Error('退出失败')
    changeUser(null)
  } catch {
    authError.value = '退出失败，请检查连接后重试'
  } finally {
    switching.value = false
  }
}

async function sendMessage(text = question.value.trim()) {
  if (!text || sending.value || switching.value || !user.value) return

  showCommonQuestions.value = false
  sending.value = true
  messages.value.push({ role: 'user', text })
  const replyIndex = messages.value.push({ role: 'assistant', text: '', steps: [], nodes: [], sources: [], orders: [], applications: [], pending: true }) - 1
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
          if (name === 'applications') reply.applications = data.applications
          if (name === 'after_sales_confirmation') reply.confirmationRequired = data.required
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
  <main class="page" :class="{ 'is-authenticated': user }">
    <header class="page-heading">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none"><path d="M5 5h14v11H9l-4 3V5Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M9 9h6M9 12h4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>
        </span>
        <div><span class="eyebrow">SERVICE HUB</span><h1>服务通智能客服</h1></div>
      </div>
      <p class="workspace-label">客户服务工作台 <span> / </span> 智能会话</p>
    </header>

    <p v-if="checkingSession" role="status">正在读取登录状态…</p>
    <LoginForm v-else-if="!user" :message="authError" @logged-in="changeUser" />

    <div v-else class="workspace">
      <aside class="workspace-sidebar" aria-label="服务导航">
        <div class="sidebar-label">工作空间</div>
        <div class="workspace-current"><span aria-hidden="true">◈</span> 智能会话 <span class="ai-badge">AI</span></div>
        <div class="sidebar-tip"><span class="tip-icon" aria-hidden="true">✦</span><strong>让问题更清晰</strong><p>提供商品型号与具体问题，帮助我们找到更合适的解答。</p></div>
        <p class="sidebar-footer">SERVICE HUB <span>智能服务 · 轻松沟通</span></p>
        <div class="account-controls">
          <span class="account-name">{{ user.username }}</span>
          <button class="switch-account" type="button" :disabled="sending || switching" @click="logout">{{ switching ? '退出中…' : '退出 / 切换账号' }}</button>
        </div>
      </aside>
      <section class="chat" aria-label="客服对话">
      <header class="chat-header">
        <div class="avatar service-avatar" aria-hidden="true">✦</div>
        <div>
          <h2>智能服务助手 <span class="assistant-badge">AI</span></h2>
          <p><span class="status-dot"></span>{{ sending ? '正在为您处理问题' : '商品咨询 · 订单查询 · 售后协助' }}</p>
        </div>
      </header>
      <p v-if="authError" class="auth-error" role="alert">{{ authError }}</p>

      <div ref="conversation" class="conversation" :class="{ 'is-empty': messages.length === 1 }" role="log" aria-label="聊天记录" aria-live="polite" tabindex="0">
        <div class="conversation-note">本次对话 · 随时为你解答</div>
        <div v-if="messages.length === 1" class="welcome-intro">
          <div class="welcome-symbol" aria-hidden="true">✦</div>
          <span class="eyebrow">YOUR SERVICE ASSISTANT</span>
          <h2>您好，有什么可以帮您？</h2>
        </div>
        <article v-for="(message, index) in messages" :key="index" class="message" :class="message.role">
          <div class="avatar" :class="message.role === 'assistant' ? 'service-avatar' : 'user-avatar'" aria-hidden="true">
            {{ message.role === 'assistant' ? '客' : '我' }}
          </div>
          <div class="message-content" :class="{ 'has-business-card': message.orders?.length || message.applications?.length }">
            <span class="sender">{{ message.role === 'assistant' ? '电商客服助手' : '你' }}</span>
            <details v-if="message.steps" class="stream-progress">
              <summary><span class="trace-status" :class="{ pending: message.pending, failed: message.error }" aria-hidden="true"></span><span>{{ message.error ? '处理未完成' : message.pending ? '正在处理' : '处理完成' }}</span><span v-if="message.intent" class="trace-intent">{{ message.intent.intent }}</span><span v-if="message.elapsedMs" class="trace-duration">{{ (message.elapsedMs / 1000).toFixed(2) }} 秒</span><span class="trace-toggle">执行详情 <span aria-hidden="true">⌄</span></span></summary>
              <div class="trace-details">
              <p v-if="message.intent" class="intent-result"><strong>识别意图：{{ message.intent.intent }}</strong><span>意图来源：{{ message.intent.source }}</span><span v-if="message.intent.reason">{{ message.intent.reason }}</span><span>已提供上一轮问答：{{ message.intent.context_used ? '是' : '否' }}</span></p>
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
            </details>
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
            <p v-if="message.confirmationRequired && index === messages.length - 1 && !message.pending && !message.error">
              <button type="button" :disabled="sending || switching" @click="sendMessage('确认提交')">确认提交</button>
            </p>
            <section v-for="application in message.applications" :key="application.application_no" class="order-card after-sales-card" :aria-label="`售后申请 ${application.application_no}`">
              <header class="order-card-heading">
                <h3>售后申请 {{ application.application_no }}</h3>
                <span>已提交申请 · {{ application.status_label }}</span>
              </header>
              <p v-if="application.is_demo" class="order-demo">演示订单的本地申请记录 · 未提交到外部商城</p>
              <dl class="order-details">
                <div><dt>申请类型</dt><dd>{{ application.action_label }}</dd></div>
                <div><dt>订单号</dt><dd>{{ application.order_no }}</dd></div>
                <div v-if="application.all_items"><dt>申请范围</dt><dd>整单全部商品</dd></div>
                <div><dt>商品</dt><dd>
                  <p v-for="(item, itemIndex) in application.items || [application.item]" :key="`${item.sku}-${itemIndex}`">{{ item.name }} · {{ item.spec }} · 数量 {{ item.quantity }}</p>
                </dd></div>
                <div><dt>申请原因</dt><dd>{{ application.reason || '未提供' }}</dd></div>
                <div><dt>提交时间</dt><dd>{{ new Date(application.created_at).toLocaleString('zh-CN') }}</dd></div>
              </dl>
            </section>
            <p v-if="message.error" class="auth-error" role="alert">{{ message.error }}</p>
          </div>
        </article>
      </div>

      <footer class="chat-footer" @focusout="closeCommonQuestions">
        <div v-if="showCommonQuestions && !question.trim() && !sending && !switching" class="question-suggestions">
          <p>常见问题 <span>选择一个问题开始咨询</span></p>
          <ul aria-label="常见问题">
            <li v-for="example in commonQuestions" :key="example">
              <button type="button" @click="selectQuestion(example)">{{ example }}</button>
            </li>
          </ul>
        </div>
        <form class="composer" @submit.prevent="sendMessage()">
          <label class="sr-only" for="question">输入你的问题</label>
          <input id="question" ref="questionInput" v-model="question" type="text" placeholder="输入问题，让我们为您解答…" autocomplete="off" @focus="showCommonQuestions = true" @click="showCommonQuestions = true" @keydown.enter="($event.isComposing || $event.keyCode === 229) && $event.preventDefault()" />
          <button type="submit" :disabled="sending || switching || !question.trim()">{{ sending ? '发送中…' : '发送' }} <span aria-hidden="true">↗</span></button>
        </form>
        <p class="input-hint">按 Enter 发送 <span>记忆按账号保存，刷新仅清空显示</span></p>
      </footer>
      </section>
    </div>
    <p class="page-footer">服务通 <span>·</span> 让每一个问题，都得到回应。</p>
  </main>
</template>
