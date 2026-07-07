import { createRouter, createWebHashHistory } from 'vue-router'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/login',
      name: 'Login',
      component: () => import('../views/Login.vue'),
    },
    {
      path: '/',
      component: () => import('../layouts/MainLayout.vue'),
      redirect: '/agent',
      children: [
        { path: 'agent', name: 'AgentConsole', component: () => import('../views/AgentConsole.vue'), meta: { title: 'Agent 指挥台' } },
        { path: 'dashboard', name: 'Dashboard', component: () => import('../views/Dashboard.vue'), meta: { title: '仪表盘' } },
        { path: 'crawl', name: 'CrawlManagement', component: () => import('../views/CrawlManagement.vue'), meta: { title: '采集管理' } },
        { path: 'analysis', name: 'ProductAnalysis', component: () => import('../views/ProductAnalysis.vue'), meta: { title: '选品分析' } },
        { path: 'generation', name: 'AIGeneration', component: () => import('../views/AIGeneration.vue'), meta: { title: 'AI 生成' } },
        { path: 'composer', name: 'VideoComposer', component: () => import('../views/VideoComposer.vue'), meta: { title: '视频合成' } },
        { path: 'publish', name: 'PublishManagement', component: () => import('../views/PublishManagement.vue'), meta: { title: '发布管理' } },
        { path: 'settings', name: 'SystemSettings', component: () => import('../views/SystemSettings.vue'), meta: { title: '系统设置' } },
      ],
    },
  ],
})

export default router
