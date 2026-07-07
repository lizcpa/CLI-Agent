<template>
  <div class="agent-console">
    <!-- 配置区 -->
    <el-card class="config-card" shadow="never">
      <div class="config-header">
        <span class="title">Agent 指挥台</span>
        <el-tag v-if="currentTaskId" :type="statusTagType(currentTaskStatus)" size="small">
          {{ statusLabel(currentTaskStatus) }}
        </el-tag>
      </div>

      <el-row :gutter="20">
        <el-col :span="8">
          <div class="field-label">Agent 工具</div>
          <el-select
            v-model="selectedTool"
            placeholder="选择 Agent 工具"
            style="width:100%"
            @change="onToolChange"
          >
            <el-option
              v-for="t in tools"
              :key="t.id"
              :label="t.name"
              :value="t.id"
            >
              <span>{{ t.name }}</span>
              <el-icon v-if="t.available" class="key-ok-icon"><CircleCheckFilled /></el-icon>
              <el-icon v-else class="key-warn-icon"><WarningFilled /></el-icon>
              <span class="option-desc">{{ t.description }}</span>
            </el-option>
          </el-select>
          <div v-if="selectedToolInfo" class="field-hint">
            <code class="cmd-code">{{ selectedToolInfo.cli_command }}</code>
            <span v-if="selectedToolInfo.available" class="key-set">已安装</span>
            <span v-else class="key-missing">未安装</span>
          </div>
          <el-alert
            v-if="selectedToolInfo && !selectedToolInfo.available"
            type="error"
            :closable="false"
            show-icon
            style="margin-top:6px"
          >
            系统未检测到该工具的可执行文件，任务执行时会失败。请先在终端安装：<code class="cmd-code">{{ selectedToolInfo.cli_command.split(' ')[0] }}</code>
          </el-alert>
        </el-col>

        <el-col :span="8">
          <div class="field-label">AI 模型</div>
          <el-select
            v-model="selectedModel"
            placeholder="选择模型"
            style="width:100%"
            filterable
            @change="onModelChange"
          >
            <el-option-group
              v-for="group in modelGroups"
              :key="group.label"
              :label="group.label"
            >
              <el-option
                v-for="m in group.items"
                :key="m.model_id"
                :label="m.model_name"
                :value="m.model_id"
              >
                <span>{{ m.model_name }}</span>
                <el-icon v-if="m.has_key" class="key-ok-icon"><CircleCheckFilled /></el-icon>
                <el-icon v-else class="key-warn-icon"><WarningFilled /></el-icon>
              </el-option>
            </el-option-group>
          </el-select>
          <div v-if="selectedModelInfo" class="field-hint">
            <span>Provider: {{ selectedModelInfo.provider }}</span>
            <span v-if="selectedModelInfo.has_key" class="key-set">Key 已配置</span>
            <span v-else class="key-missing">Key 未配置</span>
          </div>
        </el-col>

        <el-col :span="8">
          <div class="field-label">API Key</div>
          <el-input
            v-model="apiKeyInput"
            :placeholder="selectedModelInfo?.has_key ? '已配置（输入新值覆盖）' : '输入 API Key'"
            type="password"
            show-password
            clearable
          >
            <template #append>
              <el-button :loading="savingKey" :disabled="!selectedModel || !apiKeyInput" @click="handleSaveKey">保存</el-button>
            </template>
          </el-input>
          <div v-if="selectedModelInfo?.base_url" class="field-hint">
            <span>Base URL: {{ selectedModelInfo.base_url }}</span>
          </div>
        </el-col>
      </el-row>
    </el-card>

    <!-- 任务输入区 -->
    <el-card class="task-card" shadow="never">
      <div class="field-label">任务指令</div>
      <el-input
        v-model="taskInstruction"
        type="textarea"
        :rows="4"
        placeholder="输入自然语言任务，例如：&#10;• 帮我抓取抖音爆款商品并生成短视频&#10;• 分析当前商品库中评分最高的 10 个商品&#10;• 用 Veo3 生成一段 10 秒的产品展示视频"
        resize="vertical"
      />
      <div class="task-actions">
        <el-button
          type="primary"
          size="large"
          :loading="executing"
          :disabled="!canExecute"
          @click="handleExecute"
        >
          <el-icon style="margin-right:6px"><VideoPlay /></el-icon>
          {{ loopMode ? '创建定时任务' : '执行任务' }}
        </el-button>
        <el-button
          v-if="currentTaskId && (currentTaskStatus === 'running' || currentTaskStatus === 'pending')"
          type="danger"
          size="large"
          @click="handleCancel"
        >
          取消
        </el-button>
        <div class="loop-mode">
          <el-switch v-model="loopMode" active-text="循环执行" inactive-text="" />
          <el-select v-if="loopMode" v-model="loopInterval" size="small" style="width:130px">
            <el-option label="每 30 分钟" :value="1800" />
            <el-option label="每 1 小时" :value="3600" />
            <el-option label="每 3 小时" :value="10800" />
            <el-option label="每 6 小时" :value="21600" />
            <el-option label="每 12 小时" :value="43200" />
            <el-option label="每 24 小时" :value="86400" />
          </el-select>
        </div>
        <div class="quick-prompts">
          <el-tag
            v-for="p in quickPrompts"
            :key="p"
            size="small"
            class="prompt-tag"
            @click="taskInstruction = p"
          >{{ p }}</el-tag>
        </div>
      </div>
      <el-alert
        v-if="loopMode"
        type="warning"
        :closable="false"
        show-icon
        style="margin-top:12px"
      >
        循环模式：任务将按选定间隔自动重复执行，直到你手动关闭或删除。后端调度器每 30 秒检查一次到期任务。
      </el-alert>
    </el-card>

    <!-- 输出区 -->
    <el-card v-if="currentTaskId || taskOutput" class="output-card" shadow="never">
      <div class="output-header">
        <span class="field-label">执行输出</span>
        <div class="output-actions">
          <el-tag v-if="currentTaskStatus" :type="statusTagType(currentTaskStatus)" size="small">
            {{ statusLabel(currentTaskStatus) }}
          </el-tag>
          <el-button v-if="taskOutput" size="small" @click="copyOutput">复制</el-button>
          <el-button v-if="currentTaskId" size="small" @click="clearOutput">清除</el-button>
        </div>
      </div>
      <div class="output-box" ref="outputBox">
        <pre v-if="taskOutput">{{ taskOutput }}</pre>
        <el-empty v-else description="等待执行..." :image-size="60" />
      </div>
    </el-card>

    <!-- 定时任务 -->
    <el-card class="history-card" shadow="never">
      <div class="output-header">
        <span class="field-label">定时循环任务</span>
        <el-button size="small" :loading="loadingSched" @click="loadScheduled">刷新</el-button>
      </div>
      <el-table v-loading="loadingSched" :data="scheduledTasks" stripe size="small">
        <el-table-column prop="agent_tool_name" label="Agent" width="120" />
        <el-table-column prop="model_name" label="模型" width="150" />
        <el-table-column prop="task_instruction" label="任务" min-width="200" show-overflow-tooltip />
        <el-table-column label="间隔" width="100">
          <template #default="{ row }">{{ formatInterval(row.interval_seconds) }}</template>
        </el-table-column>
        <el-table-column prop="run_count" label="执行次数" width="90" />
        <el-table-column prop="next_run_at" label="下次执行" width="170">
          <template #default="{ row }">{{ row.next_run_at || '-' }}</template>
        </el-table-column>
        <el-table-column prop="last_run_at" label="上次执行" width="170">
          <template #default="{ row }">{{ row.last_run_at || '-' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-switch
              :model-value="row.enabled"
              size="small"
              @change="(val: boolean) => handleToggleSched(row.id, val)"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button type="danger" link size="small" @click="handleDeleteSched(row.id)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无定时任务，开启循环执行后创建" :image-size="60" />
        </template>
      </el-table>
    </el-card>

    <!-- 历史任务 -->
    <el-card class="history-card" shadow="never">
      <div class="output-header">
        <span class="field-label">历史任务</span>
        <el-button size="small" :loading="loadingHistory" @click="loadHistory">刷新</el-button>
      </div>
      <el-table :data="taskHistory" stripe size="small" @row-click="handleHistoryClick">
        <el-table-column prop="agent_tool_name" label="Agent" width="120" />
        <el-table-column prop="model_name" label="模型" width="160" />
        <el-table-column prop="task_instruction" label="任务" min-width="250" show-overflow-tooltip />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="exit_code" label="退出码" width="80" />
        <el-table-column prop="created_at" label="时间" width="170" />
        <template #empty>
          <el-empty description="暂无历史任务" :image-size="60" />
        </template>
      </el-table>
    </el-card>

    <!-- 添加自定义 Agent 工具 -->
    <el-dialog v-model="addToolDialog" title="添加 Agent 工具" width="560px" destroy-on-close @closed="resetToolForm">
      <el-form :model="toolForm" label-width="100px">
        <el-form-item label="工具名称">
          <el-input v-model="toolForm.name" placeholder="如: My Custom Agent" />
        </el-form-item>
        <el-form-item label="CLI 命令">
          <el-input v-model="toolForm.cli_command" type="textarea" :rows="3" placeholder="命令模板，支持 {task} 和 {model} 占位符&#10;如: myagent --prompt {task} --model {model}" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="toolForm.description" placeholder="工具说明" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addToolDialog = false">取消</el-button>
        <el-button type="primary" :loading="addingTool" @click="handleAddTool">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { CircleCheckFilled, WarningFilled, VideoPlay } from '@element-plus/icons-vue'
import {
  getAgentTools, addAgentTool,
  getAgentModels, saveModelKey,
  executeAgentTask, getAgentTasks, getAgentTaskOutput, cancelAgentTask,
  getScheduledTasks, createScheduledTask, toggleScheduledTask, deleteScheduledTask,
} from '../api'

const tools = ref<any[]>([])
const models = ref<any[]>([])
const taskHistory = ref<any[]>([])
const scheduledTasks = ref<any[]>([])

const selectedTool = ref('')
const selectedModel = ref('')
const apiKeyInput = ref('')
const taskInstruction = ref('')

const savingKey = ref(false)
const executing = ref(false)
const loadingHistory = ref(false)
const loadingSched = ref(false)
const addingTool = ref(false)
const addToolDialog = ref(false)

const loopMode = ref(false)
const loopInterval = ref(3600)

const currentTaskId = ref('')
const currentTaskStatus = ref('')
const taskOutput = ref('')
const outputBox = ref<HTMLElement | null>(null)

let pollTimer: ReturnType<typeof setInterval> | null = null

const toolForm = reactive({ name: '', cli_command: '', description: '' })

const quickPrompts = [
  '帮我抓取抖音爆款商品并生成短视频',
  '分析当前商品库中评分最高的 10 个商品',
  '生成一段产品营销文案',
]

const selectedToolInfo = computed(() => tools.value.find(t => t.id === selectedTool.value))
const selectedModelInfo = computed(() => models.value.find(m => m.model_id === selectedModel.value))

const canExecute = computed(() => {
  if (!selectedTool.value || !selectedModel.value || !taskInstruction.value.trim()) return false
  if (executing.value) return false
  return true
})

const modelGroups = computed(() => {
  const groups: Record<string, any[]> = {}
  for (const m of models.value) {
    const p = m.provider || 'other'
    if (!groups[p]) groups[p] = []
    groups[p].push(m)
  }
  const labels: Record<string, string> = {
    anthropic: 'Anthropic', openai: 'OpenAI', google: 'Google',
    volcengine: '字节火山引擎 (豆包)', deepseek: 'DeepSeek', alibaba: '阿里通义',
    stability: 'Stability AI', custom: '自定义',
  }
  return Object.entries(groups).map(([k, items]) => ({
    label: labels[k] || k,
    items,
  }))
})

function statusTagType(s: string) {
  if (s === 'completed') return 'success'
  if (s === 'running' || s === 'pending') return 'warning'
  if (s === 'failed') return 'danger'
  if (s === 'cancelled') return 'info'
  return 'info'
}
function statusLabel(s: string) {
  const m: Record<string, string> = {
    completed: '已完成', running: '运行中', pending: '等待中',
    failed: '失败', cancelled: '已取消',
  }
  return m[s] || s
}

async function loadTools() {
  try {
    const res: any = await getAgentTools()
    tools.value = res ?? []
    if (tools.value.length > 0 && !selectedTool.value) {
      selectedTool.value = tools.value[0].id
    }
  } catch { tools.value = [] }
}

async function loadModels() {
  try {
    const res: any = await getAgentModels()
    models.value = res ?? []
  } catch { models.value = [] }
}

async function loadHistory() {
  loadingHistory.value = true
  try {
    const res: any = await getAgentTasks()
    taskHistory.value = res ?? []
  } catch { taskHistory.value = [] }
  finally { loadingHistory.value = false }
}

function onToolChange() {}
function onModelChange() {
  apiKeyInput.value = ''
}

async function handleSaveKey() {
  if (!selectedModel.value || !apiKeyInput.value) return
  savingKey.value = true
  try {
    const baseUrl = selectedModelInfo.value?.base_url || ''
    await saveModelKey(selectedModel.value, apiKeyInput.value, baseUrl)
    ElMessage.success('API Key 已保存')
    apiKeyInput.value = ''
    await loadModels()
  } catch (e: any) {
    ElMessage.error('保存失败: ' + (e?.response?.data?.detail || e?.message || ''))
  } finally {
    savingKey.value = false
  }
}

async function handleExecute() {
  if (!canExecute.value) return
  executing.value = true
  taskOutput.value = ''
  currentTaskStatus.value = ''
  try {
    if (loopMode.value) {
      await createScheduledTask({
        agent_tool_id: selectedTool.value,
        model_id: selectedModel.value,
        task_instruction: taskInstruction.value,
        interval_seconds: loopInterval.value,
      })
      ElMessage.success(`定时任务已创建，每 ${formatInterval(loopInterval.value)} 执行一次`)
      await loadScheduled()
    } else {
      const res: any = await executeAgentTask({
        agent_tool_id: selectedTool.value,
        model_id: selectedModel.value,
        task_instruction: taskInstruction.value,
      })
      currentTaskId.value = res?.task_id || ''
      currentTaskStatus.value = 'running'
      startPolling()
      ElMessage.success('任务已启动')
    }
  } catch (e: any) {
    ElMessage.error('操作失败: ' + (e?.response?.data?.detail || e?.message || ''))
  } finally {
    executing.value = false
  }
}

async function handleCancel() {
  if (!currentTaskId.value) return
  try {
    await cancelAgentTask(currentTaskId.value)
    ElMessage.info('已发送取消信号')
  } catch (e: any) {
    ElMessage.error('取消失败: ' + (e?.message || ''))
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    if (!currentTaskId.value) { stopPolling(); return }
    try {
      const res: any = await getAgentTaskOutput(currentTaskId.value)
      taskOutput.value = res?.output || ''
      currentTaskStatus.value = res?.status || ''
      nextTick(() => {
        if (outputBox.value) outputBox.value.scrollTop = outputBox.value.scrollHeight
      })
      if (res?.status === 'completed' || res?.status === 'failed' || res?.status === 'cancelled') {
        stopPolling()
        loadHistory()
      }
    } catch { /* ignore */ }
  }, 1500)
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

function clearOutput() {
  currentTaskId.value = ''
  currentTaskStatus.value = ''
  taskOutput.value = ''
  stopPolling()
}

function copyOutput() {
  if (!taskOutput.value) return
  navigator.clipboard.writeText(taskOutput.value).then(() => ElMessage.success('已复制到剪贴板'))
}

async function handleHistoryClick(row: any) {
  currentTaskId.value = row.id
  currentTaskStatus.value = row.status
  try {
    const res: any = await getAgentTaskOutput(row.id)
    taskOutput.value = res?.output || '（无输出）'
  } catch {
    taskOutput.value = '（加载失败）'
  }
  if (row.status === 'running' || row.status === 'pending') {
    startPolling()
  }
}

async function handleAddTool() {
  if (!toolForm.name || !toolForm.cli_command) {
    ElMessage.warning('请填写工具名称和 CLI 命令')
    return
  }
  addingTool.value = true
  try {
    await addAgentTool({
      name: toolForm.name,
      cli_command: toolForm.cli_command,
      description: toolForm.description,
    })
    ElMessage.success('工具已添加')
    addToolDialog.value = false
    await loadTools()
  } catch (e: any) {
    ElMessage.error('添加失败: ' + (e?.response?.data?.detail || e?.message || ''))
  } finally {
    addingTool.value = false
  }
}

function resetToolForm() {
  toolForm.name = ''
  toolForm.cli_command = ''
  toolForm.description = ''
}

function formatInterval(seconds: number): string {
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`
  if (seconds < 86400) return `${Math.round(seconds / 3600)} 小时`
  return `${Math.round(seconds / 86400)} 天`
}

async function loadScheduled() {
  loadingSched.value = true
  try {
    const res: any = await getScheduledTasks()
    scheduledTasks.value = res ?? []
  } catch { scheduledTasks.value = [] }
  finally { loadingSched.value = false }
}

async function handleToggleSched(schedId: string, enabled: boolean) {
  try {
    await toggleScheduledTask(schedId, enabled)
    ElMessage.success(enabled ? '已启用' : '已暂停')
    await loadScheduled()
  } catch (e: any) {
    ElMessage.error('操作失败: ' + (e?.message || ''))
  }
}

async function handleDeleteSched(schedId: string) {
  try {
    await deleteScheduledTask(schedId)
    ElMessage.success('已删除')
    await loadScheduled()
  } catch (e: any) {
    ElMessage.error('删除失败: ' + (e?.message || ''))
  }
}

onMounted(() => {
  loadTools()
  loadModels()
  loadHistory()
  loadScheduled()
})

onUnmounted(() => {
  stopPolling()
})
</script>

<style scoped>
.agent-console { padding: 0; display: flex; flex-direction: column; gap: 16px; }

.config-card, .task-card, .output-card, .history-card {
  border: 1px solid #ebeef5;
  border-radius: 8px;
}
.config-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.config-header .title { font-size: 18px; font-weight: 700; color: #303133; }

.field-label {
  font-size: 13px; font-weight: 600; color: #606266;
  margin-bottom: 6px;
}
.field-hint {
  margin-top: 4px;
  font-size: 12px; color: #909399;
  display: flex; gap: 12px; align-items: center;
}
.cmd-code {
  font-size: 12px; background: #f5f7fa;
  padding: 2px 8px; border-radius: 4px; color: #606266;
}
.key-set { color: #67C23A; }
.key-missing { color: #E6A23C; }
.option-desc {
  float: right; font-size: 12px; color: #909399;
  max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.key-ok-icon { color: #67C23A; margin-left: 4px; }
.key-warn-icon { color: #E6A23C; margin-left: 4px; }

.task-actions {
  margin-top: 12px;
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
}
.loop-mode {
  display: flex; align-items: center; gap: 8px;
  padding: 4px 10px;
  background: #f5f7fa;
  border-radius: 6px;
}
.quick-prompts { display: flex; gap: 6px; flex-wrap: wrap; margin-left: auto; }
.prompt-tag { cursor: pointer; }
.prompt-tag:hover { color: #409EFF; border-color: #409EFF; }

.output-header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 8px;
}
.output-actions { display: flex; gap: 8px; align-items: center; }
.output-box {
  background: #1e1e1e;
  border-radius: 6px;
  padding: 16px;
  max-height: 400px;
  overflow-y: auto;
  min-height: 120px;
}
.output-box pre {
  color: #d4d4d4;
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
}
.output-box .el-empty { background: transparent; }

:deep(.output-box .el-empty__description p) { color: #666; }
</style>
