export type Result = {
  id: number; answer: string; score: number; correct_answer: string; explanation: string
  check_in_date: string; review_due_date: string; review_reason: string
}

export type QuestionContent = {
  question_version_id: number; stem: string; options: { id: string; text: string }[]
  source: string; result: Result | null
}
