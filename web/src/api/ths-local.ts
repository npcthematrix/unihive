import { http } from './client'

export interface ThsLocalTool {
  name: string
  title: string
  description: string
  inputSchema: {
    type: string
    properties?: Record<string, {
      type: string
      description?: string
      default?: unknown
      required?: boolean
    }>
    required?: string[]
  }
}

export interface ThsLocalStatus {
  connected: boolean
  toolCount: number
  tools: ThsLocalTool[]
}

export async function getThsLocalStatus(): Promise<ThsLocalStatus> {
  const { data } = await http.get<ThsLocalStatus>('/api/ths-local/status')
  return data
}
