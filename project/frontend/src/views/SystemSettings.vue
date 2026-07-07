<template>
  <div class="system-settings">
    <el-tabs v-model="activeTab">
      <el-tab-pane label="API Keys" name="keys">
        <el-card class="section-card">
          <div style="margin-bottom:16px;display:flex;justify-content:space-between;align-items:center">
            <span class="tab-title">API 密钥管理</span>
            <div>
              <el-button :loading="keysLoading" @click="loadApiKeys">刷新</el-button>
              <el-button type="primary" @click="keyDialogVisible = true">创建 Key</el-button>
            </div>
          </div>
          <el-table v-loading="keysLoading" :data="apiKeys" stripe>
            <el-table-column prop="id" label="ID" width="60" />
            <el-table-column prop="name" label="名称" width="150" />
            <el-table-column prop="prefix" label="密钥前缀" width="200">
              <template #default="{ row }">
                <code class="key-code">{{ row.prefix }}</code>
              </template>
            </el-table-column>
            <el-table-column prop="scopes" label="权限范围" min-width="220">
              <template #default="{ row }">
                <el-tag v-for="s in (row.scopes || [])" :key="s" size="small" style="margin-right:4px;margin-bottom:2px">{{ s }}</el-tag>
                <span v-if="!row.scopes || row.scopes.length === 0" class="muted">-</span>
              </template>
            </el-table-column>
            <el-table-column prop="enabled" label="状态" width="90">
              <template #default="{ row }">
                <el-switch :model-value="!!row.enabled" size="small" @change="handleToggleKey(row)" />
              </template>
            </el-table-column>
            <el-table-column prop="last_used_at" label="最后使用" width="170">
              <template #default="{ row }">{{ row.last_used_at || '-' }}</template>
            </el-table-column>
            <el-table-column prop="created_at" label="创建时间" width="170" />
            <el-table-column label="操作" width="100">
              <template #default="{ row }">
                <el-button type="danger" link size="small" @click="handleDeleteKey(row)">删除</el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无 API Key" />
            </template>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="模型管理" name="models">
        <el-card class="section-card">
          <div style="margin-bottom:16px;display:flex;justify-content:space-between;align-items:center">
            <span class="tab-title">可用 AI 模型</span>
            <el-button :loading="modelsLoading" @click="loadModels">刷新</el-button>
          </div>
          <el-table v-loading="modelsLoading" :data="models" stripe>
            <el-table-column prop="id" label="模型ID" min-width="180" />
            <el-table-column prop="type" label="类型" width="100">
              <template #default="{ row }">
                <el-tag size="small">{{ row.type || '-' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="name" label="名称" min-width="160" />
            <el-table-column prop="status" label="健康状态" width="110">
              <template #default="{ row }">
                <el-tag :type="modelStatusType(row)" size="small">{{ modelStatusLabel(row) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="能力" min-width="200">
              <template #default="{ row }">
                <el-tag v-for="cap in rowCapabilities(row)" :key="cap" size="small" style="margin-right:6px;margin-bottom:4px">{{ cap }}</el-tag>
                <span v-if="rowCapabilities(row).length === 0" class="muted">-</span>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无可用模型" />
            </template>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="平台配置" name="configs">
        <el-card class="section-card">
          <div style="margin-bottom:16px;display:flex;justify-content:space-between;align-items:center">
            <span class="tab-title">平台配置</span>
            <div>
              <el-button :loading="configsLoading" @click="loadPlatformConfigs">刷新</el-button>
              <el-button type="primary" @click="configDialogVisible = true">添加配置</el-button>
            </div>
          </div>
          <el-table v-loading="configsLoading" :data="platformConfigs" stripe>
            <el-table-column prop="id" label="ID" width="60" />
            <el-table-column prop="platform" label="平台" width="120">
              <template #default="{ row }">
                <el-tag size="small">{{ row.platform }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="config_key" label="配置键" width="240">
              <template #default="{ row }">
                <code class="key-code">{{ row.config_key }}</code>
              </template>
            </el-table-column>
            <el-table-column prop="config_value" label="配置值" min-width="200" show-overflow-tooltip />
            <el-table-column prop="description" label="描述" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">{{ row.description || '-' }}</template>
            </el-table-column>
            <el-table-column prop="updated_at" label="更新时间" width="170">
              <template #default="{ row }">{{ row.updated_at || '-' }}</template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无平台配置" />
            </template>
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="keyDialogVisible" title="创建 API Key" width="520px" destroy-on-close @closed="resetKeyForm">
      <el-form :model="keyForm" label-width="100px">
        <el-form-item label="名称">
          <el-input v-model="keyForm.name" placeholder="输入 Key 名称" />
        </el-form-item>
        <el-form-item label="权限范围">
          <el-select v-model="keyForm.scopes" multiple placeholder="选择权限" style="width:100%">
            <el-option label="读取" value="read" />
            <el-option label="写入" value="write" />
            <el-option label="删除" value="delete" />
            <el-option label="管理" value="admin" />
          </el-select>
        </el-form-item>
        <el-form-item label="最大并发">
          <el-input-number v-model="keyForm.max_concurrency" :min="1" :max="100" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="keyDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="creatingKey" @click="handleCreateKey">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="createdKeyVisible" title="Key 创建成功" width="520px" destroy-on-close>
      <div class="key-reveal-box">
        <p style="color:#606266;margin-bottom:8px">请妥善保存，此密钥仅显示一次：</p>
        <div class="full-key">{{ createdFullKey }}</div>
      </div>
      <template #footer>
        <el-button @click="copyFullKey">复制密钥</el-button>
        <el-button type="primary" @click="createdKeyVisible = false">我已保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="configDialogVisible" title="添加平台配置" width="520px" destroy-on-close @closed="resetConfigForm">
      <el-form :model="configForm" label-width="80px">
        <el-form-item label="平台">
          <el-select v-model="configForm.platform" placeholder="选择平台" style="width:100%">
            <el-option label="抖音" value="douyin" />
            <el-option label="淘宝" value="taobao" />
            <el-option label="Amazon" value="amazon" />
            <el-option label="Shopee" value="shopee" />
            <el-option label="YouTube" value="youtube" />
          </el-select>
        </el-form-item>
        <el-form-item label="配置键">
          <el-input v-model="configForm.config_key" placeholder="例如: api_endpoint" />
        </el-form-item>
        <el-form-item label="配置值">
          <el-input v-model="configForm.config_value" type="textarea" :rows="3" placeholder="配置值" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="configForm.description" placeholder="配置说明" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="configDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingConfig" @click="handleAddConfig">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getApiKeys,
  createApiKey,
  toggleApiKey,
  deleteApiKey,
  getPlatformConfigs,
  updatePlatformConfig,
  getModels,
} from '../api'

const activeTab = ref('keys')

const keysLoading = ref(false)
const modelsLoading = ref(false)
const configsLoading = ref(false)
const creatingKey = ref(false)
const savingConfig = ref(false)

const apiKeys = ref<any[]>([])
const models = ref<any[]>([])
const platformConfigs = ref<any[]>([])

const keyDialogVisible = ref(false)
const createdKeyVisible = ref(false)
const createdFullKey = ref('')
const configDialogVisible = ref(false)

const keyForm = reactive({
  name: '',
  scopes: [] as string[],
  max_concurrency: 5,
})

const configForm = reactive({
  platform: '',
  config_key: '',
  config_value: '',
  description: '',
})

function rowCapabilities(row: any) {
  const caps = row.capabilities || row.supported_features || {}
  if (Array.isArray(caps)) return caps
  if (typeof caps === 'object' && caps !== null) {
    return Object.entries(caps).map(([k, v]) => `${k}: ${v}`)
  }
  return []
}
function modelStatusType(row: any) {
  const s = row.status || (row.is_healthy ? 'healthy' : 'unhealthy')
  if (s === 'healthy' || s === 'active' || s === 'ready') return 'success'
  if (s === 'unhealthy' || s === 'error' || s === 'failed') return 'danger'
  return 'info'
}
function modelStatusLabel(row: any) {
  const s = row.status || (row.is_healthy ? 'healthy' : 'unhealthy')
  if (s === 'healthy' || s === 'active' || s === 'ready') return '正常'
  if (s === 'unhealthy' || s === 'error' || s === 'failed') return '异常'
  return s || '未知'
}

async function loadApiKeys() {
  keysLoading.value = true
  try {
    const res: any = await getApiKeys()
    const list = res?.keys ?? res?.items ?? res?.data ?? res ?? []
    apiKeys.value = Array.isArray(list) ? list : []
  } catch (e: any) {
    apiKeys.value = []
  } finally {
    keysLoading.value = false
  }
}

async function loadModels() {
  modelsLoading.value = true
  try {
    const res: any = await getModels()
    const list = res?.models ?? res?.items ?? res?.data ?? res ?? []
    models.value = Array.isArray(list) ? list : []
  } catch (e: any) {
    models.value = []
  } finally {
    modelsLoading.value = false
  }
}

async function loadPlatformConfigs() {
  configsLoading.value = true
  try {
    const res: any = await getPlatformConfigs()
    const list = res?.configs ?? res?.items ?? res?.data ?? res ?? []
    platformConfigs.value = Array.isArray(list) ? list : []
  } catch (e: any) {
    platformConfigs.value = []
  } finally {
    configsLoading.value = false
  }
}

async function handleToggleKey(row: any) {
  try {
    await toggleApiKey(row.id)
    row.enabled = !row.enabled
    ElMessage.success(`${row.name} 已${row.enabled ? '启用' : '禁用'}`)
  } catch (e: any) {
    ElMessage.error('切换失败: ' + (e?.response?.data?.detail || e?.message || ''))
  }
}

async function handleDeleteKey(row: any) {
  try {
    await ElMessageBox.confirm(`确定删除 Key "${row.name}"？此操作不可恢复。`, '确认删除', { type: 'warning' })
    await deleteApiKey(row.id)
    ElMessage.success(`Key "${row.name}" 已删除`)
    loadApiKeys()
  } catch (e: any) {
    if (e !== 'cancel' && e?.message !== 'cancel') {
      ElMessage.error('删除失败: ' + (e?.response?.data?.detail || e?.message || ''))
    }
  }
}

async function handleCreateKey() {
  if (!keyForm.name || keyForm.scopes.length === 0) {
    ElMessage.warning('请填写名称并选择权限范围')
    return
  }
  creatingKey.value = true
  try {
    const res: any = await createApiKey(keyForm.name, keyForm.scopes, keyForm.max_concurrency)
    createdFullKey.value = res?.api_key ?? res?.key ?? ''
    keyDialogVisible.value = false
    createdKeyVisible.value = true
    loadApiKeys()
  } catch (e: any) {
    ElMessage.error('创建失败: ' + (e?.response?.data?.detail || e?.message || ''))
  } finally {
    creatingKey.value = false
  }
}

function copyFullKey() {
  if (!createdFullKey.value) return
  navigator.clipboard.writeText(createdFullKey.value).then(() => {
    ElMessage.success('密钥已复制到剪贴板')
  })
}

function resetKeyForm() {
  keyForm.name = ''
  keyForm.scopes = []
  keyForm.max_concurrency = 5
}

async function handleAddConfig() {
  if (!configForm.platform || !configForm.config_key) {
    ElMessage.warning('请填写平台和配置键')
    return
  }
  savingConfig.value = true
  try {
    await updatePlatformConfig(
      configForm.platform,
      configForm.config_key,
      configForm.config_value,
      configForm.description,
    )
    ElMessage.success('配置已保存')
    configDialogVisible.value = false
    loadPlatformConfigs()
  } catch (e: any) {
    ElMessage.error('保存失败: ' + (e?.response?.data?.detail || e?.message || ''))
  } finally {
    savingConfig.value = false
  }
}

function resetConfigForm() {
  configForm.platform = ''
  configForm.config_key = ''
  configForm.config_value = ''
  configForm.description = ''
}

onMounted(() => {
  loadApiKeys()
  loadModels()
  loadPlatformConfigs()
})
</script>

<style scoped>
.system-settings { padding: 4px 0; }
.section-card { margin-bottom: 0; }
.tab-title { font-size: 15px; font-weight: 600; color: #303133; }
.key-code {
  font-size: 13px;
  background: #f5f7fa;
  padding: 2px 8px;
  border-radius: 4px;
  color: #606266;
}
.muted { color: #c0c4cc; }
.key-reveal-box {
  padding: 20px;
  background: #fef0f0;
  border: 1px solid #fde2e2;
  border-radius: 8px;
  text-align: center;
}
.full-key {
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 14px;
  background: #fff;
  padding: 12px 16px;
  border-radius: 6px;
  word-break: break-all;
  color: #303133;
  letter-spacing: 0.5px;
}
</style>
