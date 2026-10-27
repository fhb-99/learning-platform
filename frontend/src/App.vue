<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

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
  <header class="site-header">
    <div class="header-inner">
      <a class="brand" href="/" aria-label="知识练习首页">
        <span class="brand-mark" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M5 5h6v14H5zM14 5h5v14h-5" /></svg></span>
        <span>知识练习</span>
      </a>
      <span class="header-divider" aria-hidden="true"></span>
      <span class="current-page">学习空间</span>
      <span class="preview-label">本地预览</span>
    </div>
  </header>

  <main id="main-content">
    <div class="page-heading">
      <div><p class="eyebrow">学习空间</p><h1>从一个领域开始</h1><p class="intro">选择你关注的知识领域，查看练习内容。</p></div>
      <a class="text-link" href="#service-status" @click="serviceDetails && (serviceDetails.open = true)">服务状态 <span aria-hidden="true">↗</span></a>
    </div>

    <section class="workspace" aria-label="学习领域与内容" :aria-busy="loading">
      <aside class="domain-sidebar" aria-labelledby="domain-heading">
        <div class="section-label"><h2 id="domain-heading">知识领域</h2><span>{{ ready ? String(domains.length).padStart(2, '0') : '—' }}</span></div>
        <div v-if="loading" class="sidebar-message">正在加载领域…</div>
        <div v-else-if="!ready" class="sidebar-message">领域暂不可用</div>
        <div v-else-if="!domains.length" class="sidebar-message">暂无知识领域</div>
        <div v-else class="domain-list" aria-label="选择知识领域">
          <button v-for="domain in domains" :key="domain.code" class="domain-button" :class="{ selected: selectedCode === domain.code }" :aria-pressed="selectedCode === domain.code" @click="selectedCode = domain.code">
            <span class="domain-icon" aria-hidden="true">{{ domain.code === 'computer' ? '⌘' : domain.code === 'english' ? 'Aa' : '文' }}</span>
            <span>{{ domain.name }}</span><span class="domain-arrow" aria-hidden="true">→</span>
          </button>
        </div>
        <p class="sidebar-note">先确定领域，再循序练习。</p>
      </aside>

      <div class="content-pane" aria-live="polite">
        <template v-if="ready && selectedDomain">
          <div class="content-heading"><div><h2>{{ selectedDomain.name }}</h2><p>{{ describeDomain(selectedDomain.code) }}</p></div><span class="quiet-badge">尚未开放练习</span></div>
          <div class="empty-state">
            <svg class="empty-icon" viewBox="0 0 64 64" fill="none" aria-hidden="true"><path d="M13 15h15a7 7 0 0 1 7 7v29a12 12 0 0 0-9-4H13V15Z"/><path d="M35 22a7 7 0 0 1 7-7h9v32h-7a12 12 0 0 0-9 4"/><path d="M20 24h7m-7 7h7m15-7h3m-3 7h3"/></svg>
            <h3>练习内容正在准备</h3>
            <p>后续将在这里提供每日题与个人复习。<br />当前可以浏览领域，答题功能尚未接入。</p>
          </div>
          <div class="feature-note"><span class="small-label">后续功能</span><span>每日一题</span><span class="dot-separator" aria-hidden="true">·</span><span>个人复习</span><span class="dot-separator" aria-hidden="true">·</span><span>学习记录</span></div>
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
        <div class="service-heading"><p role="status">{{ message }}</p><button class="secondary-button" :disabled="loading" @click="checkConnection">{{ loading ? '检查中…' : '重新检查' }}</button></div>
        <dl class="service-list"><div><dt>前端页面</dt><dd>已加载</dd></div><div><dt>业务服务</dt><dd>{{ loading ? '检查中' : healthy ? '已连接' : '未连接' }}</dd></div><div><dt>数据服务</dt><dd>{{ loading ? '检查中' : ready ? '已就绪' : '未就绪' }}</dd></div></dl>
        <p class="checked-at">最近检查 {{ checkedAt }}</p>
      </div>
    </details>
    <footer class="page-footer"><span>知识练习与复习系统</span><span>开发预览 · 功能持续建设中</span></footer>
  </main>
</template>
