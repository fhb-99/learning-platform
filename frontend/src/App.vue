<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import DailyPractice from './DailyPractice.vue'

type Domain = { code: string; name: string }
const loading = ref(false)
const healthy = ref(false)
const ready = ref(false)
const domains = ref<Domain[]>([])
const selectedCode = ref('')
const selectedDomain = computed(() => domains.value.find(domain => domain.code === selectedCode.value))
const message = ref('正在连接服务…')
const checkedAt = ref('尚未检查')
const serviceDetails = ref<HTMLDetailsElement | null>(null)
const submittingAnswer = ref(false)
const sections = [
  { id: 'practice', name: '每日练习', intro: '从感兴趣的领域开始，专注于今天的学习。', title: '每日题正在准备', description: '后续将按学习方向提供每日题。当前可浏览知识领域，答题功能尚未开放。', icon: 'M5 4h10l4 4v12H5z M14 4v5h5 M9 13h6 M9 17h4' },
  { id: 'review', name: '个人复习', intro: '留一点时间，回顾已经学过的知识。', title: '复习列表正在准备', description: '作答后已保存下次复习日期。复习队列与到期重答页面将在后续接入。', icon: 'M5 8a8 8 0 1 1-1 7 M5 3v5h5 M12 8v5l3 2' },
  { id: 'history', name: '学习记录', intro: '在这里回看每一次练习与复习。', title: '历史列表正在准备', description: '作答结果已保存，可在每日练习中恢复查看。完整历史列表将在后续开放。', icon: 'M5 4h14v16H5z M9 8h6 M9 12h6 M9 16h4' },
] as const
const activeSection = ref<(typeof sections)[number]['id']>('practice')
const currentSection = computed(() => sections.find(section => section.id === activeSection.value)!)

function describeDomain(code: string) {
  if (code === 'computer') return '编程语言、计算机基础与工程实践'
  if (code === 'english') return '词汇、语法与阅读理解'
  return '浏览本领域的练习内容'
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
  <a class="skip-link" href="#main-content">跳转到主要内容</a>
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
            <button :class="{ active: activeSection === section.id }" :aria-current="activeSection === section.id ? 'page' : undefined" @click="activeSection = section.id">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="section.icon" /></svg>
              <span>{{ section.name }}</span><span v-if="activeSection === section.id" class="nav-indicator" aria-hidden="true"></span>
            </button>
          </li>
        </ul>
      </nav>
      <div class="sidebar-bottom"><span class="preview-dot" aria-hidden="true"></span><span>本地预览<small>功能逐步开放中</small></span></div>
    </aside>

    <main id="main-content">
      <div class="topbar"><span>学习空间 <span class="breadcrumb-divider">/</span> {{ currentSection.name }}</span><a href="#service-status" @click="serviceDetails && (serviceDetails.open = true)"><span class="status-dot" :class="{ online: ready, pending: loading }" aria-hidden="true"></span>服务状态</a></div>
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
              <DailyPractice v-show="activeSection === 'practice'" :domain-code="selectedCode" @busy="submittingAnswer = $event" />
              <template v-if="activeSection !== 'practice'">
              <div class="content-heading"><div><h2>{{ selectedDomain.name }}</h2><p>{{ describeDomain(selectedDomain.code) }}</p></div><span class="quiet-badge">建设中</span></div>
              <div class="empty-state">
                <span class="empty-illustration"><svg viewBox="0 0 64 64" fill="none" aria-hidden="true"><path d="M13 17h13a10 10 0 0 1 9 5v28a13 13 0 0 0-9-4H13V17Z"/><path d="M35 22a10 10 0 0 1 9-5h7v29h-7a13 13 0 0 0-9 4"/><path d="M21 26h6m-6 7h6m15-7h2m-2 7h2"/></svg></span>
                <h3>{{ currentSection.title }}</h3><p>{{ currentSection.description }}</p>
              </div>
              </template>
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
        <footer class="page-footer">知识练习与复习系统</footer>
      </div>
    </main>
  </div>
</template>
