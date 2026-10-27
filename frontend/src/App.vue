<script setup lang="ts">
import { onMounted, ref } from 'vue'

type Domain = { code: string; name: string }
const loading = ref(false)
const healthy = ref(false)
const ready = ref(false)
const domains = ref<Domain[]>([])
const message = ref('正在检查本地开发环境…')
const checkedAt = ref('尚未检查')

async function checkConnection() {
  loading.value = true
  healthy.value = false
  ready.value = false
  domains.value = []
  message.value = '正在检查本地开发环境…'
  try {
    const health = await fetch('/api/health', { signal: AbortSignal.timeout(8000) })
    healthy.value = health.ok && (await health.json()).status === 'ok'
    if (!healthy.value) throw new Error('后端尚未就绪，请检查服务是否启动。')
    const readiness = await fetch('/api/ready', { signal: AbortSignal.timeout(8000) })
    if (!readiness.ok) throw new Error('后端已启动，数据库或表结构尚未就绪，请检查配置并执行迁移。')
    const response = await fetch('/api/v1/domains', { signal: AbortSignal.timeout(8000) })
    if (!response.ok) throw new Error('暂时无法读取学习领域，请重新检查连接。')
    domains.value = await response.json() as Domain[]
    ready.value = true
    message.value = '已从 MySQL 读取学习领域，前后端联通完成。'
  } catch (error) {
    message.value = error instanceof Error && !['TypeError', 'TimeoutError'].includes(error.name)
      ? error.message : '连接失败或超时，请确认本地前后端服务已启动。'
  } finally {
    loading.value = false
    checkedAt.value = new Date().toLocaleTimeString('zh-CN')
  }
}

onMounted(checkConnection)
</script>

<template>
  <main>
    <header>
      <span class="eyebrow">LEARNING PLATFORM · 开发环境</span>
      <h1>让每一次练习，<br />成为下一次进步的起点。</h1>
      <p>知识练习与复习系统</p>
    </header>
    <section class="panel" aria-labelledby="connection-title">
      <div class="heading">
        <div><span class="eyebrow">第一步 · 基础联通</span><h2 id="connection-title">环境检查</h2></div>
        <button :disabled="loading" @click="checkConnection">{{ loading ? '检查中…' : '重新检查' }}</button>
      </div>
      <div class="checks">
        <div><span>前端页面</span><strong class="ok">已加载</strong><small>Vue 3 + TypeScript</small></div>
        <div><span>业务服务</span><strong :class="{ ok: healthy }">{{ loading && !healthy ? '检查中' : healthy ? '已连接' : '未连接' }}</strong><small>FastAPI</small></div>
        <div><span>数据与结构</span><strong :class="{ ok: ready }">{{ loading ? '检查中' : ready ? '已就绪' : '未就绪' }}</strong><small>MySQL + Alembic</small></div>
      </div>
      <p class="message" role="status" aria-live="polite">{{ message }}</p>
      <div v-if="ready" class="domains"><span>已加载领域</span><b v-for="domain in domains" :key="domain.code">{{ domain.name }}</b></div>
      <footer>最近检查：{{ checkedAt }} · 当前仅验证基础联通，练习功能即将接入</footer>
    </section>
    <p class="next">下一步：发布一道题 → 完成作答 → 记录打卡 → 安排复习</p>
  </main>
</template>
