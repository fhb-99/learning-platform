<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, errorMessage } from './session'
import PracticeQuestion from './PracticeQuestion.vue'
import type { QuestionContent } from './practiceTypes'

type Item = { id: number; stem: string; track_name: string; date: string; source: string; correct: boolean; review_due_date: string }
type Page = { items: Item[]; next_cursor: number | null }
type Detail = QuestionContent & { version: number; source: string; scheduled_due_date: string | null; current_due_date: string | null }
const props = defineProps<{ domainCode: string }>()
const items = ref<Item[]>([])
const cursor = ref<number | null>(null)
const selectedId = ref(Number(new URLSearchParams(location.hash.slice(1)).get('attempt')) || 0)
const detail = ref<Detail | null>(null)
const answer = ref('')
const source = ref('all')
const start = ref('')
const end = ref('')
const loading = ref(false)
const error = ref('')
let loadVersion = 0
// 分页沿用已应用的筛选，表单草稿只有点击筛选后才生效。
let appliedFilters = { source: 'all', start: '', end: '' }

async function load(more = false) {
  const version = ++loadVersion
  loading.value = true
  error.value = ''
  detail.value = null
  if (!more) { items.value = []; cursor.value = null }
  const hash = new URLSearchParams(location.hash.slice(1))
  if (selectedId.value) hash.set('attempt', String(selectedId.value))
  else hash.delete('attempt')
  history.replaceState(null, '', `#${hash}`)
  try {
    if (selectedId.value) {
      const data = await api<Detail>(`/history/${selectedId.value}`)
      if (version !== loadVersion) return
      detail.value = data
      answer.value = data.result?.answer ?? ''
    } else {
      if (!more) appliedFilters = { source: source.value, start: start.value, end: end.value }
      const params = new URLSearchParams({ domain_code: props.domainCode, source: appliedFilters.source })
      if (appliedFilters.start) params.set('start_date', appliedFilters.start)
      if (appliedFilters.end) params.set('end_date', appliedFilters.end)
      if (more && cursor.value) params.set('before_id', String(cursor.value))
      const data = await api<Page>(`/history?${params}`)
      if (version !== loadVersion) return
      items.value = more ? [...items.value, ...data.items] : data.items
      cursor.value = data.next_cursor
    }
  } catch (cause) { if (version === loadVersion) error.value = errorMessage(cause) }
  finally { if (version === loadVersion) loading.value = false }
}

function open(id: number) { selectedId.value = id; void load() }
function back() { selectedId.value = 0; void load() }
watch(() => props.domainCode, back)
onMounted(() => load())
onBeforeUnmount(() => { ++loadVersion })
</script>

<template>
  <div class="practice-panel">
    <div class="practice-toolbar"><button v-if="selectedId" class="secondary-button" @click="back">返回学习记录</button><span v-else class="practice-message">每一次正式提交都会保留</span><button class="secondary-button" :disabled="loading" @click="load()">重新加载</button></div>
    <form v-if="!selectedId" class="history-filters" @submit.prevent="load()"><label>练习类型<select v-model="source"><option value="all">全部练习</option><option value="daily">每日练习</option><option value="review">个人复习</option></select></label><label>开始日期<input v-model="start" type="date" /></label><label>结束日期<input v-model="end" type="date" /></label><button class="secondary-button" :disabled="loading">筛选</button></form>
    <p v-if="loading" class="practice-message" role="status">正在加载学习记录…</p>
    <template v-if="detail && !loading">
      <div class="question-meta"><span>{{ detail.result?.check_in_date }} · {{ detail.source === 'daily' ? '每日练习' : '个人复习' }}</span><span>题目版本 {{ detail.version }}</span></div>
      <p class="practice-message"><span v-if="detail.scheduled_due_date">原定复习日期：{{ detail.scheduled_due_date }} · </span>当前计划：{{ detail.current_due_date ?? '暂无' }}</p>
      <PracticeQuestion v-model="answer" :question="detail" historical />
    </template>
    <template v-else-if="!selectedId">
      <p v-if="!loading && !error && !items.length" class="empty-review">这个范围内还没有学习记录。</p>
      <ul class="record-list"><li v-for="item in items" :key="item.id"><div class="question-meta"><span>{{ item.date }} · {{ item.source === 'daily' ? '每日练习' : '个人复习' }} · {{ item.track_name }}</span><span>{{ item.correct ? '回答正确' : '回答错误' }}</span></div><h2>{{ item.stem }}</h2><p>当次安排复习：{{ item.review_due_date }}</p><button class="secondary-button" @click="open(item.id)">查看答案与解析</button></li></ul>
      <button v-if="cursor" class="secondary-button" :disabled="loading" @click="load(true)">加载更多记录</button>
    </template>
    <p v-if="error" class="practice-error" role="alert">{{ error }}</p>
  </div>
</template>

<style scoped src="./practice.css"></style>
