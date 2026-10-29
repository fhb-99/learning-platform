<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, errorMessage } from './session'
import PracticeQuestion from './PracticeQuestion.vue'
import type { QuestionContent, Result } from './practiceTypes'

type ReviewItem = { question_id: number; stem: string; track_name: string; due_date: string; overdue_days: number; reason: string }
type Queue = { date: string; total: number; items: ReviewItem[] }
type Review = QuestionContent & { question_id: number; basis_attempt_id: number; date: string; due_date: string; reason: string; can_submit: boolean }
const props = defineProps<{ domainCode: string }>()
const emit = defineEmits<{ busy: [value: boolean] }>()
const queue = ref<Queue | null>(null)
const detail = ref<Review | null>(null)
const selectedId = ref(Number(new URLSearchParams(location.hash.slice(1)).get('review')) || 0)
const offset = ref(0)
const loading = ref(false)
const submitting = ref(false)
const error = ref('')
const answer = ref('')
const pendingAnswer = ref('')
let key = ''
let loadVersion = 0

async function load() {
  if (submitting.value) return
  const version = ++loadVersion
  loading.value = true
  error.value = ''
  detail.value = null
  queue.value = null
  answer.value = ''
  pendingAnswer.value = ''
  const params = new URLSearchParams(location.hash.slice(1))
  if (selectedId.value) params.set('review', String(selectedId.value))
  else params.delete('review')
  history.replaceState(null, '', `#${params}`)
  try {
    if (selectedId.value) {
      const data = await api<Review>(`/reviews/${selectedId.value}`)
      if (version !== loadVersion) return
      detail.value = data
      answer.value = data.result?.answer ?? ''
      key = crypto.randomUUID()
    } else {
      const data = await api<Queue>(`/reviews?domain_code=${encodeURIComponent(props.domainCode)}&offset=${offset.value}`)
      if (version !== loadVersion) return
      // 另一窗口完成复习后，总数可能缩小；回到仍存在的最后一页。
      if (offset.value > 0 && offset.value >= data.total) {
        offset.value = Math.max(0, Math.floor((data.total - 1) / 20) * 20)
        await load()
        return
      }
      queue.value = data
    }
  } catch (cause) { if (version === loadVersion) error.value = errorMessage(cause) }
  finally { if (version === loadVersion) loading.value = false }
}

function open(id: number) { selectedId.value = id; void load() }
function back() { selectedId.value = 0; offset.value = 0; void load() }
function page(step: number) { offset.value += step; void load() }

async function submit() {
  const current = detail.value
  if (!current?.can_submit || current.result || !answer.value || submitting.value) return
  submitting.value = true
  emit('busy', true)
  error.value = ''
  pendingAnswer.value ||= answer.value
  try {
    current.result = await api<Result>(`/reviews/${current.question_id}/attempts`, {
      method: 'POST', body: JSON.stringify({ question_version_id: current.question_version_id,
        basis_attempt_id: current.basis_attempt_id, answer: pendingAnswer.value, idempotency_key: key }),
    })
    current.can_submit = false
    current.due_date = current.result.review_due_date
    answer.value = current.result.answer
    pendingAnswer.value = ''
  } catch (cause) { error.value = errorMessage(cause) }
  finally { submitting.value = false; emit('busy', false) }
}

watch(() => props.domainCode, back)
onMounted(load)
onBeforeUnmount(() => { ++loadVersion })
</script>

<template>
  <div class="practice-panel">
    <div class="practice-toolbar"><button v-if="selectedId" class="secondary-button" :disabled="submitting" @click="back">返回到期列表</button><span v-else class="practice-message">按最早到期排序 · 每题每天一次</span><button class="secondary-button" :disabled="loading || submitting" @click="load">重新加载</button></div>
    <p v-if="loading" class="practice-message" role="status">正在加载复习安排…</p>
    <template v-else-if="detail">
      <div class="question-meta"><span>{{ detail.date }} · 个人复习</span><span>{{ detail.result ? '今日已完成' : detail.can_submit ? '待复习' : '尚未到期' }}</span></div>
      <p v-if="!detail.can_submit" class="practice-message">当前计划：{{ detail.due_date }} 再来复习。{{ detail.result ? '当天可以回看本次解析。' : '' }}</p>
      <PracticeQuestion v-model="answer" :question="detail" :busy="submitting" :pending="!!pendingAnswer" :can-submit="detail.can_submit" @submit="submit" />
    </template>
    <template v-else-if="queue">
      <p class="practice-message">{{ queue.date }} · 共 {{ queue.total }} 道到期题目</p>
      <p v-if="!queue.total" class="empty-review">今天没有到期复习。可以先完成每日练习，或在学习记录中回看解析。</p>
      <ul class="record-list"><li v-for="item in queue.items" :key="item.question_id"><div class="question-meta"><span>{{ item.track_name }} · {{ item.due_date }} 到期</span><span>{{ item.overdue_days ? `已逾期 ${item.overdue_days} 天` : '今日到期' }}</span></div><h2>{{ item.stem }}</h2><p>{{ item.reason }}</p><button class="secondary-button" @click="open(item.question_id)">开始复习</button></li></ul>
      <div v-if="queue.total > 20" class="practice-toolbar"><button class="secondary-button" :disabled="!offset" @click="page(-20)">上一页</button><span>{{ offset + 1 }}–{{ Math.min(offset + 20, queue.total) }}</span><button class="secondary-button" :disabled="offset + 20 >= queue.total" @click="page(20)">下一页</button></div>
    </template>
    <p v-if="error" class="practice-error" role="alert">{{ error }}<span v-if="pendingAnswer"> 可重试原提交，或重新加载以恢复已保存的结果。</span></p>
  </div>
</template>

<style scoped src="./practice.css"></style>
