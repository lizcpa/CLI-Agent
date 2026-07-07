<template>
  <div class="publish-management">
    <el-tabs v-model="activeTab">
      <el-tab-pane label="发布任务" name="publish">
        <el-card class="section-card">
          <el-form :model="publishForm" label-width="100px">
            <el-row :gutter="20">
              <el-col :span="8">
                <el-form-item label="管线 ID">
                  <el-input v-model="publishForm.pipeline_id" placeholder="pipeline id" />
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="视频 URL">
                  <el-input v-model="publishForm.video_url" placeholder="https://..." />
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="目标平台">
                  <el-select v-model="publishForm.platforms" multiple placeholder="选择平台" style="width:100%">
                    <el-option label="抖音" value="douyin" />
                    <el-option label="淘宝" value="taobao" />
                    <el-option label="Amazon" value="amazon" />
                    <el-option label="Shopee" value="shopee" />
                    <el-option label="YouTube" value="youtube" />
                  </el-select>
                </el-form-item>
              </el-col>
            </el-row>
            <el-row :gutter="20">
              <el-col :span="8">
                <el-form-item label="标题">
                  <el-input v-model="publishForm.title" placeholder="视频标题" />
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="标签">
                  <el-input v-model="publishForm.tags" placeholder="用逗号分隔" />
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="定时发布">
                  <el-date-picker
                    v-model="publishForm.scheduled_time"
                    type="datetime"
                    placeholder="选择时间（留空则立即发布）"
                    format="YYYY-MM-DD HH:mm"
                    value-format="YYYY-MM-DD HH:mm"
                    style="width:100%"
                  />
                </el-form-item>
              </el-col>
            </el-row>
            <el-form-item label="描述">
              <el-input v-model="publishForm.description" type="textarea" :rows="3" placeholder="视频描述" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="publishing" @click="handlePublish">
                {{ publishForm.scheduled_time ? '定时发布' : '立即发布' }}
              </el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="发布日志" name="logs">
        <el-card class="section-card">
          <div style="margin-bottom:16px;display:flex;align-items:center;gap:12px">
            <el-select v-model="logFilterPlatform" placeholder="按平台筛选" clearable style="width:180px">
              <el-option label="抖音" value="douyin" />
              <el-option label="淘宝" value="taobao" />
              <el-option label="Amazon" value="amazon" />
              <el-option label="Shopee" value="shopee" />
              <el-option label="YouTube" value="youtube" />
            </el-select>
            <el-button :loading="logsLoading" @click="loadLogs">刷新</el-button>
          </div>
          <el-table v-loading="logsLoading" :data="filteredLogs" stripe>
            <el-table-column prop="id" label="ID" width="80" />
            <el-table-column prop="pipeline_id" label="管线 ID" width="140" show-overflow-tooltip />
            <el-table-column prop="platform" label="平台" width="100">
              <template #default="{ row }">
                <el-tag size="small">{{ row.platform }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="100">
              <template #default="{ row }">
                <el-tag :type="logStatusTag(row.status)" size="small">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="platform_post_id" label="帖子 ID" width="140" show-overflow-tooltip />
            <el-table-column prop="public_url" label="公开链接" min-width="180">
              <template #default="{ row }">
                <a v-if="row.public_url" :href="row.public_url" target="_blank" style="color:#409EFF">查看</a>
                <span v-else class="muted">-</span>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="创建时间" width="170" />
            <el-table-column prop="error_message" label="错误信息" min-width="150">
              <template #default="{ row }">
                <span :style="{ color: row.error_message ? '#F56C6C' : '#C0C4CC' }">{{ row.error_message || '-' }}</span>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无发布日志" />
            </template>
          </el-table>
          <div class="pagination-wrap" v-if="logsTotal > logsPageSize">
            <el-pagination
              background
              layout="prev, pager, next"
              :total="logsTotal"
              :page-size="logsPageSize"
              :current-page="logsPage"
              @current-change="onLogsPageChange"
            />
          </div>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="平台授权" name="auth">
        <el-card class="section-card">
          <div style="margin-bottom:16px">
            <el-button type="primary" @click="authDialogVisible = true">添加授权</el-button>
            <el-button :loading="authLoading" @click="loadAuthorizedPlatforms">刷新</el-button>
          </div>
          <el-table v-loading="authLoading" :data="platformAuths" stripe>
            <el-table-column prop="platform" label="平台" width="120">
              <template #default="{ row }">
                <el-tag size="small">{{ row.platform }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="platform_user_id" label="账号" width="180">
              <template #default="{ row }">
                {{ row.platform_user_id || row.username || '-' }}
              </template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="100">
              <template #default="{ row }">
                <el-tag :type="row.status === 'active' ? 'success' : 'danger'" size="small">
                  {{ row.status === 'active' ? '已授权' : (row.status || '未知') }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="authorized_at" label="授权时间" width="170">
              <template #default="{ row }">{{ row.authorized_at || row.created_at || '-' }}</template>
            </el-table-column>
            <el-table-column prop="expires_at" label="过期时间" width="170">
              <template #default="{ row }">{{ row.expires_at || row.updated_at || '-' }}</template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无已授权平台" />
            </template>
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="authDialogVisible" title="添加平台授权" width="480px" destroy-on-close @closed="resetAuthForm">
      <el-form :model="authForm" label-width="80px">
        <el-form-item label="平台">
          <el-select v-model="authForm.platform" placeholder="选择平台" style="width:100%">
            <el-option label="抖音" value="douyin" />
            <el-option label="淘宝" value="taobao" />
            <el-option label="Amazon" value="amazon" />
            <el-option label="Shopee" value="shopee" />
            <el-option label="YouTube" value="youtube" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="gettingUrl" @click="handleGetAuthUrl">获取授权链接</el-button>
        </el-form-item>
        <el-form-item v-if="authUrl" label="授权链接">
          <div class="auth-url-box">{{ authUrl }}</div>
          <el-button size="small" style="margin-top:8px" @click="copyAuthUrl">复制链接</el-button>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="authDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  publishContent,
  getPublishLogs,
  getAuthorizedPlatforms,
  getAuthUrl,
} from '../api'

const activeTab = ref('publish')
const publishing = ref(false)
const logsLoading = ref(false)
const authLoading = ref(false)
const gettingUrl = ref(false)

const publishForm = reactive({
  pipeline_id: '',
  video_url: '',
  platforms: [] as string[],
  title: '',
  description: '',
  tags: '',
  scheduled_time: '',
})

const logFilterPlatform = ref('')

const publishLogs = ref<any[]>([])
const logsPage = ref(1)
const logsPageSize = ref(20)
const logsTotal = ref(0)

const platformAuths = ref<any[]>([])

const authDialogVisible = ref(false)
const authUrl = ref('')

const authForm = reactive({
  platform: '',
})

const filteredLogs = computed(() => {
  if (!logFilterPlatform.value) return publishLogs.value
  return publishLogs.value.filter((l: any) => l.platform === logFilterPlatform.value)
})

function logStatusTag(s: string) {
  if (s === 'success' || s === 'completed') return 'success'
  if (s === 'running' || s === 'pending' || s === 'processing') return 'warning'
  if (s === 'failed' || s === 'error') return 'danger'
  return 'info'
}

async function loadLogs() {
  logsLoading.value = true
  try {
    const res: any = await getPublishLogs(logsPage.value, logsPageSize.value)
    const items = res?.items ?? res?.data ?? res ?? []
    publishLogs.value = Array.isArray(items) ? items : []
    logsTotal.value = res?.total ?? publishLogs.value.length
  } catch (e: any) {
    publishLogs.value = []
  } finally {
    logsLoading.value = false
  }
}

function onLogsPageChange(p: number) {
  logsPage.value = p
  loadLogs()
}

async function loadAuthorizedPlatforms() {
  authLoading.value = true
  try {
    const res: any = await getAuthorizedPlatforms()
    const items = res?.platforms ?? res?.items ?? res?.data ?? res ?? []
    platformAuths.value = Array.isArray(items) ? items : []
  } catch (e: any) {
    platformAuths.value = []
  } finally {
    authLoading.value = false
  }
}

async function handlePublish() {
  if (!publishForm.video_url || publishForm.platforms.length === 0) {
    ElMessage.warning('请填写视频 URL 并选择目标平台')
    return
  }
  publishing.value = true
  try {
    await publishContent({
      pipeline_id: publishForm.pipeline_id ? Number(publishForm.pipeline_id) : undefined,
      video_url: publishForm.video_url,
      platforms: publishForm.platforms,
      title: publishForm.title || undefined,
      description: publishForm.description || undefined,
      tags: publishForm.tags ? publishForm.tags.split(',').map((t: string) => t.trim()).filter(Boolean) : undefined,
      scheduled_time: publishForm.scheduled_time || undefined,
    })
    ElMessage.success(publishForm.scheduled_time ? `已设定定时发布: ${publishForm.scheduled_time}` : '发布任务已提交')
    publishForm.pipeline_id = ''
    publishForm.video_url = ''
    publishForm.platforms = []
    publishForm.title = ''
    publishForm.description = ''
    publishForm.tags = ''
    publishForm.scheduled_time = ''
    setTimeout(loadLogs, 1500)
  } catch (e: any) {
    ElMessage.error('发布失败: ' + (e?.response?.data?.detail || e?.message || ''))
  } finally {
    publishing.value = false
  }
}

async function handleGetAuthUrl() {
  if (!authForm.platform) {
    ElMessage.warning('请选择平台')
    return
  }
  gettingUrl.value = true
  authUrl.value = ''
  try {
    const res: any = await getAuthUrl(authForm.platform)
    authUrl.value = res?.auth_url ?? res?.url ?? res?.data?.auth_url ?? ''
    if (!authUrl.value) {
      ElMessage.warning('未能获取授权链接，请确认该平台已配置 OAuth 参数')
    } else {
      ElMessage.success('授权链接已生成')
    }
  } catch (e: any) {
    ElMessage.error('获取授权链接失败: ' + (e?.response?.data?.detail || e?.message || ''))
  } finally {
    gettingUrl.value = false
  }
}

function copyAuthUrl() {
  if (!authUrl.value) return
  navigator.clipboard.writeText(authUrl.value).then(() => {
    ElMessage.success('授权链接已复制到剪贴板')
  })
}

function resetAuthForm() {
  authForm.platform = ''
  authUrl.value = ''
}

onMounted(() => {
  loadLogs()
  loadAuthorizedPlatforms()
})
</script>

<style scoped>
.publish-management { padding: 4px 0; }
.section-card { margin-bottom: 0; }
.muted { color: #c0c4cc; }
.pagination-wrap { margin-top: 16px; display: flex; justify-content: flex-end; }
.auth-url-box {
  padding: 10px 14px;
  background: #f5f7fa;
  border-radius: 6px;
  font-size: 13px;
  word-break: break-all;
  color: #606266;
  line-height: 1.5;
}
</style>
