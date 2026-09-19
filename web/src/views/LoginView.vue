<template>
  <div class="login-page">
    <el-card class="login-card" shadow="always">
      <div class="login-head">
        <img class="brand-mark" :src="brandMark" alt="UniHive" />
        <h1>UniHive Console</h1>
        <p class="subtitle">金融数据 MCP 聚合网关</p>
      </div>

      <el-alert
        v-if="errorMsg"
        :title="errorMsg"
        type="error"
        show-icon
        :closable="false"
        class="login-alert"
      />

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        size="large"
        @submit.prevent="onSubmit"
      >
        <el-form-item prop="username">
          <el-input
            v-model="form.username"
            placeholder="用户名"
            autocomplete="username"
            :prefix-icon="User"
            aria-label="用户名"
            autofocus
          />
        </el-form-item>
        <el-form-item prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="密码"
            autocomplete="current-password"
            :prefix-icon="Lock"
            aria-label="密码"
            show-password
            @keyup.enter="onSubmit"
          />
        </el-form-item>
        <el-button
          type="primary"
          class="login-button"
          :loading="loading"
          @click="onSubmit"
        >
          登录
        </el-button>
      </el-form>

      <p class="login-meta">需授权访问 · 配置见 config/config.yaml</p>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Lock, User } from '@element-plus/icons-vue'
import type { FormInstance, FormRules } from 'element-plus'
import { errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import brandMark from '@/assets/unihive-mark-light.svg'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const formRef = ref<FormInstance>()
const loading = ref(false)
const errorMsg = ref('')

const form = reactive({ username: '', password: '' })

const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function onSubmit(): Promise<void> {
  errorMsg.value = ''
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    await auth.login(form.username.trim(), form.password)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/status'
    await router.replace(redirect)
  } catch (error) {
    errorMsg.value = errorMessage(error, '登录失败')
    // 失败清空密码 — 用户需重新输入, 避免反复输入用户名+旧密码
    form.password = ''
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100%;
  background: linear-gradient(135deg, #1d2733 0%, #2c3e55 55%, #3a506b 100%);
  padding: 24px;
}

.login-card {
  width: 380px;
  max-width: 100%;
  border-radius: 10px;
}

.login-card :deep(.el-card__body) {
  padding: 32px 32px 24px;
}

.login-head {
  text-align: center;
  margin-bottom: 22px;
}

.brand-mark {
  width: 48px;
  height: 48px;
  margin-bottom: 10px;
}

.login-head h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
}

.subtitle {
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.login-alert {
  margin-bottom: 16px;
}

.login-button {
  width: 100%;
}

.login-meta {
  margin: 18px 0 0;
  text-align: center;
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}
</style>
