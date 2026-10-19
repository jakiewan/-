<script setup>
import { nextTick, ref } from 'vue'

const question = ref('')
const sending = ref(false)
const conversation = ref(null)
const messages = ref([
  { role: 'assistant', text: '你好，欢迎来到服务中心 👋\n请问有什么可以帮你？你可以在下方输入问题。' },
])

async function sendMessage() {
  const text = question.value.trim()
  if (!text || sending.value) return

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
    if (!response.ok) throw new Error(`请求失败：${response.status}`)
    const data = await response.json()
    messages.value.push({ role: 'assistant', text: data.reply })
  } catch {
    messages.value.push({ role: 'assistant', text: '暂时无法获取回复，请确认后端服务已启动后重试。' })
  } finally {
    sending.value = false
    await nextTick()
    conversation.value.scrollTop = conversation.value.scrollHeight
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

    <section class="chat" aria-label="客服对话">
      <header class="chat-header">
        <div class="avatar service-avatar" aria-hidden="true">客</div>
        <div>
          <h2>在线客服</h2>
          <p><span class="status-dot"></span>很高兴为你服务</p>
        </div>
        <span class="demo-badge">接口演示</span>
      </header>

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
          <button type="submit" :disabled="sending || !question.trim()">{{ sending ? '发送中…' : '发送' }} <span aria-hidden="true">↗</span></button>
        </form>
        <p class="input-hint">按 Enter 发送 <span>回复来自后端接口，刷新后对话将清空</span></p>
      </footer>
    </section>
    <p class="page-footer">让每一个问题，都得到回应。</p>
  </main>
</template>
