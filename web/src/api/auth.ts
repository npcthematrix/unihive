import { http } from './client'

export interface LoginPayload {
  username: string
  password: string
}

export async function login(payload: LoginPayload): Promise<{ username: string }> {
  const { data } = await http.post<{ username: string }>('/api/auth/login', payload)
  return data
}

export async function logout(): Promise<void> {
  await http.post('/api/auth/logout')
}

export async function fetchMe(): Promise<{ username: string }> {
  const { data } = await http.get<{ username: string }>('/api/auth/me')
  return data
}
