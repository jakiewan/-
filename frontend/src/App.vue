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
const messages = ref([
  { role: 'assistant', text: '你好，欢迎来到服务中心 👋\n请问有什么可以帮你？你可以在下方输入问题。' },
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
  question.value = ''
  await nextTick()
  conversation.value.scrollTop = conversation.value.scrollHeight

  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text }),
    })
    if (response.status === 401) {
      changeUser(null)
      authError.value = '登录已失效，请重新登录'
      return
    }
    if (!response.ok) throw new Error(`请求失败：${response.status}`)
    const data = await response.json()
    messages.value.push({ role: 'assistant', text: data.reply })
  } catch {
    messages.value.push({ role: 'assistant', text: '暂时无法获取回复，请确认后端服务已启动后重试。' })
  } finally {
    sending.value = false
    await nextTick()
    if (conversation.value) conversation.value.scrollTop = conversation.value.scrollHeight
  }
}
</script>

<template>
  <main class="page">
    <div class="page-heading">
      <span class="eyebrow">CUSTOMER SUPPORT</span>
      <h1>有问题，我们来帮你。</h1>
      <p>从一个问题开始，让沟通更简单。</p>
    </div>

    <p v-if="checkingSession" role="status">正在读取登录状态…</p>
    <LoginForm v-else-if="!user" :message="authError" @logged-in="changeUser" />

    <section v-else class="chat" aria-label="客服对话">
      <header class="chat-header">
        <div class="avatar service-avatar" aria-hidden="true">客</div>
        <div>
          <h2>在线客服</h2>
          <p><span class="status-dot"></span>很高兴为你服务</p>
        </div>
        <div class="account-controls">
          <span class="account-name">{{ user.username }}</span>
          <button class="switch-account" type="button" :disabled="sending || switching" @click="logout">{{ switching ? '退出中…' : '退出 / 切换账号' }}</button>
        </div>
      </header>
      <p v-if="authError" class="auth-error" role="alert">{{ authError }}</p>

      <div ref="conversation" class="conversation" role="log" aria-label="聊天记录" aria-live="polite" tabindex="0">
        <div class="conversation-note">本次对话 · 随时为你解答</div>
        <article v-for="(message, index) in messages" :key="index" class="message" :class="message.role">
          <div class="avatar" :class="message.role === 'assistant' ? 'service-avatar' : 'user-avatar'" aria-hidden="true">
            {{ message.role === 'assistant' ? '客' : '我' }}
          </div>
          <div class="message-content">
            <span class="sender">{{ message.role === 'assistant' ? '客服助手' : '你' }}</span>
            <p class="bubble">{{ message.text }}</p>
          </div>
        </article>
      </div>

      <footer class="chat-footer">
        <form class="composer" @submit.prevent="sendMessage">
          <label class="sr-only" for="question">输入你的问题</label>
          <input id="question" v-model="question" type="text" placeholder="输入你的问题…" autocomplete="off" @keydown.enter="($event.isComposing || $event.keyCode === 229) && $event.preventDefault()" />
          <button type="submit" :disabled="sending || switching || !question.trim()">{{ sending ? '发送中…' : '发送' }} <span aria-hidden="true">↗</span></button>
        </form>
        <p class="input-hint">按 Enter 发送 <span>回复来自后端接口，刷新后对话将清空</span></p>
      </footer>
    </section>
    <p class="page-footer">让每一个问题，都得到回应。</p>
  </main>
</template>
