import axios from 'axios'

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
})

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  config.headers['X-Tenant-ID'] = localStorage.getItem('tenantId') || 'default'
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.clear()
      window.location.hash = '#/login'
    }
    return Promise.reject(error)
  }
)

function unwrap(res: any) {
  return res?.data?.data ?? res?.data ?? res
}

// --- Auth ---
export function login(username: string, password: string) {
  return api.post('/auth/login', { username, password }).then(unwrap)
}

// --- Dashboard ---
export function getDashboard() {
  return api.get('/dashboard').then(unwrap)
}

// --- Tasks (Redis task monitoring) ---
export function getTasks() {
  return api.get('/tasks').then(unwrap)
}

// --- Products ---
export function getProducts(page = 1, pageSize = 20) {
  return api.get('/products', { params: { page, page_size: pageSize } }).then(unwrap)
}

export function getHotProducts(limit = 50) {
  return api.get('/products/hot', { params: { limit } }).then(unwrap)
}

// --- Analyze ---
export function analyzeProducts(productIds: number[] | null = null, platform = '', limit = 100) {
  return api.post('/analyze', { product_ids: productIds, platform: platform || undefined, limit }).then(unwrap)
}

// --- Score Config ---
export function getScoreConfig() {
  return api.get('/config/score').then(unwrap)
}

export function updateScoreConfig(config: any) {
  return api.put('/config/score', config).then(unwrap)
}

// --- Crawl Jobs ---
export function getCrawlJobs(page = 1, pageSize = 20) {
  return api.get('/crawl/jobs', { params: { page, page_size: pageSize } }).then(unwrap)
}

export function createCrawlJob(platform: string, keyword: string, maxCount = 100, sortBy = 'sales') {
  return api.post('/crawl/jobs', { platform, keyword, max_count: maxCount, sort_by: sortBy }).then(unwrap)
}

// --- Crawl Plans ---
export function getCrawlPlans(page = 1, pageSize = 20) {
  return api.get('/crawl/plans', { params: { page, page_size: pageSize } }).then(unwrap)
}

export function createCrawlPlan(data: any) {
  return api.post('/crawl/plans', data).then(unwrap)
}

export function updateCrawlPlan(planId: string, data: any) {
  return api.put(`/crawl/plans/${planId}`, data).then(unwrap)
}

export function deleteCrawlPlan(planId: string) {
  return api.delete(`/crawl/plans/${planId}`).then(unwrap)
}

// --- AI Generation ---
export function getModels() {
  return api.get('/models').then(unwrap)
}

export function generateCopywriting(data: any) {
  return api.post('/copywriting', data).then(unwrap)
}

export function generateImages(data: any) {
  return api.post('/images/generate', data).then(unwrap)
}

export function generateVideoClips(data: any) {
  return api.post('/videos/generate', data).then(unwrap)
}

export function getAiTaskResult(taskId: string) {
  return api.get(`/ai/tasks/${taskId}/result`).then(unwrap)
}

// --- Video Composer ---
export function getComposeTasks() {
  return api.get('/compose').then(unwrap)
}

export function composeVideo(data: any) {
  return api.post('/compose', data).then(unwrap)
}

export function getComposeStatus(taskId: string) {
  return api.get(`/compose/${taskId}`).then(unwrap)
}

// --- Publish ---
export function publishContent(data: any) {
  return api.post('/publish', data).then(unwrap)
}

export function getPublishLogs(page = 1, pageSize = 20) {
  return api.get('/publish/logs', { params: { page, page_size: pageSize } }).then(unwrap)
}

export function getAuthorizedPlatforms() {
  return api.get('/platforms/authorized-list').then(unwrap)
}

export function getAuthUrl(platform: string) {
  return api.get('/platforms/auth-url', { params: { platform } }).then(unwrap)
}

// --- Pipelines ---
export function getPipelines(page = 1, pageSize = 20) {
  return api.get('/pipelines', { params: { page, page_size: pageSize } }).then(unwrap)
}

export function createPipeline(productId: number, config: any = null) {
  return api.post('/pipelines', { product_id: productId, config }).then(unwrap)
}

// --- API Keys ---
export function getApiKeys() {
  return api.get('/api-keys').then(unwrap)
}

export function createApiKey(name: string, scopes: string[] | null = null, maxConcurrency = 10) {
  return api.post('/api-keys', { name, scopes, max_concurrency: maxConcurrency }).then(unwrap)
}

export function toggleApiKey(keyId: number) {
  return api.patch(`/api-keys/${keyId}/toggle`).then(unwrap)
}

export function deleteApiKey(keyId: number) {
  return api.delete(`/api-keys/${keyId}`).then(unwrap)
}

// --- Platform Configs ---
export function getPlatformConfigs(platform = '') {
  return api.get('/platform-configs', { params: platform ? { platform } : {} }).then(unwrap)
}

export function updatePlatformConfig(platform: string, configKey: string, configValue: string, description = '') {
  return api.put(`/platform-configs/${platform}`, null, { params: { config_key: configKey, config_value: configValue, description } }).then(unwrap)
}

// --- Authorized Platforms (BFF direct) ---
export function getAuthorizedPlatformList() {
  return api.get('/platforms/authorized').then(unwrap)
}

// --- Agent Orchestrator ---
export function getAgentTools() {
  return api.get('/agent/tools').then(unwrap)
}

export function addAgentTool(data: { id?: string; name: string; cli_command: string; description?: string }) {
  return api.post('/agent/tools', data).then(unwrap)
}

export function getAgentModels() {
  return api.get('/agent/models').then(unwrap)
}

export function saveModelKey(modelId: string, apiKey: string, baseUrl = '') {
  return api.post(`/agent/models/${modelId}/key`, { api_key: apiKey, base_url: baseUrl }).then(unwrap)
}

export function checkModelKey(modelId: string) {
  return api.get(`/agent/models/${modelId}/key`).then(unwrap)
}

export function executeAgentTask(data: { agent_tool_id: string; model_id: string; task_instruction: string }) {
  return api.post('/agent/execute', data).then(unwrap)
}

export function getAgentTasks() {
  return api.get('/agent/tasks').then(unwrap)
}

export function getAgentTask(taskId: string) {
  return api.get(`/agent/tasks/${taskId}`).then(unwrap)
}

export function getAgentTaskOutput(taskId: string) {
  return api.get(`/agent/tasks/${taskId}/output`).then(unwrap)
}

export function cancelAgentTask(taskId: string) {
  return api.post(`/agent/tasks/${taskId}/cancel`).then(unwrap)
}

// --- Agent Scheduled Tasks ---
export function getScheduledTasks() {
  return api.get('/agent/scheduled').then(unwrap)
}

export function createScheduledTask(data: { agent_tool_id: string; model_id: string; task_instruction: string; interval_seconds: number }) {
  return api.post('/agent/scheduled', data).then(unwrap)
}

export function toggleScheduledTask(schedId: string, enabled: boolean) {
  return api.put(`/agent/scheduled/${schedId}`, { enabled }).then(unwrap)
}

export function deleteScheduledTask(schedId: string) {
  return api.delete(`/agent/scheduled/${schedId}`).then(unwrap)
}


