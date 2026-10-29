<script setup lang="ts">
import { ref } from 'vue'
import { account, api, errorMessage, sessionNotice, type Account } from './session'

const mode = ref<'login' | 'register'>('login')
const username = ref('')
const password = ref('')
const confirmation = ref('')
const busy = ref(false)
const error = ref('')

function switchMode() {
  mode.value = mode.value === 'login' ? 'register' : 'login'
  password.value = ''
  confirmation.value = ''
  error.value = ''
  sessionNotice.value = ''
}

async function authenticate() {
  if (busy.value) return
  error.value = ''
  if (mode.value === 'register' && password.value !== confirmation.value) {
    error.value = '两次输入的密码不一致，请重新确认。'
    return
  }
  busy.value = true
  try {
    const user = await api<Account>(`/auth/${mode.value}`, {
      method: 'POST', body: JSON.stringify({ username: username.value.trim(), password: password.value }),
    })
    password.value = ''
    confirmation.value = ''
    sessionNotice.value = ''
    account.value = user
  } catch (cause) { error.value = errorMessage(cause) }
  finally { busy.value = false }
}
</script>

<template>
  <div class="auth-page">
    <header class="auth-brand brand">
      <span class="brand-mark" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M4 5h5a4 4 0 0 1 3 2 4 4 0 0 1 3-2h5v14h-5a4 4 0 0 0-3 1 4 4 0 0 0-3-1H4z M12 7v13" /></svg></span>
      <span>知识练习<small>学习与复习空间</small></span>
    </header>
    <main class="auth-content">
      <div class="auth-intro" aria-hidden="true">
        <span class="intro-label">每天一点，慢慢积累</span>
        <h2>给学习<br />留一段安静的时间。</h2>
        <p>从今天的一道题开始，<br />把学过的知识，慢慢变成自己的。</p>
        <div class="intro-line"></div>
        <span class="intro-note">练习 · 回顾 · 进步</span>
      </div>
      <section class="auth-card" :aria-label="mode === 'login' ? '登录' : '注册'">
        <div class="auth-heading"><p class="card-eyebrow">{{ mode === 'login' ? '继续你的学习' : '开启新的积累' }}</p><h1>{{ mode === 'login' ? '欢迎回来' : '创建账号' }}</h1><p>{{ mode === 'login' ? '登录后，接着完成今天的练习。' : '保存每一次练习，留住每一点进步。' }}</p></div>
        <p v-if="sessionNotice" class="auth-notice" role="status">{{ sessionNotice }}</p>
        <form :key="mode" class="auth-form" :aria-busy="busy" @submit.prevent="authenticate">
          <label for="auth-username">用户名</label>
          <input id="auth-username" v-model="username" autocomplete="username" autocapitalize="none" :spellcheck="false" required pattern="[a-z][a-z0-9_]{2,31}" minlength="3" maxlength="32" :disabled="busy" :aria-describedby="mode === 'register' ? 'username-help' : undefined" placeholder="输入用户名" />
          <small v-if="mode === 'register'" id="username-help">3–32 位小写字母、数字或下划线，以字母开头。</small>
          <label for="auth-password">密码</label>
          <input id="auth-password" v-model="password" type="password" :autocomplete="mode === 'register' ? 'new-password' : 'current-password'" required minlength="10" maxlength="128" :disabled="busy" :aria-describedby="mode === 'register' ? 'password-help' : undefined" placeholder="输入密码" />
          <small v-if="mode === 'register'" id="password-help">密码长度为 10–128 个字符。</small>
          <template v-if="mode === 'register'">
            <label for="auth-confirmation">确认密码</label>
            <input id="auth-confirmation" v-model="confirmation" type="password" autocomplete="new-password" required minlength="10" maxlength="128" :disabled="busy" placeholder="再次输入密码" />
          </template>
          <p v-if="error" class="auth-error" role="alert">{{ error }}</p>
          <button class="auth-submit" type="submit" :disabled="busy">{{ busy ? '请稍候…' : mode === 'login' ? '登录' : '创建账号' }}<span v-if="!busy" aria-hidden="true">→</span></button>
        </form>
        <p class="auth-switch">{{ mode === 'login' ? '还没有账号？' : '已有账号？' }}<button :disabled="busy" @click="switchMode">{{ mode === 'login' ? '注册账号' : '返回登录' }}</button></p>
      </section>
    </main>
    <footer class="auth-footer">知识练习与复习系统</footer>
  </div>
</template>

<style scoped>
.auth-page { min-height: 100dvh; display: flex; flex-direction: column; max-width: 1320px; margin: auto; padding: 38px 56px 24px; }
.auth-brand { padding: 0; }
.auth-content { flex: 1; display: grid; grid-template-columns: 1fr 440px; align-items: center; gap: 80px; width: 100%; max-width: 1000px; margin: 48px auto; }
.intro-label, .intro-note { color: var(--muted); font-size: 12px; letter-spacing: .14em; }
.auth-intro h2 { font-size: clamp(28px, 3.2vw, 40px); line-height: 1.65; letter-spacing: .04em; font-weight: 500; margin: 23px 0; }
.auth-intro p { color: var(--muted); font-size: 14px; line-height: 2; }
.intro-line { width: 44px; height: 1px; background: #b5c2b0; margin: 34px 0 22px; }
.auth-card { padding: 38px 40px 28px; border: 1px solid #e7ece2; border-radius: 26px; background: #fefefd; box-shadow: 0 12px 48px #41533b05; }
.auth-heading h1 { font-size: 27px; }
.auth-heading p { color: var(--muted); font-size: 13px; line-height: 1.8; margin: 12px 0 0; }
.auth-heading .card-eyebrow { font-size: 11px; letter-spacing: .12em; margin: 0 0 14px; }
.auth-form { display: flex; flex-direction: column; margin-top: 24px; }
.auth-form label { font-size: 13px; margin: 18px 0 10px; }
.auth-form label:first-child { margin-top: 0; }
.auth-form input { width: 100%; min-width: 0; font: inherit; font-size: 14px; color: inherit; border: 1px solid #dfe5d9; border-radius: 12px; background: #fbfcf9; padding: 14px; }
.auth-form input::placeholder { color: #8a948d; font-size: 13px; }
.auth-form input:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.auth-form small { color: var(--muted); font-size: 11px; line-height: 1.8; margin-top: 7px; }
.auth-submit { display: flex; justify-content: space-between; align-items: center; border: 0; border-radius: 12px; padding: 14px 18px; background: #526f57; color: white; font-size: 14px; margin-top: 28px; }
.auth-submit:hover:not(:disabled) { background: #435f48; }
.auth-switch { text-align: center; color: var(--muted); font-size: 12px; margin: 24px 0 0; }
.auth-switch button { background: transparent; border: 0; color: var(--accent); padding: 8px; font-size: 12px; }
.auth-error, .auth-notice { font-size: 12px; line-height: 1.8; border-radius: 10px; padding: 12px 14px; margin: 18px 0 0; }
.auth-error { color: #89533a; background: #fbf1eb; }
.auth-notice { color: var(--accent); background: #eef3e9; }
.auth-footer { color: var(--muted); font-size: 11px; text-align: center; }
@media (max-width: 900px) { .auth-page { padding: 28px 32px 22px; } .auth-content { gap: 36px; grid-template-columns: 1fr 400px; } .auth-card { padding: 32px; } }
@media (max-width: 700px) { .auth-page { padding: 24px 20px; } .auth-intro { display: none; } .auth-content { display: block; max-width: 420px; margin: 40px auto; } .auth-card { padding: 30px 26px; border-radius: 22px; } .auth-brand small { display: block; } }
</style>
