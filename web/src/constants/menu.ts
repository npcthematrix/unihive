/**
 * 侧栏导航项。
 *
 * 抽成独立模块 (而非留在 DefaultLayout 的 <script setup> 里) 是为了能被单测
 * 覆盖: el-menu 的 default-active 按字符串**精确**匹配 index, 菜单 path 一旦
 * 与路由真实 path 漂移, 该菜单项就永远不会高亮 —— 曾出现过这种静默故障
 * (菜单写 /l1-cache, 实际路由是 /gateway-monitor, 点击后 URL 被重定向,
 * 高亮对不上)。tests/constants/menu.spec.ts 会守住这条不变式。
 */
import type { Component } from 'vue'
import {
  Connection,
  DataLine,
  Document,
  Files,
  Grid,
  Setting,
} from '@element-plus/icons-vue'

export interface MenuItem {
  /** 必须与 router 里该页面的真实 path 完全一致。 */
  path: string
  title: string
  icon: Component
}

export const MENU: readonly MenuItem[] = [
  { path: '/guides', title: '接入指南', icon: Document },
  { path: '/gateway-monitor', title: '网关运行监控', icon: DataLine },
  { path: '/interfaces', title: 'MCP APIs', icon: Connection },
  { path: '/config', title: '配置信息', icon: Setting },
  { path: '/omni', title: 'OMNI板块管理', icon: Grid },
  { path: '/mcp-browser', title: 'MCP 浏览器', icon: Files },
]
