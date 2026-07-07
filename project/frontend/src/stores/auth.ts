import { defineStore } from 'pinia'
import { ref } from 'vue'
import { login as loginApi } from '../api'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const username = ref(localStorage.getItem('username') || '')
  const tenantId = ref(localStorage.getItem('tenantId') || 'default')

  async function login(user: string, pass: string): Promise<boolean> {
    try {
      const res = await loginApi(user, pass)
      const data = res?.data ?? res
      if (data?.token) {
        token.value = data.token
        username.value = data.username || user
        tenantId.value = data.tenantId || 'default'
        localStorage.setItem('token', token.value)
        localStorage.setItem('username', username.value)
        localStorage.setItem('tenantId', tenantId.value)
        return true
      }
      return false
    } catch {
      return false
    }
  }

  function logout() {
    token.value = ''
    username.value = ''
    localStorage.clear()
  }

  return { token, username, tenantId, login, logout }
})
