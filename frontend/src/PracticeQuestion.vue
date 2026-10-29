<script setup lang="ts">
import type { QuestionContent } from './practiceTypes'

defineProps<{ question: QuestionContent; busy?: boolean; pending?: boolean; canSubmit?: boolean; historical?: boolean }>()
const answer = defineModel<string>({ default: '' })
defineEmits<{ submit: [] }>()
</script>

<template>
  <form class="question-form" @submit.prevent="$emit('submit')">
    <fieldset :disabled="busy || pending || !!question.result || !canSubmit">
      <legend>{{ question.stem }}</legend>
      <label v-for="option in question.options" :key="option.id" class="answer-option" :class="{ chosen: answer === option.id, correct: question.result?.correct_answer === option.id, incorrect: question.result && answer === option.id && question.result.correct_answer !== option.id }">
        <input v-model="answer" type="radio" name="review-answer" :value="option.id" required />
        <span class="option-key">{{ option.id }}</span><span>{{ option.text }}</span>
        <small v-if="question.result?.correct_answer === option.id">正确答案</small>
      </label>
    </fieldset>
    <p class="question-source">{{ question.source }}</p>
    <div v-if="canSubmit && !question.result" class="submit-row"><button class="primary-button" :disabled="!answer || busy">{{ busy ? '正在提交…' : pending ? '重试原提交' : '提交复习答案' }}</button><span>每题每天正式复习一次，提交后可查看解析。</span></div>
    <section v-if="question.result" class="answer-result" aria-label="答题结果" aria-live="polite">
      <h3>{{ question.result.score === 100 ? '回答正确' : '这道题答错了' }}</h3>
      <p>你的答案：{{ question.result.answer }}　正确答案：{{ question.result.correct_answer }}</p>
      <h4>解析</h4><p>{{ question.result.explanation }}</p>
      <div class="review-summary"><span>已计入 {{ question.result.check_in_date }} 打卡</span><span>{{ historical ? '当次安排的复习日期' : '本次安排的复习日期' }}：{{ question.result.review_due_date }}</span><small>{{ question.result.review_reason }}</small></div>
    </section>
  </form>
</template>

<style scoped src="./practice.css"></style>
