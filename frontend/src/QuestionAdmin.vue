<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { api, errorMessage } from './session'

type Content = { stem: string; options: { id: string; text: string }[]; correct_answer: string; explanation: string; source: string }
type Draft = Content & { revision: number; published_version_id: number | null }
type Detail = { id: number; code: string; track_id: number; domain_code: string; latest_version: number | null; draft: Draft }
type Version = Content & { id: number; version: number; published_at: string; published_by: number | null }
type Row = { id: number; code: string; stem: string; latest_version: number | null; has_draft: boolean }
const props = defineProps<{ domainCode: string }>()
const emit = defineEmits<{ busy: [value: boolean] }>()
const tracks = ref<{ id: number; name: string }[]>([])
const trackId = ref(0)
const rows = ref<Row[]>([])
const total = ref(0)
const offset = ref(0)
const loading = ref(false)
const writing = ref(false)
const dirty = ref(false)
const error = ref('')
const notice = ref('')
const detail = ref<Detail | null>(null)
const creating = ref(false)
const code = ref('')
const versions = ref<Version[]>([])
const nextVersion = ref<number | null>(null)
const publishConfirm = ref(false)
const blank = (): Content => ({ stem: '', options: Array.from('ABCD', id => ({ id, text: '' })), correct_answer: '', explanation: '', source: '' })
const form = reactive<Content>(blank())
const editing = computed(() => creating.value || detail.value !== null)
let loadVersion = 0
watch([dirty, writing], () => emit('busy', dirty.value || writing.value), { flush: 'sync' })
function beforeUnload(event: BeforeUnloadEvent) {
  if (dirty.value || writing.value) event.preventDefault()
}
window.addEventListener('beforeunload', beforeUnload)
onBeforeUnmount(() => { loadVersion++; window.removeEventListener('beforeunload', beforeUnload); emit('busy', false) })

function changed() { dirty.value = true; notice.value = ''; publishConfirm.value = false }
function fill(data: Detail) {
  detail.value = data
  creating.value = false
  code.value = data.code
  Object.assign(form, structuredClone({ stem: data.draft.stem, options: data.draft.options, correct_answer: data.draft.correct_answer, explanation: data.draft.explanation, source: data.draft.source }))
  dirty.value = false
  publishConfirm.value = false
}
async function loadList(reset = false) {
  const version = ++loadVersion
  loading.value = true; error.value = ''; detail.value = null; creating.value = false
  rows.value = []; total.value = 0; notice.value = ''
  if (reset) offset.value = 0
  try {
    const data = await api<{ items: Row[]; total: number }>(`/admin/questions?track_id=${trackId.value}&offset=${offset.value}`)
    if (version !== loadVersion) return
    rows.value = data.items; total.value = data.total
  } catch (cause) { if (version === loadVersion) error.value = errorMessage(cause) }
  finally { if (version === loadVersion) loading.value = false }
}
watch(() => props.domainCode, async domain => {
  const version = ++loadVersion
  loading.value = true; error.value = ''; rows.value = []; detail.value = null; creating.value = false
  tracks.value = []; trackId.value = 0; total.value = 0; notice.value = ''
  try {
    const data = await api<{ id: number; name: string }[]>(`/tracks?domain_code=${encodeURIComponent(domain)}`)
    if (version !== loadVersion) return
    tracks.value = data; trackId.value = data[0]?.id ?? 0
    if (trackId.value) await loadList(true)
  } catch (cause) { if (version === loadVersion) error.value = errorMessage(cause) }
  finally { if (version === loadVersion) loading.value = false }
}, { immediate: true })

async function openQuestion(id: number) {
  const version = ++loadVersion
  loading.value = true; error.value = ''; notice.value = ''
  try {
    const [data, history] = await Promise.all([
      api<Detail>(`/admin/questions/${id}`),
      api<{ items: Version[]; next_cursor: number | null }>(`/admin/questions/${id}/versions`),
    ])
    if (version !== loadVersion) return
    fill(data); versions.value = history.items; nextVersion.value = history.next_cursor
  } catch (cause) { if (version === loadVersion) error.value = errorMessage(cause) }
  finally { if (version === loadVersion) loading.value = false }
}
function newQuestion() {
  detail.value = null; creating.value = true; code.value = ''; Object.assign(form, blank())
  versions.value = []; nextVersion.value = null; error.value = ''; notice.value = ''; changed()
}
async function save() {
  if (writing.value) return
  writing.value = true; error.value = ''; notice.value = ''
  try {
    if (creating.value) {
      const result = await api<{ id: number }>('/admin/questions', { method: 'POST', body: JSON.stringify({ ...form, code: code.value, track_id: trackId.value }) })
      // 新建成功后即切换到已有题目，后续读取失败也不会重复创建。
      creating.value = false
      detail.value = { id: result.id, code: code.value, track_id: trackId.value, domain_code: props.domainCode, latest_version: null, draft: { ...JSON.parse(JSON.stringify(form)), revision: 1, published_version_id: null } }
    } else if (detail.value) {
      detail.value.draft = await api<Draft>(`/admin/questions/${detail.value.id}/draft`, { method: 'PUT', body: JSON.stringify({ ...form, revision: detail.value.draft.revision }) })
    }
    dirty.value = false; publishConfirm.value = false; notice.value = '草稿已保存，尚未发布。'
  } catch (cause) { error.value = errorMessage(cause) }
  finally { writing.value = false }
}
async function publish() {
  if (!detail.value || dirty.value || writing.value) return
  writing.value = true; error.value = ''; notice.value = ''
  try {
    const result = await api<{ id: number; version: number }>(`/admin/questions/${detail.value.id}/publish`, { method: 'POST', body: JSON.stringify({ revision: detail.value.draft.revision }) })
    detail.value.draft.published_version_id = result.id; detail.value.latest_version = result.version
    publishConfirm.value = false
    notice.value = `版本 v${result.version} 已发布。历史作答、已分配每日题和已有复习保持原版本。`
    const history = await api<{ items: Version[]; next_cursor: number | null }>(`/admin/questions/${detail.value.id}/versions`)
    versions.value = history.items; nextVersion.value = history.next_cursor
  } catch (cause) { error.value = errorMessage(cause) }
  finally { writing.value = false }
}
async function moreVersions() {
  if (!detail.value || !nextVersion.value || loading.value) return
  const version = ++loadVersion
  loading.value = true; error.value = ''
  try {
    const data = await api<{ items: Version[]; next_cursor: number | null }>(`/admin/questions/${detail.value.id}/versions?before_version=${nextVersion.value}`)
    if (version !== loadVersion) return
    versions.value.push(...data.items); nextVersion.value = data.next_cursor
  } catch (cause) { if (version === loadVersion) error.value = errorMessage(cause) }
  finally { if (version === loadVersion) loading.value = false }
}
async function discard() {
  if (detail.value) await openQuestion(detail.value.id)
  else { dirty.value = false; await loadList() }
}
</script>

<template>
  <div class="practice-panel admin-panel">
    <p class="practice-message">保存草稿后再审核发布。已发布内容只能通过新版本修订，题目所属方向与标识创建后固定。</p>
    <div class="practice-toolbar">
      <label>学习方向<select v-model="trackId" :disabled="loading || writing || dirty" @change="loadList(true)"><option v-for="track in tracks" :key="track.id" :value="track.id">{{ track.name }}</option></select></label>
      <button v-if="!editing" class="primary-button" :disabled="loading || !trackId" @click="newQuestion">新建题目</button>
      <button v-else class="secondary-button" :disabled="dirty || writing || loading" @click="loadList()">返回题目列表</button>
    </div>
    <p v-if="error" class="practice-error" role="alert">{{ error }}<span v-if="editing"> 如遇修订冲突，请重新加载；未保存内容会被放弃。</span></p>
    <p v-if="notice" class="practice-message" role="status">{{ notice }}</p>
    <p v-if="loading" role="status">正在加载…</p>
    <template v-if="editing">
      <form @submit.prevent="save" @input="changed" @change="changed">
        <fieldset :disabled="writing || loading">
          <label>题目标识<input v-model="code" :disabled="!creating" maxlength="64" pattern="[a-z0-9][a-z0-9_-]{2,63}" required placeholder="如 computer-network-001" /></label>
          <p class="practice-message">{{ detail ? `最新发布：${detail.latest_version ? 'v' + detail.latest_version : '尚无'} · 草稿修订 ${detail.draft.revision}` : '先保存草稿，再发布第一个版本。' }}</p>
          <label>题干<textarea v-model="form.stem" maxlength="10000" rows="4" /></label>
          <label v-for="option in form.options" :key="option.id">选项 {{ option.id }}<textarea v-model="option.text" maxlength="2000" rows="2" /></label>
          <label>正确答案<select v-model="form.correct_answer"><option value="">尚未选择</option><option v-for="key in 'ABCD'" :key="key" :value="key">{{ key }}</option></select></label>
          <label>解析<textarea v-model="form.explanation" maxlength="10000" rows="4" /></label>
          <label>内容来源<input v-model="form.source" maxlength="255" placeholder="填写自编说明或可追溯来源" /></label>
          <div class="submit-row"><button class="primary-button" :disabled="!dirty">{{ writing ? '保存中…' : '保存草稿' }}</button><button type="button" class="secondary-button" @click="discard">{{ dirty ? '放弃未保存内容并重新加载' : '重新加载' }}</button></div>
        </fieldset>
      </form>
      <div class="publication">
        <p v-if="dirty" class="practice-message">有未保存修改，请保存或放弃后再切换页面。</p>
        <button v-if="detail && !detail.draft.published_version_id && !publishConfirm" class="primary-button" :disabled="dirty || writing || loading" @click="publishConfirm = true">审核并发布</button>
        <div v-if="publishConfirm" class="answer-result"><h3>发布当前已保存的草稿？</h3><p>请核对上方题干、四个选项、正确答案、解析与来源。发布后将生成新版本，原版本仍用于已有作答和复习。</p><div class="submit-row"><button class="primary-button" :disabled="writing || dirty || loading" @click="publish">{{ writing ? '发布中…' : '确认发布新版本' }}</button><button class="secondary-button" :disabled="writing" @click="publishConfirm = false">继续检查</button></div></div>
        <p v-if="detail?.draft.published_version_id" class="practice-message">当前内容已有发布版；修改并保存后可发布下一版。</p>
      </div>
      <h2>已发布版本（只读）</h2>
      <p v-if="!versions.length" class="practice-message">暂无发布版本。</p>
      <details v-for="version in versions" :key="version.id" class="version-card"><summary>v{{ version.version }} · {{ new Date(version.published_at + 'Z').toLocaleString('zh-CN') }} · {{ version.published_by === null ? '历史样题导入' : '管理员 #' + version.published_by }}</summary><h3>{{ version.stem }}</h3><p v-for="option in version.options" :key="option.id">{{ option.id }}. {{ option.text }}</p><p>正确答案：{{ version.correct_answer }}</p><p>解析：{{ version.explanation }}</p><p>来源：{{ version.source }}</p></details>
      <button v-if="nextVersion" class="secondary-button" :disabled="loading || writing" @click="moreVersions">更早版本</button>
    </template>
    <template v-else-if="!loading">
      <p class="practice-message">共 {{ total }} 道题目</p>
      <ul class="record-list"><li v-for="row in rows" :key="row.id"><h2>{{ row.stem || '尚未填写题干' }}</h2><p>{{ row.code }} · {{ row.latest_version ? '已发布 v' + row.latest_version : '未发布' }}{{ row.has_draft ? ' · 有待发布草稿' : '' }}</p><button class="secondary-button" @click="openQuestion(row.id)">编辑与版本</button></li></ul>
      <div class="submit-row"><button class="secondary-button" :disabled="offset === 0" @click="offset -= 20; loadList()">上一页</button><button class="secondary-button" :disabled="offset + 20 >= total" @click="offset += 20; loadList()">下一页</button><button class="secondary-button" @click="loadList()">刷新列表</button></div>
    </template>
  </div>
</template>

<style scoped src="./practice.css"></style>
<style scoped>
.admin-panel form label { display: grid; gap: 8px; margin: 18px 0; font-size: 13px; color: var(--muted); }
.admin-panel input, .admin-panel textarea { width: 100%; min-width: 0; box-sizing: border-box; border: 1px solid #dfe5d9; border-radius: 12px; padding: 12px; font: inherit; line-height: 1.7; color: var(--ink); background: #fbfcf9; }
.admin-panel textarea { resize: vertical; }
.admin-panel h2 { font-size: 17px; font-weight: 500; }
.admin-panel h3, .version-card p { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 13px; line-height: 1.9; }
.publication { margin: 24px 0 32px; }
.publication .submit-row { margin-top: 18px; }
.version-card { border-top: 1px solid #e2e8de; padding: 18px 0; }
.version-card summary { cursor: pointer; color: var(--accent); font-size: 13px; }
</style>
