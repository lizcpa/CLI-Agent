<template>
  <div class="login-page">
    <div class="login-card">
      <h2>ProdVideo AI Factory</h2>
      <p class="sub">AI 视频生成与分发中台 管理后台</p>
      <el-form :model="form" label-position="top" @submit.prevent="handleLogin">
        <el-form-item label="用户名">
          <el-input v-model="form.username" placeholder="admin" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" placeholder="******" show-password />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" native-type="submit" style="width:100%" :loading="loading">登 录</el-button>
        </el-form-item>
      </el-form>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const authStore = useAuthStore()
const form = reactive({ username: 'admin', password: 'admin123' })
const loading = ref(false)
const error = ref('')

async function handleLogin() {
  loading.value = true
  error.value = ''
  const ok = await authStore.login(form.username, form.password)
  if (ok) {
    router.push('/dashboard')
  } else {
    error.value = '用户名或密码错误'
  }
  loading.value = false
}
</script>

<style scoped>
.login-page { height: 100vh; display: flex; align-items: center; justify-content: center; background: linear-gradient(135deg, #1e3a8a 0%, #3b82f6 50%, #06b6d4 100%); }
.login-card { width: 400px; padding: 40px; background: #fff; border-radius: 12px; box-shadow: 0 20px 60px rgba(0,0,0,.3); }
.login-card h2 { text-align: center; color: #1e3a8a; margin-bottom: 6px; }
.sub { text-align: center; color: #909399; font-size: 13px; margin-bottom: 24px; }
</style>
