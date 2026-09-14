import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import 'element-plus/dist/index.css'

import App from './App.vue'
import { router } from './router'
import './styles/main.css'

const app = createApp(App)

app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })

// 内部管理工具, 全量注册图标保持模板简洁
for (const [iconName, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(iconName, component)
}

app.mount('#app')
