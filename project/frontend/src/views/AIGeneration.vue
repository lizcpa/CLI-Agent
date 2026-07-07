<template>
  <div class="ai-generation">
    <el-tabs v-model="activeTab" class="gen-tabs">
      <el-tab-pane label="文案生成" name="text">
        <el-card shadow="never" class="section-card">
          <div class="card-title">AI 文案生成器</div>
          <el-form label-width="110px" class="gen-form">
            <el-form-item label="商品ID">
              <el-input v-model="textForm.product_id" placeholder="输入商品ID（数字）" />
            </el-form-item>
            <el-form-item label="商品标题">
              <el-input v-model="textForm.product_title" placeholder="输入商品标题" />
            </el-form-item>
            <el-form-item label="关键词">
              <div style="display:flex;gap:8px;flex-wrap:wrap;width:100%">
                <el-tag
                  v-for="(tag, idx) in textForm.tags"
                  :key="idx"
                  closable
                  size="default"
                  @close="textForm.tags.splice(idx, 1)"
                >{{ tag }}</el-tag>
                <el-input
                  v-if="textForm.tagInputVisible"
                  v-model="textForm.tagInputValue"
                  size="default"
                  style="width:100px"
                  @keyup.enter="addTag"
                  @blur="addTag"
                />
                <el-button v-else size="small" @click="textForm.tagInputVisible = true">+ 添加</el-button>
              </div>
            </el-form-item>
            <el-form-item label="文案风格">
              <el-select v-model="textForm.style" placeholder="选择风格" style="width:240px">
                <el-option label="营销推广" value="marketing" />
                <el-option label="小红书种草" value="xiaohongshu" />
                <el-option label="产品详情" value="detail" />
                <el-option label="短视频口播" value="short_video" />
                <el-option label="直播话术" value="live_stream" />
              </el-select>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="textGenerating" @click="generateText">生成文案</el-button>
            </el-form-item>
            <el-form-item label="生成结果" v-if="textResult || textError">
              <el-input v-model="textResult" type="textarea" :rows="8" readonly style="width:100%" />
              <div v-if="textTaskId" class="task-meta">任务 ID: {{ textTaskId }}</div>
              <div v-if="textError" class="error-msg">{{ textError }}</div>
            </el-form-item>
          </el-form>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="图片生成" name="image">
        <el-card shadow="never" class="section-card">
          <div class="card-title">AI 图片生成器</div>
          <el-form label-width="120px" class="gen-form">
            <el-form-item label="提示词 (每行一个)">
              <el-input v-model="imageForm.prompts" type="textarea" :rows="4" placeholder="一行一个提示词" />
            </el-form-item>
            <el-form-item label="图片尺寸">
              <el-select v-model="imageForm.size" placeholder="选择尺寸" style="width:200px">
                <el-option label="512 x 512" :value="512" />
                <el-option label="768 x 768" :value="768" />
                <el-option label="1024 x 1024" :value="1024" />
                <el-option label="2048 x 2048" :value="2048" />
              </el-select>
            </el-form-item>
            <el-form-item label="生成数量">
              <el-input-number v-model="imageForm.n" :min="1" :max="8" style="width:200px" />
            </el-form-item>
            <el-form-item label="负面提示词">
              <el-input v-model="imageForm.negative_prompt" placeholder="不希望出现的内容" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="imageGenerating" @click="generateImage">生成图片</el-button>
            </el-form-item>
          </el-form>
          <div v-if="imageResults.length" class="image-grid">
            <div v-for="(url, idx) in imageResults" :key="idx" class="image-item">
              <img v-if="url" :src="url" class="real-image" />
              <div v-else class="image-placeholder">
                <el-icon :size="40" color="#c0c4cc"><Picture /></el-icon>
                <span>图片 {{ idx + 1 }}</span>
              </div>
            </div>
          </div>
          <div v-if="imageTaskId" class="task-meta">任务 ID: {{ imageTaskId }}</div>
          <div v-if="imageError" class="error-msg">{{ imageError }}</div>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="视频生成" name="video">
        <el-card shadow="never" class="section-card">
          <div class="card-title">AI 视频生成器</div>
          <el-form label-width="130px" class="gen-form">
            <el-form-item label="生成类型">
              <el-radio-group v-model="videoForm.type">
                <el-radio value="text2video">文生视频</el-radio>
                <el-radio value="image2video">图生视频</el-radio>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="提示词">
              <el-input v-model="videoForm.prompts" type="textarea" :rows="3" placeholder="描述视频内容" />
            </el-form-item>
            <el-form-item v-if="videoForm.type === 'image2video'" label="参考图片URL">
              <el-input v-model="videoForm.reference_image_url" placeholder="输入参考图片地址" />
            </el-form-item>
            <el-form-item label="视频时长">
              <el-slider v-model="videoForm.duration" :min="3" :max="30" show-input style="max-width:400px" />
            </el-form-item>
            <el-form-item label="分辨率">
              <el-select v-model="videoForm.resolution" style="width:200px">
                <el-option label="720p" value="720p" />
                <el-option label="1080p" value="1080p" />
                <el-option label="2K" value="2k" />
                <el-option label="4K" value="4k" />
              </el-select>
            </el-form-item>
            <el-form-item label="模型选择">
              <el-select v-model="videoForm.model" style="width:240px" placeholder="选择模型">
                <el-option v-for="m in videoModels" :key="m.id" :label="m.name || m.id" :value="m.id" />
              </el-select>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="videoGenerating" @click="generateVideo">生成视频</el-button>
            </el-form-item>
          </el-form>
          <div v-if="videoTaskId" class="task-meta">任务 ID: {{ videoTaskId }}</div>
          <div v-if="videoResultUrl" class="video-result">
            <video :src="videoResultUrl" controls style="max-width:100%"></video>
          </div>
          <div v-if="videoError" class="error-msg">{{ videoError }}</div>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="模型列表" name="models">
        <el-card shadow="never" class="section-card">
          <div class="card-title" style="margin-bottom:16px">可用 AI 模型</div>
          <el-table v-loading="modelsLoading" :data="modelList" stripe style="width:100%">
            <el-table-column prop="id" label="模型ID" min-width="180" />
            <el-table-column prop="type" label="类型" width="100">
              <template #default="{ row }">
                <el-tag size="small">{{ row.type }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="name" label="名称" min-width="160" />
            <el-table-column prop="status" label="状态" width="110">
              <template #default="{ row }">
                <el-tag size="small" :type="modelStatusType(row)">{{ modelStatusLabel(row) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="能力" min-width="200">
              <template #default="{ row }">
                <el-tag v-for="cap in rowCapabilities(row)" :key="cap" size="small" style="margin-right:6px;margin-bottom:4px">{{ cap }}</el-tag>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无可用模型" />
            </template>
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { Picture } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import {
  getModels,
  generateCopywriting,
  generateImages,
  generateVideoClips,
} from '../api'

const activeTab = ref('text')

const textGenerating = ref(false)
const imageGenerating = ref(false)
const videoGenerating = ref(false)
const modelsLoading = ref(false)

const textResult = ref('')
const textError = ref('')
const textTaskId = ref('')

const imageResults = ref<string[]>([])
const imageError = ref('')
const imageTaskId = ref('')

const videoTaskId = ref('')
const videoResultUrl = ref('')
const videoError = ref('')

const textForm = reactive({
  product_id: '',
  product_title: '',
  tags: [] as string[],
  tagInputVisible: false,
  tagInputValue: '',
  style: 'marketing',
})

const imageForm = reactive({
  prompts: '',
  size: 1024,
  n: 2,
  negative_prompt: '',
})

const videoForm = reactive({
  type: 'text2video',
  prompts: '',
  reference_image_url: '',
  duration: 10,
  resolution: '1080p',
  model: '',
})

const modelList = ref<any[]>([])

const videoModels = computed(() => modelList.value.filter((m: any) => (m.type || '').toLowerCase().includes('video')))

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

async function addTag() {
  const val = textForm.tagInputValue.trim()
  if (val && !textForm.tags.includes(val)) {
    textForm.tags.push(val)
  }
  textForm.tagInputVisible = false
  textForm.tagInputValue = ''
}

async function loadModels() {
  modelsLoading.value = true
  try {
    const res: any = await getModels()
    const list = res?.models ?? res?.items ?? res?.data ?? res ?? []
    modelList.value = Array.isArray(list) ? list : []
    if (videoModels.value.length > 0 && !videoForm.model) {
      videoForm.model = videoModels.value[0].id
    }
  } catch (e: any) {
    modelList.value = []
  } finally {
    modelsLoading.value = false
  }
}

async function generateText() {
  if (!textForm.product_title && !textForm.product_id) {
    ElMessage.warning('请至少输入商品ID或商品标题')
    return
  }
  textGenerating.value = true
  textResult.value = ''
  textError.value = ''
  textTaskId.value = ''
  try {
    const res: any = await generateCopywriting({
      product_id: textForm.product_id ? Number(textForm.product_id) : undefined,
      product_title: textForm.product_title || undefined,
      keywords: textForm.tags,
      style: textForm.style,
    })
    const data = res?.result ?? res?.content ?? res?.text ?? res ?? ''
    textResult.value = typeof data === 'string' ? data : JSON.stringify(data, null, 2)
    textTaskId.value = res?.task_id || ''
    ElMessage.success('文案生成成功')
  } catch (e: any) {
    textError.value = e?.response?.data?.detail || e?.message || '生成失败'
    ElMessage.error('文案生成失败')
  } finally {
    textGenerating.value = false
  }
}

async function generateImage() {
  if (!imageForm.prompts.trim()) {
    ElMessage.warning('请输入至少一行提示词')
    return
  }
  imageGenerating.value = true
  imageResults.value = []
  imageError.value = ''
  imageTaskId.value = ''
  try {
    const prompts = imageForm.prompts.trim().split('\n').filter(Boolean)
    const res: any = await generateImages({
      prompts,
      size: imageForm.size,
      n: imageForm.n,
      negative_prompt: imageForm.negative_prompt || undefined,
    })
    imageTaskId.value = res?.task_id || ''
    const urls = res?.images ?? res?.urls ?? res?.result ?? res?.data ?? []
    if (Array.isArray(urls)) {
      imageResults.value = urls.map((u: any) => typeof u === 'string' ? u : (u?.url || u?.image_url || ''))
    } else if (typeof urls === 'string') {
      imageResults.value = [urls]
    }
    ElMessage.success(`已提交生成 ${prompts.length * imageForm.n} 张图片`)
  } catch (e: any) {
    imageError.value = e?.response?.data?.detail || e?.message || '生成失败'
    ElMessage.error('图片生成失败')
  } finally {
    imageGenerating.value = false
  }
}

async function generateVideo() {
  if (!videoForm.prompts.trim()) {
    ElMessage.warning('请输入提示词')
    return
  }
  videoGenerating.value = true
  videoResultUrl.value = ''
  videoError.value = ''
  videoTaskId.value = ''
  try {
    const res: any = await generateVideoClips({
      type: videoForm.type,
      prompts: videoForm.prompts,
      reference_image_url: videoForm.reference_image_url || undefined,
      duration: videoForm.duration,
      resolution: videoForm.resolution,
      model: videoForm.model || undefined,
    })
    videoTaskId.value = res?.task_id || ''
    videoResultUrl.value = res?.video_url || res?.url || ''
    ElMessage.success('视频生成任务已提交，预计需要几分钟完成')
  } catch (e: any) {
    videoError.value = e?.response?.data?.detail || e?.message || '生成失败'
    ElMessage.error('视频生成失败')
  } finally {
    videoGenerating.value = false
  }
}

onMounted(() => {
  loadModels()
})
</script>

<style scoped>
.ai-generation { padding: 0; }
.gen-tabs { margin-top: -4px; }
.section-card { border: none; box-shadow: none; }
.card-title { font-size: 15px; font-weight: 600; color: #303133; margin-bottom: 20px; }
.gen-form { max-width: 680px; }
.image-grid { display: flex; flex-wrap: wrap; gap: 16px; margin-top: 20px; }
.image-item {
  width: 160px; height: 160px;
  border: 1px solid #ebeef5; border-radius: 8px;
  overflow: hidden;
  background: #fff;
}
.real-image { width: 100%; height: 100%; object-fit: cover; }
.image-placeholder {
  width: 100%; height: 100%;
  border: 2px dashed #dcdfe6; border-radius: 8px;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  color: #c0c4cc; font-size: 13px;
  background: #fafafa;
}
.task-meta { margin-top: 8px; font-size: 12px; color: #909399; }
.error-msg { margin-top: 8px; color: #F56C6C; font-size: 13px; }
.video-result { margin-top: 16px; max-width: 680px; }
</style>
