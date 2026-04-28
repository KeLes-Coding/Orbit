const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1'
const TOKEN_KEY = 'orbit.accessToken'

export const getStoredToken = () => localStorage.getItem(TOKEN_KEY)

export const setStoredToken = (token) => {
  localStorage.setItem(TOKEN_KEY, token)
}

export const clearStoredToken = () => {
  localStorage.removeItem(TOKEN_KEY)
}

const parseError = async (response) => {
  const fallback = `Request failed with status ${response.status}`

  try {
    const body = await response.json()
    if (typeof body.detail === 'string') {
      return body.detail
    }
    if (Array.isArray(body.detail)) {
      return body.detail.map((item) => item.msg).filter(Boolean).join(', ') || fallback
    }
    return body.message || fallback
  } catch {
    return fallback
  }
}

export const apiRequest = async (path, options = {}) => {
  const token = getStoredToken()
  const headers = new Headers(options.headers || {})

  if (!headers.has('Content-Type') && options.body !== undefined) {
    headers.set('Content-Type', 'application/json')
  }

  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
  })

  if (response.status === 204) {
    return null
  }

  if (!response.ok) {
    if (response.status === 401) {
      clearStoredToken()
    }
    throw new Error(await parseError(response))
  }

  return response.json()
}

export const authApi = {
  login(payload) {
    return apiRequest('/auth/login', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },
  register(payload) {
    return apiRequest('/auth/register', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },
  me() {
    return apiRequest('/auth/me')
  },
}

export const conversationApi = {
  list() {
    return apiRequest('/conversations')
  },
  get(conversationId) {
    return apiRequest(`/conversations/${conversationId}`)
  },
  create(payload = {}) {
    return apiRequest('/conversations', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },
  update(conversationId, payload) {
    return apiRequest(`/conversations/${conversationId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
  },
  archive(conversationId) {
    return apiRequest(`/conversations/${conversationId}`, {
      method: 'DELETE',
    })
  },
  messages(conversationId) {
    return apiRequest(`/conversations/${conversationId}/messages`)
  },
  sendMessage(conversationId, content) {
    return apiRequest(`/conversations/${conversationId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ content }),
    })
  },
}

export const healthApi = {
  check() {
    return apiRequest('/health')
  },
}

export const llmConfigApi = {
  list() {
    return apiRequest('/llm-configs')
  },
  get(configId) {
    return apiRequest(`/llm-configs/${configId}`)
  },
  create(payload) {
    return apiRequest('/llm-configs', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },
  update(configId, payload) {
    return apiRequest(`/llm-configs/${configId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
  },
  setDefault(configId) {
    return apiRequest(`/llm-configs/${configId}/default`, {
      method: 'POST',
    })
  },
  archive(configId) {
    return apiRequest(`/llm-configs/${configId}`, {
      method: 'DELETE',
    })
  },
}
