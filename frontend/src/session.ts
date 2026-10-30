import { shallowRef } from 'vue'

export type Account = { username: string; timezone: string; csrf_token: string; is_admin: boolean }
export const account = shallowRef<Account | null>(null)
export const sessionNotice = shallowRef('')

export class ApiError extends Error {
  constructor(message: string, readonly status: number) { super(message) }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const requestAccount = account.value
  const response = await fetch(`/api/v1${path}`, {
    ...options, credentials: 'same-origin', signal: AbortSignal.timeout(12000),
    headers: {
      'Content-Type': 'application/json', 'X-Requested-With': 'learning-platform',
      ...(requestAccount ? { 'X-CSRF-Token': requestAccount.csrf_token } : {}),
    },
  })
  // 只让当前会话的失效响应退出登录，避免旧请求影响后来登录的账号。
  if (response.status === 401 && requestAccount && account.value === requestAccount) {
    sessionNotice.value = '登录状态已失效，请重新登录。'
    account.value = null
  }
  const data = response.status === 204 ? null : await response.json()
  if (!response.ok) {
    throw new ApiError(typeof data?.detail === 'string' ? data.detail : '请求未通过校验，请检查填写内容。', response.status)
  }
  return data as T
}

export function errorMessage(cause: unknown) {
  return cause instanceof ApiError ? cause.message : '连接失败或超时，请重试。'
}
