<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { account, api, errorMessage } from './session'

type Metrics = {
  attempts: number; active_days: number; correct: number; daily_attempts: number
  review_attempts: number; on_time_reviews: number; late_reviews: number
  learning_frequency: number; average_daily_attempts: number; accuracy: number | null
  completed_review_on_time_rate: number | null
}
type Summary = {
  snapshot_at_utc: string; timezone: string; start_date: string; end_date: string
  calendar_days: number; includes_today: boolean; summary: Metrics
  by_source: (Metrics & { source: string })[]
  by_domain: (Metrics & { domain_code: string })[]
  daily: (Metrics & { date: string })[]
}
const props = defineProps<{ domains: { code: string; name: string }[] }>()
const today = new Intl.DateTimeFormat('en-CA', {
  timeZone: account.value?.timezone ?? 'Asia/Singapore', year: 'numeric', month: '2-digit', day: '2-digit',
}).format(new Date())
const start = ref(new Date(Date.parse(today) - 29 * 86400000).toISOString().slice(0, 10))
const end = ref(today)
const report = ref<Summary | null>(null)
const loading = ref(false)
const error = ref('')
let generation = 0
const rangeError = computed(() => {
  const days = (Date.parse(end.value) - Date.parse(start.value)) / 86400000
  return !Number.isFinite(days) || days < 0 || days >= 366 || end.value > today
    ? '请选择不超过今天、长度为 1 至 366 天的日期范围。' : ''
})
const maxAttempts = computed(() => Math.max(1, ...report.value?.daily.map(day => day.attempts) ?? []))
const snapshotTime = computed(() => report.value ? new Date(report.value.snapshot_at_utc).toLocaleString('zh-CN', { timeZone: report.value.timezone, hour12: false }) : '')
const formatRate = (value: number | null) => value === null ? '暂无数据' : `${value}%`
const domainName = (code: string) => props.domains.find(domain => domain.code === code)?.name ?? code

async function load() {
  if (loading.value || rangeError.value) return
  const version = ++generation
  const requestAccount = account.value
  loading.value = true
  error.value = ''
  report.value = null
  const params = new URLSearchParams({ start_date: start.value, end_date: end.value })
  try {
    const data = await api<Summary>(`/analytics/me?${params}`, {}, 60000)
    // 离开页面或账号变化后，不展示旧请求返回的个人统计。
    if (version === generation && account.value === requestAccount) report.value = data
  } catch (cause) { if (version === generation) error.value = errorMessage(cause) }
  finally { if (version === generation) loading.value = false }
}
onMounted(load)
onBeforeUnmount(() => { ++generation })
</script>

<template>
  <div class="practice-panel summary-panel" :aria-busy="loading">
    <p class="scope-label">仅我的数据 · 所有学习领域</p>
    <form class="summary-filters" @submit.prevent="load">
      <label>开始日期<input v-model="start" type="date" required :max="end || today" :disabled="loading" /></label>
      <label>结束日期<input v-model="end" type="date" required :min="start || undefined" :max="today" :disabled="loading" /></label>
      <button class="primary-button" :disabled="loading || !!rangeError">{{ loading ? '正在核对统计…' : '查看汇总' }}</button>
    </form>
    <p v-if="rangeError" class="practice-error" role="alert">{{ rangeError }}</p>
    <p v-if="loading" class="practice-message" role="status">正在核对所选范围内的学习记录，请稍候…</p>
    <p v-if="error" class="practice-error" role="alert">{{ error }}</p>
    <template v-if="report">
      <div class="report-caption"><span>{{ report.start_date }} — {{ report.end_date }} · {{ report.calendar_days }} 天</span><span class="verified">已核对原始记录</span></div>
      <p class="snapshot">统计截止 {{ snapshotTime }}（{{ report.timezone }}）<span v-if="report.includes_today">；今天尚未结束，数据会继续变化。</span></p>
      <p v-if="!report.summary.attempts" class="empty-review">这个日期范围内还没有正式作答。学习频率为 0%，正确率与复习按期率暂无数据。</p>
      <div class="metric-grid">
        <article><h2>学习频率</h2><strong>{{ formatRate(report.summary.learning_frequency) }}</strong><p>{{ report.summary.active_days }} 个学习日 / {{ report.calendar_days }} 个日历日</p><small>日均 {{ report.summary.average_daily_attempts }} 次作答</small></article>
        <article><h2>作答正确率</h2><strong>{{ formatRate(report.summary.accuracy) }}</strong><p>{{ report.summary.correct }} 次正确 / {{ report.summary.attempts }} 次正式作答</p><small>每日练习 {{ report.summary.daily_attempts }} 次 · 复习 {{ report.summary.review_attempts }} 次</small></article>
        <article><h2>已完成复习按期率</h2><strong>{{ formatRate(report.summary.completed_review_on_time_rate) }}</strong><p>{{ report.summary.on_time_reviews }} 次按期 / {{ report.summary.review_attempts }} 次已完成复习</p><small>其中逾期 {{ report.summary.late_reviews }} 次；不代表到期任务完成率</small></article>
      </div>
      <section class="summary-section"><h2>每天的作答量</h2><div class="bar-scroll"><div class="daily-bars" role="img" :aria-label="`${report.calendar_days} 天的作答量趋势，具体数字见下方逐日明细`"><div v-for="day in report.daily" :key="day.date" class="bar-column" :title="`${day.date}：${day.attempts} 次作答`"><span :style="{ height: `${day.attempts ? Math.max(3, day.attempts / maxAttempts * 100) : 0}%` }"></span></div></div></div><div class="axis"><span>{{ report.start_date }}</span><span>{{ report.end_date }}</span></div></section>
      <section class="summary-section"><h2>按练习类型核对</h2><div class="table-scroll"><table><thead><tr><th>类型</th><th>作答次数</th><th>正确次数</th><th>正确率</th></tr></thead><tbody><tr v-for="item in report.by_source" :key="item.source"><th>{{ item.source === 'daily' ? '每日练习' : '个人复习' }}</th><td>{{ item.attempts }}</td><td>{{ item.correct }}</td><td>{{ formatRate(item.accuracy) }}</td></tr></tbody></table></div></section>
      <section v-if="report.by_domain.length" class="summary-section"><h2>按学习领域核对</h2><div class="table-scroll"><table><thead><tr><th>领域</th><th>学习天数</th><th>作答次数</th><th>正确率</th><th>复习按期 / 已完成</th></tr></thead><tbody><tr v-for="item in report.by_domain" :key="item.domain_code"><th>{{ domainName(item.domain_code) }}</th><td>{{ item.active_days }}</td><td>{{ item.attempts }}</td><td>{{ formatRate(item.accuracy) }}</td><td>{{ item.on_time_reviews }} / {{ item.review_attempts }}</td></tr></tbody></table></div><p class="snapshot">同一天学习多个领域，整体学习天数只计 1 天，不能直接相加各领域天数。</p></section>
      <details class="summary-details"><summary>逐日明细（包含没有作答的日期）</summary><div class="table-scroll daily-table"><table><thead><tr><th>日期</th><th>每日题 / 复习</th><th>正确 / 作答</th><th>按期 / 逾期复习</th></tr></thead><tbody><tr v-for="day in report.daily" :key="day.date"><th>{{ day.date }}</th><td>{{ day.daily_attempts }} / {{ day.review_attempts }}</td><td>{{ day.correct }} / {{ day.attempts }}</td><td>{{ day.on_time_reviews }} / {{ day.late_reviews }}</td></tr></tbody></table></div></details>
    </template>
    <details class="summary-details"><summary>这些数字怎么算？</summary><ul><li>日期按作答入库时记录的本地日期筛选，首尾日期均包含；每次最多 366 天、10,000 条作答。</li><li>有至少一次正式作答的日期算一个学习日。学习频率的分母是所选全部日历天数，包含休息日；不统计登录次数或学习时长。</li><li>当前仅统计已判分的单选题。一次正式提交计一次，提交重试不重复计数；复习同一题的多次合法作答分别计数，不按题目去重，也不按领域正确率取平均。</li><li>复习按期率仅针对所选日期内已经完成的个人复习。实际作答日期不晚于当次原定复习日算按期；原定日期可以在范围外。未作答的计划、每日题、当前计划的新到期日均不参与该比例。</li><li>正确率或复习按期率分母为零时显示“暂无数据”；百分比保留最多两位小数。历史作答使用原成绩和版本，发布新版本不会重新判分。</li><li>页面每次查询按一个一致性快照核对记录，不自动刷新。新的提交需要重新查看汇总；修改筛选后，结果区标题仍标明上一次已查询的范围。</li></ul></details>
  </div>
</template>

<style scoped src="./practice.css"></style>
<style scoped>
.scope-label, .snapshot, .axis { font-size: 12px; color: var(--muted); line-height: 1.8; }
.scope-label { color: var(--accent); margin: 0 0 20px; }
.summary-filters { display: flex; flex-wrap: wrap; gap: 14px; align-items: end; margin-bottom: 22px; }
.summary-filters label { display: grid; gap: 8px; font-size: 12px; flex: 1; min-width: 160px; }
.summary-filters input { width: 100%; min-width: 0; border: 1px solid var(--line); border-radius: 9px; padding: 10px; background: white; color: inherit; }
.report-caption { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; font-size: 13px; }
.verified { color: var(--accent); }
.metric-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 22px; }
.metric-grid article { background: #f3f6f0; border-radius: 14px; padding: 20px 18px; }
.metric-grid h2 { font-size: 13px; font-weight: 500; margin: 0 0 18px; }
.metric-grid strong { font-size: 28px; font-weight: 500; color: var(--accent); }
.metric-grid p, .metric-grid small { display: block; font-size: 12px; line-height: 1.8; margin: 12px 0 0; color: var(--muted); }
.summary-section { margin-top: 30px; }
.summary-section h2 { font-size: 15px; font-weight: 500; margin: 0 0 16px; }
.bar-scroll, .table-scroll { overflow-x: auto; }
.daily-bars { display: flex; gap: 3px; height: 100px; align-items: end; border-bottom: 1px solid var(--line); }
.bar-column { height: 100%; min-width: 5px; flex: 1; display: flex; align-items: end; }
.bar-column span { width: 100%; background: #8da68d; border-radius: 3px 3px 0 0; }
.axis { display: flex; justify-content: space-between; margin-top: 6px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; line-height: 1.8; }
th, td { text-align: left; font-weight: 400; padding: 12px 10px; border-bottom: 1px solid var(--line); white-space: nowrap; }
thead th { color: var(--muted); }
.summary-details { border-top: 1px solid var(--line); margin-top: 25px; padding-top: 18px; font-size: 13px; line-height: 1.9; }
.summary-details summary { cursor: pointer; color: var(--accent); }
.summary-details ul { padding-left: 20px; color: var(--muted); }
.summary-details li + li { margin-top: 10px; }
.daily-table { max-height: 380px; margin-top: 14px; }
@media (max-width: 1050px) { .metric-grid { grid-template-columns: 1fr; } .metric-grid article { padding: 18px; } }
</style>
