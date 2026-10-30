<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { account, errorMessage, exportLearningData } from './session'

const emit = defineEmits<{ busy: [value: boolean] }>()
const today = new Intl.DateTimeFormat('en-CA', {
  timeZone: account.value?.timezone ?? 'Asia/Singapore', year: 'numeric', month: '2-digit', day: '2-digit',
}).format(new Date())
const start = ref(today.slice(0, 7) + '-01')
const end = ref(today)
const includeAnswers = ref(false)
const busy = ref(false)
const error = ref('')
const success = ref('')
let active = true
const rangeError = computed(() => {
  const days = (Date.parse(end.value) - Date.parse(start.value)) / 86400000
  return !Number.isFinite(days) || days < 0 || days >= 366 ? '请选择开始和结束日期，范围须为 1 至 366 天。' : ''
})

async function download() {
  if (busy.value || rangeError.value) return
  const requestAccount = account.value
  // 固定本次请求范围，等待期间不允许表单变动或切换功能。
  const exportStart = start.value
  const exportEnd = end.value
  busy.value = true
  emit('busy', true)
  error.value = ''
  success.value = ''
  try {
    const blob = await exportLearningData(exportStart, exportEnd, includeAnswers.value)
    if (!active || account.value !== requestAccount) return
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `learning-data-${exportStart}-${exportEnd}.zip`
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    success.value = `${exportStart} 至 ${exportEnd} 的文件已通过一致性核对，已开始下载。没有记录时也会生成空文件和核对报告。`
  } catch (cause) { if (active) error.value = errorMessage(cause) }
  finally {
    busy.value = false
    emit('busy', false)
  }
}

onBeforeUnmount(() => { active = false; emit('busy', false) })
</script>

<template>
  <div class="practice-panel export-panel">
    <div class="export-heading"><span class="export-label">仅我的数据 · 所有学习领域</span><h2>保存一份学习记录</h2><p>导出每日练习、个人复习、打卡及相关复习计划。日期按作答时记录的本地日期筛选，包含开始和结束当天。</p></div>
    <form @submit.prevent="download">
      <fieldset :disabled="busy">
        <legend class="export-legend">选择导出范围</legend>
        <div class="export-dates"><label>开始日期<input v-model="start" type="date" required :max="end || undefined" /></label><label>结束日期<input v-model="end" type="date" required :min="start || undefined" /></label></div>
        <p class="export-hint">每次最多 366 天、10,000 条作答；超出时请分段导出。</p>
        <label class="export-checkbox"><input v-model="includeAnswers" type="checkbox" /><span>同时包含我提交的作答选项<small>默认仅包含对错与调度依据，不包含具体选项。</small></span></label>
      </fieldset>
      <div class="export-notice"><strong>下载前请了解</strong><ul><li>复习计划是导出时的当前状态，不是所选结束日期当天的历史计划。</li><li>文件不含用户名、账号编号、密码、会话或题目标准答案；日期和学习轨迹仍属于个人数据，请妥善保存。</li><li>不会导出点击、浏览或停留时长，这些操作日志目前没有采集。</li></ul></div>
      <p v-if="rangeError" class="practice-error" role="alert">{{ rangeError }}</p>
      <button class="primary-button" :disabled="busy || !!rangeError">{{ busy ? '正在导出并核对…' : '核对并下载' }}</button>
      <p class="export-hint">下载 ZIP，包含 Parquet 数据文件、范围说明和核对报告。记录编号仅在本次导出中可关联。</p>
    </form>
    <p v-if="error" class="practice-error" role="alert">{{ error }}</p>
    <p v-if="success" class="export-success" role="status">{{ success }}</p>
  </div>
</template>

<style src="./practice.css" scoped></style>
<style scoped>
.export-panel { max-width: 780px; }
.export-heading h2 { font-size: 23px; font-weight: 500; margin: 14px 0 12px; }
.export-heading p, .export-notice { font-size: 13px; line-height: 1.9; color: var(--muted); }
.export-label { color: var(--accent); font-size: 12px; }
fieldset { border: 0; padding: 20px 0 0; margin: 0; min-width: 0; }
.export-legend { font-size: 14px; padding: 0; }
.export-dates { display: flex; gap: 20px; flex-wrap: wrap; }
.export-dates label { display: grid; gap: 9px; font-size: 12px; flex: 1; min-width: 170px; }
.export-dates input { width: 100%; min-width: 0; border: 1px solid var(--line); border-radius: 9px; padding: 11px; background: white; color: inherit; }
.export-hint { font-size: 12px; line-height: 1.8; color: var(--muted); margin: 12px 0 20px; }
.export-checkbox { display: flex; gap: 10px; align-items: flex-start; font-size: 13px; line-height: 1.7; }
.export-checkbox input { accent-color: var(--accent); margin: 4px 0 0; }
.export-checkbox small { display: block; color: var(--muted); font-size: 12px; margin-top: 3px; }
.export-notice { background: #f3f6f0; padding: 18px 22px; border-radius: 12px; margin: 24px 0; }
.export-notice strong { font-size: 13px; color: #35433e; font-weight: 500; }
.export-notice ul { margin: 8px 0 0; padding-left: 18px; }
.export-notice li + li { margin-top: 6px; }
.export-success { padding: 14px 18px; border-radius: 10px; background: #edf3e8; color: var(--accent); font-size: 13px; line-height: 1.8; }
</style>
