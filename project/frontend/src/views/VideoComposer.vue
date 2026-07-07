<template>
  <div class="video-composer">
    <el-tabs v-model="activeTab">
      <el-tab-pane label="合成任务" name="compose">
        <el-card class="section-card">
          <el-form :model="composeForm" label-width="100px" inline>
            <el-form-item label="管线 ID">
              <el-input v-model="composeForm.pipeline_id" placeholder="pipeline id" style="width:200px" />
            </el-form-item>
            <el-form-item label="视频片段">
              <el-input v-model="composeForm.video_clips" type="textarea" :rows="3" placeholder="每行一个视频 URL" style="width:320px" />
            </el-form-item>
            <el-form-item label="图片素材">
              <el-input v-model="composeForm.images" type="textarea" :rows="3" placeholder="每行一个图片 URL" style="width:320px" />
            </el-form-item>
            <el-form-item label="音频 URL">
              <el-input v-model="composeForm.audio_url" placeholder="https://..." style="width:280px" />
            </el-form-item>
            <el-form-item label="字幕文本">
              <el-input v-model="composeForm.subtitle_text" placeholder="输入字幕内容" style="width:280px" />
            </el-form-item>
            <el-form-item label="合成模板">
              <el-input v-model="composeForm.template_id" placeholder="模板 ID（可选）" style="width:200px" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="submitting" @click="handleSubmitCompose">提交合成</el-button>
            </el-form-item>
          </el-form>
        </el-card>

        <el-card class="section-card" style="margin-top:20px">
          <el-table v-loading="loading" :data="composeTasks" stripe>
            <el-table-column prop="task_id" label="任务 ID" width="180" show-overflow-tooltip />
            <el-table-column prop="pipeline_id" label="管线 ID" width="140" show-overflow-tooltip />
            <el-table-column prop="status" label="状态" width="100">
              <template #default="{ row }">
                <el-tag :type="statusTagType(row.status)" size="small">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="progress" label="进度" width="160">
              <template #default="{ row }">
                <el-progress :percentage="Number(row.progress || 0)" :status="row.status === 'completed' ? 'success' : (row.status === 'failed' ? 'exception' : '')" />
              </template>
            </el-table-column>
            <el-table-column prop="output_url" label="输出视频" min-width="200">
              <template #default="{ row }">
                <a v-if="row.output_url" :href="row.output_url" target="_blank" style="color:#409EFF">查看</a>
                <span v-else class="muted">-</span>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="创建时间" width="170" />
            <el-table-column label="操作" width="100">
              <template #default="{ row }">
                <el-button v-if="row.status === 'completed' && row.output_url" type="primary" link size="small" @click="previewTask(row)">预览</el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无合成任务" />
            </template>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="模板管理" name="templates">
        <el-card class="section-card">
          <div style="margin-bottom:16px;color:#909399;font-size:13px">
            模板管理功能尚未提供后端 API，如需使用模板请在合成任务中直接输入模板 ID。
          </div>
          <el-empty description="暂无模板数据" />
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="previewVisible" title="视频预览" width="720px" destroy-on-close>
      <div class="preview-container">
        <video v-if="previewUrl" :src="previewUrl" controls style="width:100%"></video>
        <el-empty v-else description="无视频可预览" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getComposeTasks, composeVideo } from '../api'

const activeTab = ref('compose')
const submitting = ref(false)
const loading = ref(false)

const composeForm = reactive({
  pipeline_id: '',
  video_clips: '',
  images: '',
  audio_url: '',
  subtitle_text: '',
  template_id: '',
})

const composeTasks = ref<any[]>([])

const previewVisible = ref(false)
const previewUrl = ref('')

function statusTagType(s: string) {
  if (s === 'running' || s === 'processing' || s === 'pending') return 'warning'
  if (s === 'completed' || s === 'success') return 'success'
  if (s === 'failed' || s === 'error') return 'danger'
  return 'info'
}

async function loadTasks() {
  loading.value = true
  try {
    const res: any = await getComposeTasks()
    const list = res?.tasks ?? res?.items ?? res?.data ?? res ?? []
    composeTasks.value = Array.isArray(list) ? list : []
  } catch (e: any) {
    composeTasks.value = []
  } finally {
    loading.value = false
  }
}

async function handleSubmitCompose() {
  if (!composeForm.pipeline_id) {
    ElMessage.warning('请填写管线 ID')
    return
  }
  const video_clips = composeForm.video_clips.split('\n').map(s => s.trim()).filter(Boolean)
  const images = composeForm.images.split('\n').map(s => s.trim()).filter(Boolean)
  if (video_clips.length === 0 && images.length === 0) {
    ElMessage.warning('请至少提供视频片段或图片素材')
    return
  }
  submitting.value = true
  try {
    await composeVideo({
      pipeline_id: Number(composeForm.pipeline_id) || composeForm.pipeline_id,
      video_clips,
      images,
      audio_url: composeForm.audio_url || undefined,
      subtitle_text: composeForm.subtitle_text || undefined,
      template_id: composeForm.template_id || undefined,
    })
    ElMessage.success('合成任务已提交')
    composeForm.pipeline_id = ''
    composeForm.video_clips = ''
    composeForm.images = ''
    composeForm.audio_url = ''
    composeForm.subtitle_text = ''
    composeForm.template_id = ''
    setTimeout(loadTasks, 1000)
  } catch (e: any) {
    ElMessage.error('提交合成失败: ' + (e?.response?.data?.detail || e?.message || ''))
  } finally {
    submitting.value = false
  }
}

function previewTask(row: { output_url: string }) {
  previewUrl.value = row.output_url
  previewVisible.value = true
}

onMounted(loadTasks)
</script>

<style scoped>
.video-composer { padding: 4px 0; }
.section-card { margin-bottom: 0; }
.preview-container {
  background: #f5f7fa;
  border-radius: 8px;
  padding: 12px;
  min-height: 320px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.muted { color: #c0c4cc; }
</style>
