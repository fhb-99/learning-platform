<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import DailyPractice from './DailyPractice.vue'
import ReviewPractice from './ReviewPractice.vue'
import LearningHistory from './LearningHistory.vue'
import QuestionAdmin from './QuestionAdmin.vue'
import { account, api, errorMessage } from './session'

const loggingOut = ref(false)
const logoutError = ref('')

async function logout() {
  if (submittingAnswer.value || loggingOut.value) return
  loggingOut.value = true
  logoutError.value = ''
  try {
    await api('/auth/logout', { method: 'POST' })
    account.value = null
  } catch (cause) { logoutError.value = errorMessage(cause) }
  finally { loggingOut.value = false }
}

type Domain = { code: string; name: string }
const loading = ref(false)
const healthy = ref(false)
const ready = ref(false)
const domains = ref<Domain[]>([])
const initialView = new URLSearchParams(location.hash.slice(1))
const selectedCode = ref(initialView.get('domain') ?? '')
const selectedDomain = computed(() => domains.value.find(domain => domain.code === selectedCode.value))
const message = ref('正在连接服务…')
const checkedAt = ref('尚未检查')
const serviceDetails = ref<HTMLDetailsElement | null>(null)
const submittingAnswer = ref(false)
const sections = computed(() => [
  { id: 'practice', name: '每日练习', intro: '从感兴趣的领域开始，专注于今天的学习。', icon: 'M5 4h10l4 4v12H5z M14 4v5h5 M9 13h6 M9 17h4' },
  { id: 'review', name: '个人复习', intro: '留一点时间，回顾已经学过的知识。', icon: 'M5 8a8 8 0 1 1-1 7 M5 3v5h5 M12 8v5l3 2' },
  { id: 'history', name: '学习记录', intro: '在这里回看每一次练习与复习。', icon: 'M5 4h14v16H5z M9 8h6 M9 12h6 M9 16h4' },
  ...(account.value?.is_admin ? [{ id: 'admin', name: '题库管理', intro: '整理题目，审核内容，发布新的版本。', icon: 'M5 4h14v16H5z M8 8h8 M8 12h8 M8 16h4' }] : []),
])
const savedView = initialView.get('view')
const activeSection = ref(sections.value.some(section => section.id === savedView) ? savedView! : 'practice')
watch([activeSection, selectedCode], () => {
  const params = new URLSearchParams({ view: activeSection.value, domain: selectedCode.value })
  history.replaceState(null, '', `#${params}`)
})
const currentSection = computed(() => sections.value.find(section => section.id === activeSection.value)!)

function showServiceDetails() {
  if (!serviceDetails.value) return
  serviceDetails.value.open = true
  serviceDetails.value.scrollIntoView({ block: 'nearest' })
}

function focusMain() {
  document.getElementById('main-content')?.focus()
}

async function checkConnection() {
  if (loading.value) return
  loading.value = true
  healthy.value = false
  ready.value = false
  domains.value = []
  message.value = '正在连接服务…'
  try {
    const health = await fetch('/api/health', { signal: AbortSignal.timeout(8000) })
    healthy.value = health.ok && (await health.json()).status === 'ok'
    if (!healthy.value) throw new Error('服务暂时不可用，请稍后重试。')
    const readiness = await fetch('/api/ready', { signal: AbortSignal.timeout(8000) })
    if (!readiness.ok) throw new Error('数据服务尚未就绪，请稍后重试。')
    const response = await fetch('/api/v1/domains', { signal: AbortSignal.timeout(8000) })
    if (!response.ok) throw new Error('暂时无法读取学习领域，请稍后重试。')
    domains.value = await response.json() as Domain[]
    // 刷新后保留当前领域；数据变化时回到第一个可用领域。
    if (!domains.value.some(domain => domain.code === selectedCode.value)) {
      selectedCode.value = domains.value[0]?.code ?? ''
    }
    ready.value = true
    message.value = '服务连接正常，学习领域已更新。'
  } catch (error) {
    message.value = error instanceof Error && !['TypeError', 'TimeoutError'].includes(error.name)
      ? error.message : '连接失败或超时，请稍后重试。'
  } finally {
    loading.value = false
    checkedAt.value = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  }
}

onMounted(checkConnection)
</script>

<template>
  <a class="skip-link" href="#main-content" @click.prevent="focusMain">跳转到主要内容</a>
  <div class="app-shell">
    <aside class="app-sidebar">
      <a class="brand" href="/" aria-label="知识练习首页">
        <span class="brand-mark" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M4 5h5a4 4 0 0 1 3 2 4 4 0 0 1 3-2h5v14h-5a4 4 0 0 0-3 1 4 4 0 0 0-3-1H4z M12 7v13" /></svg></span>
        <span>知识练习<small>学习与复习空间</small></span>
      </a>
      <nav class="feature-nav" aria-label="功能导航">
        <p class="nav-label">学习</p>
        <ul>
          <li v-for="section in sections" :key="section.id">
            <button :disabled="submittingAnswer" :class="{ active: activeSection === section.id }" :aria-current="activeSection === section.id ? 'page' : undefined" @click="activeSection = section.id">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="section.icon" /></svg>
              <span>{{ section.name }}</span><span v-if="activeSection === section.id" class="nav-indicator" aria-hidden="true"></span>
            </button>
          </li>
        </ul>
      </nav>
      <div class="user-account" v-if="account"><span class="account-avatar" aria-hidden="true">{{ account.username[0]?.toUpperCase() }}</span><div><strong>{{ account.username }}</strong><small>我的学习账号</small></div><button :disabled="submittingAnswer || loggingOut" @click="logout">{{ loggingOut ? '退出中…' : '退出登录' }}</button><p v-if="logoutError" role="alert">{{ logoutError }}</p></div>
    </aside>

    <main id="main-content" tabindex="-1">
      <div class="topbar"><span>学习空间 <span class="breadcrumb-divider">/</span> {{ currentSection.name }}</span><a href="#service-status" @click.prevent="showServiceDetails"><span class="status-dot" :class="{ online: ready, pending: loading }" aria-hidden="true"></span>服务状态</a></div>
      <div class="page-content">
        <header class="page-heading"><h1>{{ currentSection.name }}</h1><p class="intro">{{ currentSection.intro }}</p></header>

        <section class="workspace" :aria-label="currentSection.name + '内容'" :aria-busy="loading">
          <div class="domain-toolbar">
            <span class="section-label">知识领域</span>
            <div v-if="loading" class="toolbar-message">正在加载…</div>
            <div v-else-if="!ready" class="toolbar-message">暂不可用</div>
            <div v-else-if="!domains.length" class="toolbar-message">暂无领域</div>
            <div v-else class="domain-list" aria-label="选择知识领域">
              <button v-for="domain in domains" :key="domain.code" :class="{ selected: selectedCode === domain.code }" :aria-pressed="selectedCode === domain.code" :disabled="submittingAnswer" @click="selectedCode = domain.code">{{ domain.name }}</button>
            </div>
          </div>
          <div class="content-pane">
            <template v-if="ready && selectedDomain">
              <DailyPractice v-if="activeSection === 'practice'" :domain-code="selectedCode" @busy="submittingAnswer = $event" />
              <ReviewPractice v-else-if="activeSection === 'review'" :domain-code="selectedCode" @busy="submittingAnswer = $event" />
              <LearningHistory v-else-if="activeSection === 'history'" :domain-code="selectedCode" />
              <QuestionAdmin v-else-if="activeSection === 'admin' && account?.is_admin" :domain-code="selectedCode" @busy="submittingAnswer = $event" />
            </template>
            <div v-else class="empty-state connection-state">
              <span class="state-symbol" aria-hidden="true">{{ loading ? '…' : ready ? '＋' : '!' }}</span>
              <h2>{{ loading ? '正在加载学习空间' : ready ? '还没有知识领域' : '暂时无法加载' }}</h2>
              <p>{{ loading ? '请稍候，正在获取领域信息。' : ready ? '添加领域后，会在这里显示。' : message }}</p>
              <button v-if="!loading" class="secondary-button" @click="checkConnection">重新加载</button>
            </div>
          </div>
        </section>

        <details id="service-status" ref="serviceDetails" class="service-details">
          <summary><span class="status-dot" :class="{ online: ready, pending: loading }" aria-hidden="true"></span><span>服务状态</span><span class="status-summary">{{ loading ? '检查中' : ready ? '连接正常' : '连接异常' }}</span><span class="disclosure-arrow" aria-hidden="true">⌄</span></summary>
          <div class="service-content">
            <div class="service-heading"><p role="status">{{ message }}</p><button class="secondary-button" :disabled="loading || submittingAnswer" @click="checkConnection">{{ loading ? '检查中…' : '重新检查' }}</button></div>
            <dl class="service-list"><div><dt>前端页面</dt><dd>已加载</dd></div><div><dt>业务服务</dt><dd>{{ loading ? '检查中' : healthy ? '已连接' : '未连接' }}</dd></div><div><dt>数据服务</dt><dd>{{ loading ? '检查中' : ready ? '已就绪' : '未就绪' }}</dd></div></dl>
            <p class="checked-at">最近检查 {{ checkedAt }}</p>
          </div>
        </details>
        <footer class="page-footer">知识练习与复习系统<span v-if="account"> · 日期按 {{ account.timezone }} 计算</span></footer>
      </div>
    </main>
  </div>
</template>

<style scoped>
.user-account { margin-top: auto; padding: 24px 12px 0; display: flex; flex-wrap: wrap; gap: 10px; align-items: center; font-size: 12px; }
.account-avatar { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 11px; background: #e0e8db; color: var(--accent); }
.user-account > div { min-width: 0; flex: 1; }
.user-account strong { display: block; font-weight: 500; overflow-wrap: anywhere; }
.user-account small { display: block; color: var(--muted); font-size: 10px; margin-top: 4px; }
.user-account button { border: 0; background: transparent; color: var(--accent); font-size: 11px; padding: 8px 0; }
.user-account p { color: #89533a; font-size: 12px; line-height: 1.8; margin: 0; }
@media (max-width: 480px) { .user-account { padding: 18px 4px 0; } .user-account small { display: none; } }
</style>
