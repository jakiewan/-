<script setup>
import { onMounted, ref } from 'vue'
const emit = defineEmits(['changed', 'close'])
const items = ref([])
const autoCapture = ref(false)
const busy = ref(false)
const error = ref('')
const key = ref('reply_style')
const value = ref('')
const labels = { preferred_name: '称呼', phone_preferences: '选购偏好', reply_style: '回复偏好', current_device: '自述设备', agreement: '任务共识', todo: '待办', episode: '排查记录' }
async function request(path = '', method = 'GET', body) {
  const response = await fetch(`/api/memory${path}`, { method, headers: { 'Content-Type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}) })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '操作未完成，请检查填写内容')
  return data
}
async function load() {
  const data = await request()
  items.value = data.items
  autoCapture.value = data.auto_capture
}
async function act(path, method, body) {
  busy.value = true
  error.value = ''
  try { await request(path, method, body); await load(); emit('changed') }
  catch (e) { error.value = e.message }
  finally { busy.value = false }
}
onMounted(() => load().catch(e => { error.value = e.message }))
</script>
<template>
  <section class="memory-panel" aria-label="长期记忆管理">
    <div class="memory-title"><h3>长期记忆</h3><button type="button" @click="emit('close')">关闭</button></div>
    <p>仅保存服务偏好和必要的任务记录。记忆不能作为订单事实或业务授权。</p>
    <label><input type="checkbox" :checked="autoCapture" :disabled="busy" @change="act('/settings', 'PUT', { auto_capture: $event.target.checked })"> 自动提取稳定偏好（默认关闭）</label>
    <form @submit.prevent="act('', 'POST', { key, value, quote: value })">
      <label>类型 <select v-model="key"><option v-for="field in ['preferred_name', 'phone_preferences', 'reply_style', 'current_device']" :key="field" :value="field">{{ labels[field] }}</option></select></label>
      <label>内容 <input v-model="value" maxlength="300" required placeholder="例如：我希望回复简洁一些"></label>
      <button :disabled="busy">保存或更新</button>
    </form>
    <ul>
      <li v-for="item in items" :key="item.id">
        <strong>{{ labels[item.memory_key] }}</strong>：{{ item.value }}
        <small>{{ item.status === 'pending_review' ? '待您确认，尚未使用' : '已保存' }} · {{ item.task_status === 'overdue' ? '已逾期，未标记完成' : '' }} <template v-if="item.memory_key === 'todo' && ['open', 'overdue'].includes(item.task_status)">保留至处理</template><template v-else>有效期至 {{ new Date(item.expires_at).toLocaleDateString() }}</template></small>
        <button v-if="item.scope_id === 'user' && item.status === 'active'" type="button" @click="key = item.memory_key; value = item.value">编辑</button>
        <template v-if="item.status === 'pending_review'">
          <button type="button" :disabled="busy" @click="act(`/${item.id}/review`, 'POST', { approve: true, version: item.version })">保存候选</button>
          <button type="button" :disabled="busy" @click="act(`/${item.id}/review`, 'POST', { approve: false, version: item.version })">拒绝</button>
        </template>
        <template v-if="item.memory_key === 'todo' && item.status === 'active' && ['open', 'overdue'].includes(item.task_status)">
          <button type="button" :disabled="busy" @click="act(`/${item.id}`, 'PATCH', { version: item.version, status: 'done' })">标记完成</button>
          <button type="button" :disabled="busy" @click="act(`/${item.id}`, 'PATCH', { version: item.version, status: 'cancelled' })">取消待办</button>
        </template>
        <button type="button" :disabled="busy" @click="act(`/${item.id}`, 'DELETE')">删除</button>
      </li>
    </ul>
    <p v-if="!items.length">暂无有效记忆。</p>
    <button type="button" :disabled="busy" @click="act('', 'DELETE')">清空记忆并关闭自动保存</button>
    <p>删除后旧聊天仍可查看，但不再用于个性化推理；待提交申请需重新确认。</p>
    <p v-if="error" role="alert">{{ error }}</p>
  </section>
</template>
<style scoped>
.memory-panel { padding: 16px; background: #f8fafc; max-height: 48vh; overflow: auto; color: #1e293b; border-bottom: 1px solid #dbe3ed; }
.memory-title { display: flex; justify-content: space-between; align-items: center; }
form { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
button, input, select { padding: 6px; color: #1e293b; max-width: 100%; }
input { width: 240px; min-width: 0; border: 1px solid #cbd5e1; background: white; }
input[type=checkbox] { width: 16px; min-width: 16px; height: 16px; flex: none; padding: 0; vertical-align: middle; }
form label { display: flex; align-items: center; gap: 6px; min-width: 0; max-width: 100%; }
button { border: 1px solid #cbd5e1; border-radius: 6px; background: white; }
button:hover:not(:disabled) { background: #eef2ff; }
li { margin: 10px 0; overflow-wrap: anywhere; }
small { display: block; }
p { font-size: 13px; }
</style>
