<script setup lang="ts">
import { onMounted, ref } from 'vue'
import AuthPage from './AuthPage.vue'
import LearningSpace from './LearningSpace.vue'
import { account, api, ApiError, errorMessage, type Account } from './session'

const restoring = ref(true)
const restoreError = ref('')

async function restoreSession() {
  restoring.value = true
  restoreError.value = ''
  try { account.value = await api<Account>('/auth/me') }
  catch (cause) {
    if (!(cause instanceof ApiError && cause.status === 401)) restoreError.value = errorMessage(cause)
  } finally { restoring.value = false }
}

onMounted(restoreSession)
</script>

<template>
  <main v-if="restoring || restoreError" class="session-state" aria-label="登录状态">
    <span class="session-mark" aria-hidden="true">知</span>
    <h1>知识练习</h1>
    <p v-if="restoring" role="status">正在恢复登录状态…</p>
    <template v-else>
      <p role="alert">{{ restoreError }}</p>
      <button class="secondary-button" @click="restoreSession">重新连接</button>
    </template>
  </main>
  <LearningSpace v-else-if="account" />
  <AuthPage v-else />
</template>

<style scoped>
.session-state { min-height: 100dvh; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 24px; }
.session-mark { display: grid; place-items: center; width: 48px; height: 48px; border-radius: 17px; background: #dfe8dc; color: var(--accent); margin-bottom: 20px; }
.session-state h1 { font-size: 22px; }
.session-state p { color: var(--muted); font-size: 14px; line-height: 1.8; }
</style>
