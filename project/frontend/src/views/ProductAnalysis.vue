<template>
  <div class="product-analysis">
    <el-tabs v-model="activeTab" class="analysis-tabs">
      <el-tab-pane label="选品分析" name="analysis">
        <el-card shadow="never" class="section-card">
          <div class="card-header">
            <div class="header-left">
              <span class="card-title">选品分析结果</span>
              <el-select v-model="analysisPlatform" placeholder="全部平台" size="default" clearable style="width:140px;margin-left:16px">
                <el-option label="抖音" value="douyin" />
                <el-option label="淘宝" value="taobao" />
                <el-option label="Amazon" value="amazon" />
                <el-option label="Shopee" value="shopee" />
              </el-select>
            </div>
            <el-button type="primary" :icon="TrendCharts" :loading="analysisRunning" @click="runAnalysis">执行分析</el-button>
          </div>

          <el-table
            v-loading="loading"
            :data="filteredProducts"
            stripe
            style="width:100%"
            :row-key="(r: ProductRow) => r.id"
            @row-click="(r: ProductRow) => toggleRow(r.id)"
          >
            <el-table-column prop="id" label="ID" width="80" />
            <el-table-column prop="platform_product_id" label="商品编号" width="160" show-overflow-tooltip />
            <el-table-column prop="title" label="商品标题" min-width="200" show-overflow-tooltip />
            <el-table-column prop="platform" label="平台" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="platformType(row.platform)">{{ platformLabel(row.platform) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="price" label="价格" width="100" sortable>
              <template #default="{ row }">{{ row.price != null ? '¥' + Number(row.price).toFixed(2) : '-' }}</template>
            </el-table-column>
            <el-table-column prop="sales_count" label="销量" width="100" sortable />
            <el-table-column prop="rating" label="评分" width="90" sortable>
              <template #default="{ row }">{{ row.rating != null ? Number(row.rating).toFixed(1) : '-' }}</template>
            </el-table-column>
            <el-table-column prop="score" label="综合分" width="100" sortable>
              <template #default="{ row }">
                <span :style="{ color: scoreColor(row.score), fontWeight: 700 }">
                  {{ row.score != null ? Number(row.score).toFixed(1) : '-' }}
                </span>
              </template>
            </el-table-column>
            <el-table-column prop="tier" label="分级" width="90">
              <template #default="{ row }">
                <el-tag v-if="row.tier" size="small" :type="tierType(row.tier)">{{ tierLabel(row.tier) }}</el-tag>
                <span v-else class="muted">-</span>
              </template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="statusType(row.status)">{{ row.status || '-' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="updated_at" label="更新时间" width="170" />
            <template #empty>
              <el-empty description="暂无商品数据，请先在抓取管理中抓取商品" />
            </template>
          </el-table>

          <div class="pagination-wrap" v-if="total > pageSize">
            <el-pagination
              background
              layout="prev, pager, next"
              :total="total"
              :page-size="pageSize"
              :current-page="page"
              @current-change="onPageChange"
            />
          </div>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="配置" name="config">
        <el-card shadow="never" class="section-card config-card">
          <div class="card-header">
            <span class="card-title">评分参数配置</span>
            <el-button type="primary" :loading="configSaving" @click="saveConfig">保存配置</el-button>
          </div>
          <el-form v-loading="configLoading" label-width="140px" class="config-form">
            <el-form-item label="评分阈值">
              <el-slider v-model="config.score_threshold" :min="0" :max="100" show-input />
            </el-form-item>
            <el-form-item label="热度权重">
              <el-slider v-model="config.weight_hotness" :min="0" :max="100" show-input />
            </el-form-item>
            <el-form-item label="转化权重">
              <el-slider v-model="config.weight_conversion" :min="0" :max="100" show-input />
            </el-form-item>
            <el-form-item label="利润权重">
              <el-slider v-model="config.weight_profit" :min="0" :max="100" show-input />
            </el-form-item>
          </el-form>
          <el-alert type="info" :closable="false" show-icon style="margin-top:12px">
            当前权重总和: {{ config.weight_hotness + config.weight_conversion + config.weight_profit }}
            <template v-if="config.weight_hotness + config.weight_conversion + config.weight_profit !== 100">
              <el-tag type="warning" size="small" style="margin-left:8px">建议总和为 100</el-tag>
            </template>
          </el-alert>
        </el-card>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { TrendCharts } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getProducts, analyzeProducts, getScoreConfig, updateScoreConfig } from '../api'

interface ProductRow {
  id: number
  platform: string
  platform_product_id: string
  title: string
  price: number | null
  sales_count: number | null
  rating: number | null
  score: number | null
  tier: string
  status: string
  updated_at: string
}

const activeTab = ref('analysis')
const analysisPlatform = ref('')
const analysisRunning = ref(false)
const configSaving = ref(false)
const configLoading = ref(false)
const loading = ref(false)
const expandedRows = ref<number[]>([])

const page = ref(1)
const pageSize = ref(20)
const total = ref(0)

const config = reactive({
  score_threshold: 70,
  weight_hotness: 40,
  weight_conversion: 35,
  weight_profit: 25,
})

const productList = ref<ProductRow[]>([])

const filteredProducts = computed(() => {
  if (!analysisPlatform.value) return productList.value
  return productList.value.filter(p => p.platform === analysisPlatform.value)
})

function platformType(p: string) {
  const m: Record<string, string> = { douyin: 'danger', taobao: 'warning', amazon: 'success', shopee: 'primary' }
  return m[p] || 'info'
}
function platformLabel(p: string) {
  const m: Record<string, string> = { douyin: '抖音', taobao: '淘宝', amazon: 'Amazon', shopee: 'Shopee' }
  return m[p] || p
}
function tierType(t: string) {
  if (t === 'hot') return 'danger'
  if (t === 'normal') return 'warning'
  if (t === 'cold') return 'info'
  return 'info'
}
function tierLabel(t: string) {
  const m: Record<string, string> = { hot: '热门', normal: '普通', cold: '冷门' }
  return m[t] || t
}
function statusType(s: string) {
  if (s === 'analyzed') return 'success'
  if (s === 'pending') return 'warning'
  if (s === 'failed') return 'danger'
  return 'info'
}
function scoreColor(s: number | null) {
  if (s == null) return '#909399'
  if (s >= 85) return '#F56C6C'
  if (s >= 70) return '#E6A23C'
  return '#909399'
}
function toggleRow(id: number) {
  const idx = expandedRows.value.indexOf(id)
  if (idx >= 0) expandedRows.value.splice(idx, 1)
  else expandedRows.value.push(id)
}

async function loadProducts() {
  loading.value = true
  try {
    const res: any = await getProducts(page.value, pageSize.value)
    const items = res?.items ?? res?.data ?? res ?? []
    productList.value = Array.isArray(items) ? items : []
    total.value = res?.total ?? productList.value.length
  } catch (e: any) {
    ElMessage.error('加载商品列表失败: ' + (e?.message || ''))
    productList.value = []
  } finally {
    loading.value = false
  }
}

function onPageChange(p: number) {
  page.value = p
  loadProducts()
}

async function runAnalysis() {
  analysisRunning.value = true
  try {
    const ids = filteredProducts.value.map(p => p.id)
    const payloadIds = ids.length > 0 ? ids : null
    await analyzeProducts(payloadIds, analysisPlatform.value, 100)
    ElMessage.success('分析任务已提交，请稍后刷新查看结果')
    setTimeout(loadProducts, 2000)
  } catch (e: any) {
    ElMessage.error('执行分析失败: ' + (e?.response?.data?.detail || e?.message || ''))
  } finally {
    analysisRunning.value = false
  }
}

async function loadConfig() {
  configLoading.value = true
  try {
    const res: any = await getScoreConfig()
    const cfg = res?.config ?? res ?? {}
    if (cfg.score_threshold != null) config.score_threshold = Number(cfg.score_threshold)
    if (cfg.weight_hotness != null) {
      const v = Number(cfg.weight_hotness)
      config.weight_hotness = v <= 1 ? Math.round(v * 100) : v
    }
    if (cfg.weight_conversion != null) {
      const v = Number(cfg.weight_conversion)
      config.weight_conversion = v <= 1 ? Math.round(v * 100) : v
    }
    if (cfg.weight_profit != null) {
      const v = Number(cfg.weight_profit)
      config.weight_profit = v <= 1 ? Math.round(v * 100) : v
    }
  } catch (e: any) {
    // 配置可能尚未初始化，保持默认值
  } finally {
    configLoading.value = false
  }
}

async function saveConfig() {
  configSaving.value = true
  try {
    await updateScoreConfig({
      score_threshold: config.score_threshold,
      weight_hotness: config.weight_hotness / 100,
      weight_conversion: config.weight_conversion / 100,
      weight_profit: config.weight_profit / 100,
    })
    ElMessage.success('评分配置已保存')
  } catch (e: any) {
    ElMessage.error('保存配置失败: ' + (e?.response?.data?.detail || e?.message || ''))
  } finally {
    configSaving.value = false
  }
}

onMounted(() => {
  loadProducts()
  loadConfig()
})
</script>

<style scoped>
.product-analysis { padding: 4px 0; }
.analysis-tabs { margin-top: -4px; }
.section-card { border: none; box-shadow: none; }
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.header-left { display: flex; align-items: center; }
.card-title { font-size: 15px; font-weight: 600; color: #303133; }
.pagination-wrap { margin-top: 16px; display: flex; justify-content: flex-end; }
.muted { color: #c0c4cc; }
.config-form { max-width: 640px; }
</style>
