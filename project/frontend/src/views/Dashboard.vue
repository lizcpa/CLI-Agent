<template>
  <div class="dashboard" v-loading="loading">
    <el-row :gutter="20">
      <el-col :span="6" v-for="s in stats" :key="s.label">
        <el-card shadow="hover">
          <div class="stat-card">
            <el-icon :size="36" :color="s.color"><component :is="s.icon" /></el-icon>
            <div>
              <div class="stat-value">{{ s.value }}</div>
              <div class="stat-label">{{ s.label }}</div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="20" style="margin-top:20px">
      <el-col :span="12">
        <el-card header="任务概览">
          <el-empty v-if="tasks.length === 0" description="暂无任务" :image-size="80" />
          <el-table v-else :data="tasks" stripe size="small">
            <el-table-column prop="task_id" label="任务ID" width="200" />
            <el-table-column prop="status" label="状态" width="100">
              <template #default="{ row }">
                <el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="progress" label="进度" width="120">
              <template #default="{ row }">
                <el-progress :percentage="Number(row.progress) || 0" :status="row.status === 'completed' ? 'success' : ''" />
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card header="模型使用统计">
          <el-empty v-if="modelUsage.length === 0" description="暂无模型调用记录" :image-size="80" />
          <div v-for="m in modelUsage" :key="m.name" style="margin-bottom:12px">
            <div style="display:flex;justify-content:space-between;margin-bottom:4px">
              <span>{{ m.name }}</span><span>{{ m.count }} 次</span>
            </div>
            <el-progress :percentage="m.pct" :stroke-width="10" />
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { getDashboard, getTasks } from '../api'

const loading = ref(true)
const stats = ref([
  { label: '商品总数', value: 0, icon: 'Goods', color: '#409EFF' },
  { label: '热门商品', value: 0, icon: 'Star', color: '#E6A23C' },
  { label: '活跃流水线', value: 0, icon: 'Connection', color: '#67C23A' },
  { label: '累计发布', value: 0, icon: 'Promotion', color: '#F56C6C' },
])
const tasks = ref<any[]>([])
const modelUsage = ref<any[]>([])

function statusType(s: string) {
  return s === 'completed' ? 'success' : s === 'running' || s === 'queued' ? 'warning' : s === 'failed' ? 'danger' : 'info'
}

async function loadData() {
  loading.value = true
  try {
    const [dashData, taskData] = await Promise.allSettled([getDashboard(), getTasks()])
    if (dashData.status === 'fulfilled' && dashData.value) {
      const d = dashData.value
      stats.value[0].value = d.total_products ?? 0
      stats.value[1].value = d.hot_products ?? 0
      stats.value[2].value = d.active_pipelines ?? 0
      stats.value[3].value = d.total_publishes ?? 0
      const usage = d.model_usage || {}
      const entries = Object.entries(usage)
      const maxCount = Math.max(...entries.map(([, v]) => v as number), 1)
      modelUsage.value = entries.map(([name, count]) => ({
        name, count: count as number, pct: Math.round(((count as number) / maxCount) * 100),
      }))
    }
    if (taskData.status === 'fulfilled' && Array.isArray(taskData.value)) {
      tasks.value = taskData.value
    }
  } catch (e) {
    console.error('Dashboard load error:', e)
  } finally {
    loading.value = false
  }
}

onMounted(loadData)
</script>

<style scoped>
.stat-card { display: flex; align-items: center; gap: 16px; }
.stat-value { font-size: 28px; font-weight: 700; color: #303133; }
.stat-label { font-size: 13px; color: #909399; margin-top: 4px; }
</style>
