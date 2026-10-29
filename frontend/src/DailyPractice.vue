<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { account, api, errorMessage } from './session'
type Track = { id: number; name: string }
type Result = {
  id: number; answer: string; score: number; correct_answer: string; explanation: string
  check_in_date: string; review_due_date: string; review_reason: string
}
type Daily = {
  date: string; track_id: number
  assignment: {
    id: number; question_version_id: number; stem: string
    options: { id: string; text: string }[]; source: string; result: Result | null
  } | null
}
const props = defineProps<{ domainCode: string }>()
const emit = defineEmits<{ busy: [value: boolean] }>()
const loading = ref(false)
const submitting = ref(false)
const error = ref('')
const tracks = ref<Track[]>([])
const trackId = ref(0)
const daily = ref<Daily | null>(null)
const answer = ref('')
const submissionKey = ref('')
const pendingAnswer = ref('')
let loadVersion = 0
let disposed = false

function showError(cause: unknown) {
  error.value = errorMessage(cause)
}

async function loadDaily(refreshTracks = false) {
  if (!account.value || submitting.value) return
  const version = ++loadVersion
  loading.value = true
  daily.value = null
  answer.value = ''
  pendingAnswer.value = ''
  error.value = ''
  try {
    if (refreshTracks) {
      const data = await api<Track[]>(`/tracks?domain_code=${encodeURIComponent(props.domainCode)}`)
      if (version !== loadVersion || disposed) return
      tracks.value = data
      trackId.value = data[0]?.id ?? 0
    }
    if (!trackId.value) return
    const data = await api<Daily>(`/today?track_id=${trackId.value}`)
    if (version !== loadVersion || disposed) return
    daily.value = data
    answer.value = data.assignment?.result?.answer ?? ''
    submissionKey.value = crypto.randomUUID()
  } catch (cause) {
    if (version === loadVersion && !disposed) showError(cause)
  } finally {
    if (version === loadVersion && !disposed) loading.value = false
  }
}

async function submitAnswer() {
  const assignment = daily.value?.assignment
  if (!assignment || assignment.result || !answer.value || submitting.value) return
  submitting.value = true
  emit('busy', true)
  error.value = ''
  // 请求结果不确定时锁定原答案与幂等键；重试不能把另一答案当成同一次提交。
  pendingAnswer.value = pendingAnswer.value || answer.value
  try {
    const result = await api<Result>('/attempts', {
      method: 'POST', body: JSON.stringify({
        daily_assignment_id: assignment.id, question_version_id: assignment.question_version_id,
        answer: pendingAnswer.value, idempotency_key: submissionKey.value,
      }),
    })
    assignment.result = result
    answer.value = result.answer
    pendingAnswer.value = ''
  } catch (cause) { showError(cause) }
  finally { submitting.value = false; emit('busy', false) }
}

watch(() => props.domainCode, () => { void loadDaily(true) })
onMounted(() => loadDaily(true))
onBeforeUnmount(() => { disposed = true; ++loadVersion })
</script>

<template>
  <div class="practice-panel">
    <div class="practice-toolbar">
      <label for="practice-track">学习方向<select id="practice-track" v-model="trackId" :disabled="loading || submitting || !tracks.length" @change="loadDaily()"><option v-for="track in tracks" :key="track.id" :value="track.id">{{ track.name }}</option></select></label>
      <button class="secondary-button" :disabled="loading || submitting" @click="loadDaily(!tracks.length)">重新加载</button>
    </div>
    <p v-if="loading" class="practice-message" role="status">正在获取今日题目…</p>
    <p v-else-if="!tracks.length" class="practice-message">该领域尚未配置学习方向。</p>
    <p v-else-if="daily && !daily.assignment" class="practice-message">今天还没有可用的已发布题目，请稍后再来。</p>
    <form v-else-if="daily?.assignment" class="question-form" @submit.prevent="submitAnswer">
      <div class="question-meta"><span>{{ daily.date }} · 每日一题</span><span>{{ daily.assignment.result ? '已完成' : '单选题' }}</span></div>
      <fieldset :disabled="submitting || !!pendingAnswer || !!daily.assignment.result">
        <legend>{{ daily.assignment.stem }}</legend>
        <label v-for="option in daily.assignment.options" :key="option.id" class="answer-option" :class="{ chosen: answer === option.id, correct: daily.assignment.result?.correct_answer === option.id, incorrect: daily.assignment.result && answer === option.id && daily.assignment.result.correct_answer !== option.id }">
          <input v-model="answer" type="radio" name="daily-answer" :value="option.id" required />
          <span class="option-key">{{ option.id }}</span><span>{{ option.text }}</span>
          <small v-if="daily.assignment.result?.correct_answer === option.id">正确答案</small>
        </label>
      </fieldset>
      <p class="question-source">{{ daily.assignment.source }}</p>
      <div v-if="!daily.assignment.result" class="submit-row"><button class="primary-button" type="submit" :disabled="!answer || submitting">{{ submitting ? '正在提交…' : pendingAnswer ? '重试原提交' : '提交答案' }}</button><span>提交后显示解析，当天结果会保留。</span></div>
      <section v-else class="answer-result" aria-label="答题结果" aria-live="polite">
        <h3>{{ daily.assignment.result.score === 100 ? '回答正确' : '这道题答错了' }}</h3>
        <p>你的答案：{{ daily.assignment.result.answer }}　正确答案：{{ daily.assignment.result.correct_answer }}</p>
        <h4>解析</h4><p>{{ daily.assignment.result.explanation }}</p>
        <div class="review-summary"><span>已计入 {{ daily.assignment.result.check_in_date }} 打卡</span><span>下次复习：{{ daily.assignment.result.review_due_date }}</span><small>{{ daily.assignment.result.review_reason }}。这是初始规则，后续通过实验评估。</small></div>
      </section>
    </form>
    <p v-if="error" class="practice-error" role="alert">{{ error }}<span v-if="pendingAnswer"> 如提交结果不确定，可重试原提交，或重新加载以恢复已保存的结果。</span></p>
  </div>
</template>

<style scoped>
.practice-panel { padding: 24px 30px 32px; }
.practice-message { color: var(--muted); font-size: 13px; line-height: 1.9; }
select { font: inherit; color: inherit; background: #fbfcf9; border: 1px solid #dfe5d9; border-radius: 12px; padding: 11px 13px; min-width: 0; }
input:focus-visible, select:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.primary-button { border: none; border-radius: 12px; background: #526f57; color: white; padding: 12px 24px; font-size: 13px; }
.primary-button:hover:not(:disabled) { background: #435f48; }
.primary-button:disabled { cursor: not-allowed; opacity: .55; }
.practice-toolbar { display: flex; justify-content: space-between; align-items: end; gap: 16px; margin: 22px 0; }
.practice-toolbar label { display: grid; gap: 8px; font-size: 12px; color: var(--muted); }
.practice-toolbar select { min-width: 160px; font-size: 13px; }
.question-meta { display: flex; justify-content: space-between; gap: 12px; font-size: 12px; color: var(--muted); margin: 26px 0 20px; }
fieldset { border: none; padding: 0; margin: 0; min-width: 0; }
legend { font-size: 18px; line-height: 1.8; font-weight: 500; padding: 0; margin-bottom: 20px; }
.answer-option { display: flex; align-items: center; gap: 12px; padding: 15px 18px; border: 1px solid #e2e8de; border-radius: 14px; margin-bottom: 11px; font-size: 14px; line-height: 1.8; cursor: pointer; }
.answer-option:has(input:disabled) { cursor: default; }
.answer-option.chosen, .answer-option.correct { background: #eef4e9; border-color: #a6b99a; }
.answer-option.incorrect { background: #fbf1eb; border-color: #d8b5a0; }
.answer-option input { accent-color: #526f57; margin: 0; flex-shrink: 0; }
.option-key { color: var(--muted); }
.answer-option small { margin-left: auto; color: #506e4d; font-size: 11px; white-space: nowrap; }
.question-source { font-size: 11px; color: var(--muted); line-height: 1.8; margin: 18px 0 24px; }
.submit-row { display: flex; gap: 18px; align-items: center; flex-wrap: wrap; }
.submit-row > span { font-size: 12px; color: var(--muted); }
.answer-result { background: #f4f6f0; border-radius: 18px; padding: 22px; }
.answer-result h3 { margin: 0 0 16px; font-size: 17px; font-weight: 500; }
.answer-result h4 { font-size: 13px; margin: 20px 0 8px; font-weight: 500; }
.answer-result p { font-size: 13px; line-height: 1.9; margin: 0; }
.review-summary { display: grid; gap: 9px; border-top: 1px solid #e1e7da; margin-top: 20px; padding-top: 18px; font-size: 12px; }
.review-summary small { color: var(--muted); line-height: 1.8; }
.practice-error { padding: 14px 17px; border-radius: 12px; color: #89533a; background: #fbf1eb; font-size: 13px; line-height: 1.9; }
@media (max-width: 700px) {
  .practice-panel { padding: 20px; }
  .practice-toolbar { flex-wrap: wrap; align-items: center; }
  .practice-toolbar select { min-width: 130px; max-width: 100%; }
  .answer-option { padding: 12px; gap: 8px; flex-wrap: wrap; }
  .answer-result { padding: 18px; }
}
</style>
