<script setup>
import { nextTick, onMounted, onUnmounted, ref } from 'vue'

defineEmits(['logout', 'back'])
const conversations = ref([])
const selected = ref(null)
const messages = ref([])
const draft = ref('')
const note = ref('')
const lastNote = ref('')
const error = ref('')
const busy = ref(false)
const log = ref(null)
let source
let timer
let selectionVersion = 0
let pendingMessage
const modes = { ai: 'AI 服务', waiting_human: '等待接管', human: '人工接待', closed: '接待已结束' }
const notifications = { pending: '待发送', sending: '发送中', sent: '已发送', retry: '等待重试', failed: '发送失败', unknown: '结果待核对', not_configured: '尚未配置通知' }

async function api(path, body) {
  const response = await fetch(`/api/${path}`, body ? {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  } : {})
  const data = await response.json()
  if (!response.ok) throw new Error(data.detail || '请求失败')
  return data
}

async function refreshList() {
  try { conversations.value = (await api('support/conversations')).conversations }
  catch (cause) { error.value = cause.message }
}

function merge(message) {
  const old = messages.value.find(item => item.id === message.id)
  if (old) Object.assign(old, message)
  else messages.value.push(message)
  nextTick(() => { if (log.value) log.value.scrollTop = log.value.scrollHeight })
}

async function open(id) {
  source?.close()
  const version = ++selectionVersion
  error.value = ''
  draft.value = ''
  note.value = ''
  pendingMessage = null
  try {
    const data = await api(`conversations/${id}`)
    if (version !== selectionVersion) return
    selected.value = data.conversation
    lastNote.value = data.support_note || ''
    messages.value = data.messages
    source = new EventSource(`/api/conversations/${id}/events?after=${data.conversation.event_seq}`)
    source.addEventListener('message', event => { if (version === selectionVersion) merge(JSON.parse(event.data)) })
    source.addEventListener('state', event => { if (version === selectionVersion) selected.value = JSON.parse(event.data) })
    source.addEventListener('auth_expired', () => { source.close(); error.value = '登录已失效，请重新登录' })
    source.addEventListener('reset', () => open(id))
    await nextTick()
    if (log.value) log.value.scrollTop = log.value.scrollHeight
    history.replaceState(null, '', `/?support=${id}`)
  } catch (cause) { error.value = cause.message }
}

async function action(name, extra = {}) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    selected.value = await api(`support/conversations/${selected.value.id}/${name}`, { version: selected.value.version, ...extra })
    await refreshList()
  } catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}

async function send() {
  const text = draft.value.trim()
  if (!text || busy.value) return
  if (!pendingMessage || pendingMessage.message !== text) pendingMessage = { message: text, client_message_id: crypto.randomUUID() }
  busy.value = true
  error.value = ''
  try {
    merge(await api(`support/conversations/${selected.value.id}/messages`, pendingMessage))
    draft.value = ''
    pendingMessage = null
  } catch (cause) { error.value = cause.message }
  finally { busy.value = false }
}

onMounted(async () => {
  await refreshList()
  const id = new URLSearchParams(location.search).get('support')
  if (id) await open(id)
  timer = setInterval(refreshList, 5000)
})
onUnmounted(() => { source?.close(); clearInterval(timer); selectionVersion += 1 })
</script>

<template>
  <div class="support-workspace">
    <aside class="support-list" aria-label="客服待处理会话">
      <h2>客服工作台</h2>
      <p>通知投递与实际接管分别记录。</p>
      <button type="button" @click="$emit('back')">返回用户聊天</button>
      <button type="button" @click="$emit('logout')">退出账号</button>
      <p v-if="!conversations.length">暂无人工请求</p>
      <button v-for="item in conversations" :key="item.id" class="support-item" type="button" :aria-pressed="selected?.id === item.id" @click="open(item.id)">
        <strong>{{ item.user_id }} · {{ modes[item.mode] }}</strong>
        <span>{{ item.reason }}</span>
        <small>飞书：{{ notifications[item.notification_status] || item.notification_status }}</small>
      </button>
    </aside>
    <section class="support-chat" aria-label="人工接待会话">
      <p v-if="error" role="alert" class="auth-error">{{ error }}</p>
      <template v-if="selected">
        <header>
          <h2>{{ selected.user_id }} · {{ modes[selected.mode] }}</h2>
          <button v-if="selected.mode === 'waiting_human'" type="button" :disabled="busy" @click="action('claim')">接管会话</button>
        </header>
        <div ref="log" class="support-history" role="log" aria-label="人工会话历史" aria-live="polite">
          <article v-for="message in messages" :key="message.id" class="support-message">
            <strong>{{ { user: '客户', assistant: 'AI 助手', agent: '人工客服', system: '服务通知' }[message.role] }}</strong>
            <p>{{ message.text }}</p>
            <p v-for="application in message.applications || []" :key="application.application_no">申请 {{ application.application_no }}：{{ application.status_label }}</p>
          </article>
        </div>
        <form v-if="selected.mode === 'human'" class="support-reply" @submit.prevent="send">
          <label for="support-reply">回复客户</label>
          <textarea id="support-reply" v-model="draft" rows="3" maxlength="10000" />
          <button type="submit" :disabled="busy || !draft.trim()">发送人工回复</button>
        </form>
        <p v-if="lastNote">上次接待记录：{{ lastNote }}</p>
        <div v-if="selected.mode === 'human'" class="support-end">
          <label for="support-note">接待记录（内部）</label>
          <input id="support-note" v-model="note" maxlength="1000" placeholder="记录处理结果或仍需跟进的问题" />
          <button type="button" :disabled="busy || !note.trim()" @click="action('end', { note, resume_ai: true })">结束并恢复 AI</button>
          <button type="button" :disabled="busy || !note.trim()" @click="action('end', { note, resume_ai: false })">结束接待</button>
        </div>
      </template>
      <p v-else>从左侧选择一个会话，查看历史后主动接管。</p>
    </section>
  </div>
</template>

<style scoped>
.support-workspace { display: grid; grid-template-columns: 280px minmax(0, 1fr); width: 100%; min-height: 65vh; gap: 16px; }
.support-list, .support-chat { padding: 20px; background: white; border: 1px solid #dce3ed; border-radius: 14px; min-width: 0; }
.support-list p, small { color: #64748b; }
button { padding: 8px 12px; border: 1px solid #cbd5e1; border-radius: 8px; background: #f8fafc; color: #1e293b; cursor: pointer; }
button:disabled { opacity: .5; cursor: default; }
.support-item { display: grid; gap: 8px; text-align: left; width: 100%; margin-top: 12px; overflow-wrap: anywhere; }
.support-item[aria-pressed=true] { border-color: #2563eb; }
.support-history { height: 42vh; overflow-y: auto; padding: 8px 0; }
.support-message { border-bottom: 1px solid #edf2f7; padding: 12px 0; }
.support-message p { white-space: pre-wrap; overflow-wrap: anywhere; margin: 8px 0; }
.support-reply, .support-end { display: grid; gap: 8px; margin-top: 16px; }
textarea, input { width: 100%; box-sizing: border-box; padding: 10px; border: 1px solid #cbd5e1; border-radius: 8px; font: inherit; }
@media (max-width: 700px) { .support-workspace { grid-template-columns: minmax(0, 1fr); } }
</style>
