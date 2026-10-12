/**
 * 后端 API 客户端。
 * 桌面端与开发态统一走相对路径 /api（开发态由 Vite 代理）。
 */

const BASE = '/api'

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  const text = await res.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = { detail: text }
  }
  if (!res.ok) {
    const msg = data?.detail || data?.message || `请求失败（HTTP ${res.status}）`
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
  }
  return data
}

export const api = {
  health: () => request('/health'),

  // ---- 配置与模型 ----
  getConfig: () => request('/config'),
  updateConfig: (payload) =>
    request('/config', { method: 'PUT', body: JSON.stringify(payload) }),
  testProvider: (id, payload) =>
    request(`/config/provider/${id}/test`, {
      method: 'POST',
      body: JSON.stringify(payload || {}),
    }),
  listModels: (id) => request(`/config/provider/${id}/models`),
  resetConfig: () => request('/config/reset', { method: 'POST' }),

  // ---- 知识库 ----
  kbStatus: () => request('/kb/status'),
  kbDocuments: () => request('/kb/documents'),
  kbSearch: (query, top_k = 8) =>
    request('/kb/search', { method: 'POST', body: JSON.stringify({ query, top_k }) }),
  kbRebuild: () => request('/kb/rebuild', { method: 'POST' }),
  kbUpload: async (file, meta = {}) => {
    const fd = new FormData()
    fd.append('file', file)
    const qs = new URLSearchParams(meta).toString()
    const res = await fetch(`${BASE}/kb/documents/upload?${qs}`, {
      method: 'POST',
      body: fd,
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data?.detail || '上传失败')
    return data
  },
  kbDelete: (docId) => request(`/kb/documents/${docId}`, { method: 'DELETE' }),
  kbFetch: (payload) =>
    request('/kb/fetch', { method: 'POST', body: JSON.stringify(payload) }),
  kbSources: () => request('/kb/sources'),
  kbCheckUpdate: () => request('/kb/update/check', { method: 'POST' }),
  kbRunUpdate: (force = false) =>
    request(`/kb/update/run?force=${force}`, { method: 'POST' }),
  kbUpdateLog: () => request('/kb/update/log'),
  kbScheduler: () => request('/kb/update/scheduler'),

  // ---- 规则 ----
  listRules: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v)
    ).toString()
    return request(`/kb/rules${qs ? `?${qs}` : ''}`)
  },
  reloadRules: () => request('/kb/rules/reload', { method: 'POST' }),

  // ---- 上传 ----
  upload: async (file) => {
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch(`${BASE}/upload`, { method: 'POST', body: fd })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data?.detail || '上传失败')
    return data
  },
  uploadFormats: () => request('/upload/formats'),
  listUploads: () => request('/upload'),
  getUpload: (fileId) => request(`/upload/${fileId}`),
  downloadUpload: (fileId) => `${BASE}/upload/${fileId}/download`,

  // ---- 审查 ----
  listReports: () => request('/review/reports'),
  getReport: (id) => request(`/review/reports/${id}`),
  deleteReport: (id) => request(`/review/reports/${id}`, { method: 'DELETE' }),
  saveSelection: (reviewId, selectedIds) =>
    request('/review/select', {
      method: 'POST',
      body: JSON.stringify({ review_id: reviewId, selected_ids: selectedIds }),
    }),
  exportReport: (id, fmt = 'docx') =>
    request(`/review/reports/${id}/export?fmt=${fmt}`, { method: 'POST' }),
  recheck: (reviewId, findingId, providerId) =>
    request('/review/recheck', {
      method: 'POST',
      body: JSON.stringify({
        review_id: reviewId,
        finding_id: findingId,
        provider_id: providerId,
      }),
    }),

  // ---- 修订 ----
  previewRewrite: (reviewId, selectedIds) =>
    request('/rewrite/preview', {
      method: 'POST',
      body: JSON.stringify({ review_id: reviewId, selected_ids: selectedIds }),
    }),
  applyRewrite: (reviewId, selectedIds, opts = {}) =>
    request('/rewrite/apply', {
      method: 'POST',
      body: JSON.stringify({
        review_id: reviewId,
        selected_ids: selectedIds,
        append_change_log: opts.append_change_log !== false,
        add_highlights: opts.add_highlights !== false,
        output_name: opts.output_name || null,
      }),
    }),
  listExports: () => request('/rewrite/exports'),
  deleteExport: (filename) =>
    request(`/rewrite/exports/${encodeURIComponent(filename)}`, { method: 'DELETE' }),
  downloadExport: (filename) =>
    `${BASE}/rewrite/download?filename=${encodeURIComponent(filename)}`,
}

/**
 * 流式审查：通过 SSE 接收进度。
 * 返回一个可取消的句柄。
 */
export function streamReview({ fileId, providerId, onlyRules }, handlers = {}) {
  const controller = new AbortController()

  ;(async () => {
    try {
      const res = await fetch(`${BASE}/review/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          file_id: fileId,
          provider_id: providerId || null,
          only_rules: onlyRules || null,
        }),
        signal: controller.signal,
      })

      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data?.detail || `审查请求失败（HTTP ${res.status}）`)
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        const parts = buffer.split('\n\n')
        buffer = parts.pop() || ''

        for (const part of parts) {
          const line = part.split('\n').find((l) => l.startsWith('data:'))
          if (!line) continue
          let payload
          try {
            payload = JSON.parse(line.slice(5).trim())
          } catch {
            continue
          }
          if (payload.type === 'progress') handlers.onProgress?.(payload)
          else if (payload.type === 'start') handlers.onStart?.(payload)
          else if (payload.type === 'done') handlers.onDone?.(payload.report)
          else if (payload.type === 'error') handlers.onError?.(new Error(payload.message))
        }
      }
    } catch (e) {
      if (e.name !== 'AbortError') handlers.onError?.(e)
    }
  })()

  return { cancel: () => controller.abort() }
}
