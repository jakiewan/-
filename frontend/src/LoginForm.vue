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
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: username.value.trim(), password: password.value }),
    })
    if (!response.ok) throw new Error(response.status === 401 ? '账号或密码错误' : '登录失败，请检查后端账号配置')
    const user = await response.json()
    password.value = ''
    emit('logged-in', user)
  } catch (cause) {
    error.value = cause instanceof TypeError ? '无法连接服务，请稍后重试' : cause.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="login-card" aria-label="账号登录">
    <span class="avatar service-avatar" aria-hidden="true">客</span>
    <h2>登录电商智能客服</h2>
    <p class="login-description">登录你的账号，咨询电子产品选购、使用与售后问题。</p>
    <form class="login-form" @submit.prevent="login">
      <label for="username">账号</label>
      <input id="username" v-model="username" autocomplete="username" maxlength="64" placeholder="请输入账号" required />
      <label for="password">密码</label>
      <input id="password" v-model="password" type="password" autocomplete="current-password" maxlength="256" placeholder="请输入密码" required />
      <p v-if="error || message" class="auth-error" role="alert">{{ error || message }}</p>
      <button type="submit" :disabled="busy || !username.trim() || !password">{{ busy ? '登录中…' : '登录' }}</button>
    </form>
  </section>
</template>
