<script setup>
import { ref } from 'vue'

defineProps({ message: { type: String, default: '' } })
const emit = defineEmits(['logged-in'])
const username = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')

async function login() {
  if (busy.value || !username.value.trim() || !password.value) return
  busy.value = true
  error.value = ''
  try {
    const response = await fetch('/api/auth/login', {
      method: 'POST',
      signal: AbortSignal.timeout(8000),
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: username.value.trim(), password: password.value }),
    })
    if (!response.ok) throw new Error(response.status === 401 ? '账号或密码错误' : '后端服务暂时不可用，请稍后重试')
    const user = await response.json()
    password.value = ''
    emit('logged-in', user)
  } catch (cause) {
    error.value = cause.name === 'TimeoutError' ? '登录请求超时，请检查后端服务后重试' : cause instanceof TypeError ? '无法连接服务，请稍后重试' : cause.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="login-layout">
    <div class="login-intro">
      <span class="intro-label"><span></span> CUSTOMER SERVICE WORKSPACE</span>
      <h2>每一次咨询，<br />都能从容应对<span>。</span></h2>
      <p>连接商品知识与客户需求，让选购、使用和售后问题，在一次自然的对话中得到回应。</p>
      <div class="login-capabilities"><span>商品咨询</span><span>订单查询</span><span>售后协助</span></div>
      <div class="service-illustration" aria-hidden="true">
        <div class="illustration-icon">✦</div>
        <div class="illustration-line line-one"></div><div class="illustration-line line-two"></div><div class="illustration-line line-three"></div>
        <span class="illustration-caption">把复杂的问题，交给简单的对话。</span>
      </div>
    </div>
  <section class="login-card" aria-label="账号登录">
    <span class="login-eyebrow">WELCOME BACK</span>
    <h2>登录服务通智能客服</h2>
    <p class="login-description">欢迎回来，登录账号开始咨询。</p>
    <form class="login-form" @submit.prevent="login">
      <label for="username">账号</label>
      <input id="username" v-model="username" autocomplete="username" maxlength="64" placeholder="请输入账号" required />
      <label for="password">密码</label>
      <input id="password" v-model="password" type="password" autocomplete="current-password" maxlength="256" placeholder="请输入密码" required />
      <p v-if="error || message" class="auth-error" role="alert">{{ error || message }}</p>
      <button type="submit" :disabled="busy || !username.trim() || !password">{{ busy ? '登录中…' : '登录' }}</button>
    </form>
    <p class="login-note">使用已有账号登录服务工作台</p>
  </section>
  </div>
</template>
