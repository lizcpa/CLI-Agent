<template>
  <el-container class="main-layout">
    <el-aside width="220px" class="sidebar">
      <div class="logo">
        <el-icon :size="28"><VideoCamera /></el-icon>
        <span>ProdVideo AI</span>
      </div>
      <el-menu :default-active="activeMenu" router background-color="#304156" text-color="#bfcbd9" active-text-color="#409EFF">
        <el-menu-item index="/agent">
          <el-icon><Monitor /></el-icon><span>Agent 指挥台</span>
        </el-menu-item>
        <el-menu-item index="/dashboard">
          <el-icon><DataAnalysis /></el-icon><span>仪表盘</span>
        </el-menu-item>
        <el-menu-item index="/crawl">
          <el-icon><Search /></el-icon><span>采集管理</span>
        </el-menu-item>
        <el-menu-item index="/analysis">
          <el-icon><TrendCharts /></el-icon><span>选品分析</span>
        </el-menu-item>
        <el-menu-item index="/generation">
          <el-icon><MagicStick /></el-icon><span>AI 生成</span>
        </el-menu-item>
        <el-menu-item index="/composer">
          <el-icon><VideoPlay /></el-icon><span>视频合成</span>
        </el-menu-item>
        <el-menu-item index="/publish">
          <el-icon><Promotion /></el-icon><span>发布管理</span>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon><span>系统设置</span>
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="topbar">
        <span class="page-title">{{ route.meta.title }}</span>
        <div class="topbar-right">
          <el-tag size="small">租户: {{ authStore.tenantId }}</el-tag>
          <span class="user">{{ authStore.username }}</span>
          <el-button text @click="handleLogout">退出</el-button>
        </div>
      </el-header>
      <el-main>
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const activeMenu = computed(() => route.path)

function handleLogout() {
  authStore.logout()
  router.push('/login')
}
</script>

<style scoped>
.main-layout { height: 100vh; }
.sidebar { background-color: #304156; overflow-y: auto; }
.logo { height: 60px; display: flex; align-items: center; justify-content: center; color: #fff; font-size: 18px; font-weight: bold; gap: 8px; }
.el-menu { border-right: none; }
.topbar { background: #fff; border-bottom: 1px solid #e4e7ed; display: flex; align-items: center; justify-content: space-between; padding: 0 20px; height: 56px; }
.page-title { font-size: 18px; font-weight: 600; color: #303133; }
.topbar-right { display: flex; align-items: center; gap: 12px; }
.user { color: #606266; }
.el-main { padding: 20px; background: #f5f7fa; }
</style>
