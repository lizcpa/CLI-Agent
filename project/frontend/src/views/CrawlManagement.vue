<template>
  <div class="crawl-management">
    <el-tabs v-model="activeTab" class="crawl-tabs" @tab-change="onTabChange">
      <el-tab-pane label="采集任务" name="tasks">
        <el-card shadow="never" class="section-card">
          <div class="card-header">
            <span class="card-title">采集任务列表</span>
            <el-button type="primary" :icon="Plus" @click="openTaskDialog">新建采集</el-button>
          </div>
          <el-empty v-if="taskList.length === 0 && !taskLoading" description="暂无采集任务" :image-size="80" />
          <el-table :data="taskList" stripe v-loading="taskLoading" style="width:100%">
            <el-table-column prop="job_id" label="任务ID" width="200" />
            <el-table-column prop="platform" label="平台" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="platformType(row.platform)">{{ platformLabel(row.platform) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="keyword" label="关键词" width="140" />
            <el-table-column prop="status" label="状态" width="110">
              <template #default="{ row }">
                <el-tag size="small" :type="taskStatusType(row.status)">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="进度" width="160">
              <template #default="{ row }">
                <el-progress :percentage="Number(row.progress) || 0" :status="row.status === 'completed' ? 'success' : ''" :stroke-width="16" />
              </template>
            </el-table-column>
            <el-table-column prop="products_found" label="采集商品数" width="110" />
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="采集计划" name="plans">
        <el-card shadow="never" class="section-card">
          <div class="card-header">
            <span class="card-title">采集计划列表</span>
            <el-button type="primary" :icon="Plus" @click="openPlanDialog">新建计划</el-button>
          </div>
          <el-empty v-if="planList.length === 0 && !planLoading" description="暂无采集计划" :image-size="80" />
          <el-table :data="planList" stripe v-loading="planLoading" style="width:100%">
            <el-table-column prop="name" label="计划名称" width="160" />
            <el-table-column prop="platform" label="平台" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="platformType(row.platform)">{{ platformLabel(row.platform) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="keyword" label="关键词" width="140" />
            <el-table-column prop="cron_expression" label="Cron" width="150" />
            <el-table-column prop="enabled" label="启用" width="80">
              <template #default="{ row }">
                <el-switch v-model="row.enabled" size="small" @change="togglePlan(row)" />
              </template>
            </el-table-column>
            <el-table-column prop="last_run_at" label="上次运行" width="170" />
            <el-table-column prop="next_run_at" label="下次运行" width="170" />
            <el-table-column label="操作" width="120">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="editPlan(row)">编辑</el-button>
                <el-button link type="danger" size="small" @click="deletePlan(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="taskDialogVisible" title="新建采集任务" width="480px" :close-on-click-modal="false">
      <el-form ref="taskFormRef" :model="taskForm" :rules="taskRules" label-width="90px">
        <el-form-item label="平台" prop="platform">
          <el-select v-model="taskForm.platform" placeholder="选择平台" style="width:100%">
            <el-option label="抖音" value="douyin" />
            <el-option label="淘宝" value="taobao" />
            <el-option label="Amazon" value="amazon" />
            <el-option label="Shopee" value="shopee" />
          </el-select>
        </el-form-item>
        <el-form-item label="关键词" prop="keyword">
          <el-input v-model="taskForm.keyword" placeholder="输入搜索关键词" />
        </el-form-item>
        <el-form-item label="最大数量" prop="max_count">
          <el-input-number v-model="taskForm.max_count" :min="1" :max="10000" style="width:100%" />
        </el-form-item>
        <el-form-item label="排序方式" prop="sort_by">
          <el-select v-model="taskForm.sort_by" placeholder="选择排序方式" style="width:100%">
            <el-option label="销量" value="sales" />
            <el-option label="价格" value="price" />
            <el-option label="评分" value="rating" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="taskDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="taskSubmitting" @click="submitTask">提交采集</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="planDialogVisible" :title="editingPlanId ? '编辑采集计划' : '新建采集计划'" width="520px" :close-on-click-modal="false">
      <el-form ref="planFormRef" :model="planForm" :rules="planRules" label-width="100px">
        <el-form-item label="计划名称" prop="name">
          <el-input v-model="planForm.name" placeholder="输入计划名称" />
        </el-form-item>
        <el-form-item label="平台" prop="platform">
          <el-select v-model="planForm.platform" placeholder="选择平台" style="width:100%">
            <el-option label="抖音" value="douyin" />
            <el-option label="淘宝" value="taobao" />
            <el-option label="Amazon" value="amazon" />
            <el-option label="Shopee" value="shopee" />
          </el-select>
        </el-form-item>
        <el-form-item label="关键词" prop="keyword">
          <el-input v-model="planForm.keyword" placeholder="输入搜索关键词" />
        </el-form-item>
        <el-form-item label="Cron 表达式" prop="cron_expression">
          <el-input v-model="planForm.cron_expression" placeholder="如 0 */6 * * *" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="planForm.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="planDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="planSubmitting" @click="submitPlan">保存计划</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { Plus } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { getCrawlJobs, createCrawlJob, getCrawlPlans, createCrawlPlan, updateCrawlPlan, deleteCrawlPlan } from '../api'

const activeTab = ref('tasks')
const taskLoading = ref(false)
const planLoading = ref(false)
const taskSubmitting = ref(false)
const planSubmitting = ref(false)

const taskDialogVisible = ref(false)
const planDialogVisible = ref(false)
const taskFormRef = ref<FormInstance>()
const planFormRef = ref<FormInstance>()
const editingPlanId = ref<string | null>(null)

const taskForm = reactive({ platform: 'douyin', keyword: '', max_count: 100, sort_by: 'sales' })
const planForm = reactive({ name: '', platform: 'douyin', keyword: '', cron_expression: '0 */6 * * *', enabled: true })

const taskRules: FormRules = {
  platform: [{ required: true, message: '请选择平台', trigger: 'change' }],
  keyword: [{ required: true, message: '请输入关键词', trigger: 'blur' }],
}
const planRules: FormRules = {
  name: [{ required: true, message: '请输入计划名称', trigger: 'blur' }],
  platform: [{ required: true, message: '请选择平台', trigger: 'change' }],
  keyword: [{ required: true, message: '请输入关键词', trigger: 'blur' }],
}

const taskList = ref<any[]>([])
const planList = ref<any[]>([])

function platformType(platform: string) {
  const map: Record<string, string> = { douyin: 'danger', taobao: 'warning', amazon: 'success', shopee: '' }
  return map[platform] || 'info'
}
function platformLabel(platform: string) {
  const map: Record<string, string> = { douyin: '抖音', taobao: '淘宝', amazon: 'Amazon', shopee: 'Shopee' }
  return map[platform] || platform
}
function taskStatusType(status: string) {
  if (status === 'completed') return 'success'
  if (status === 'running' || status === 'queued') return 'warning'
  if (status === 'failed') return 'danger'
  return 'info'
}

async function loadTasks() {
  taskLoading.value = true
  try {
    const data = await getCrawlJobs()
    taskList.value = data?.items || []
  } catch (e) { console.error(e) } finally { taskLoading.value = false }
}
async function loadPlans() {
  planLoading.value = true
  try {
    const data = await getCrawlPlans()
    planList.value = data?.items || []
  } catch (e) { console.error(e) } finally { planLoading.value = false }
}

function onTabChange(tab: string) {
  if (tab === 'plans' && planList.value.length === 0) loadPlans()
}

function openTaskDialog() {
  Object.assign(taskForm, { platform: 'douyin', keyword: '', max_count: 100, sort_by: 'sales' })
  taskDialogVisible.value = true
}
async function submitTask() {
  const valid = await taskFormRef.value?.validate().catch(() => false)
  if (!valid) return
  taskSubmitting.value = true
  try {
    await createCrawlJob(taskForm.platform, taskForm.keyword, taskForm.max_count, taskForm.sort_by)
    ElMessage.success('采集任务已创建')
    taskDialogVisible.value = false
    loadTasks()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '创建失败')
  } finally { taskSubmitting.value = false }
}

function openPlanDialog() {
  editingPlanId.value = null
  Object.assign(planForm, { name: '', platform: 'douyin', keyword: '', cron_expression: '0 */6 * * *', enabled: true })
  planDialogVisible.value = true
}
function editPlan(row: any) {
  editingPlanId.value = row.plan_id
  Object.assign(planForm, { name: row.name, platform: row.platform, keyword: row.keyword, cron_expression: row.cron_expression || '', enabled: row.enabled })
  planDialogVisible.value = true
}
async function togglePlan(row: any) {
  try {
    await updateCrawlPlan(row.plan_id, { enabled: row.enabled })
    ElMessage.success(row.enabled ? '已启用' : '已禁用')
  } catch (e) { ElMessage.error('操作失败'); row.enabled = !row.enabled }
}
async function deletePlan(row: any) {
  try {
    await ElMessageBox.confirm('确定删除该采集计划吗？', '确认', { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' })
    await deleteCrawlPlan(row.plan_id)
    ElMessage.success('计划已删除')
    loadPlans()
  } catch (e) { /* cancelled */ }
}
async function submitPlan() {
  const valid = await planFormRef.value?.validate().catch(() => false)
  if (!valid) return
  planSubmitting.value = true
  try {
    if (editingPlanId.value) {
      await updateCrawlPlan(editingPlanId.value, { name: planForm.name, keyword: planForm.keyword, cron_expression: planForm.cron_expression, enabled: planForm.enabled })
      ElMessage.success('计划已更新')
    } else {
      await createCrawlPlan({ name: planForm.name, platform: planForm.platform, keyword: planForm.keyword, cron_expression: planForm.cron_expression, enabled: planForm.enabled ? 1 : 0 })
      ElMessage.success('计划已创建')
    }
    planDialogVisible.value = false
    loadPlans()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '操作失败')
  } finally { planSubmitting.value = false }
}

onMounted(loadTasks)
</script>

<style scoped>
.crawl-management { padding: 0; }
.crawl-tabs { margin-top: -4px; }
.section-card { border: none; box-shadow: none; }
.card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.card-title { font-size: 15px; font-weight: 600; color: #303133; }
</style>
