import React, { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { Alert, Card, Confirm, Field, Spinner } from './components'

/* =========================================================================
   系统设置：模型接入 + 审查参数
   ========================================================================= */

export default function SettingsPage({ config, reload }) {
  const [draft, setDraft] = useState(null)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState(null)
  const [err, setErr] = useState('')
  const [testResult, setTestResult] = useState({})
  const [editIdx, setEditIdx] = useState(0)

  useEffect(() => {
    if (config?.settings) {
      setDraft(JSON.parse(JSON.stringify(config.settings)))
    }
  }, [config])

  const providers = draft?.providers || []
  const cur = providers[editIdx] || null

  const setCur = (patch) => {
    setDraft((d) => {
      const n = JSON.parse(JSON.stringify(d))
      Object.assign(n.providers[editIdx], patch)
      return n
    })
  }

  const setTop = (patch) => setDraft((d) => ({ ...d, ...patch }))

  const addProvider = () => {
    setDraft((d) => {
      const n = JSON.parse(JSON.stringify(d))
      const id = `custom_${Date.now().toString(36)}`
      n.providers.push({
        id,
        name: '新建模型',
        base_url: 'https://api.deepseek.com/v1',
        api_key: '',
        model: 'deepseek-chat',
        temperature: 0.1,
        max_tokens: 8192,
        timeout: 300,
        supports_json_mode: true,
        extra_headers: {},
        extra_body: {},
      })
      setEditIdx(n.providers.length - 1)
      return n
    })
  }

  const delProvider = (idx) => {
    setDraft((d) => {
      const n = JSON.parse(JSON.stringify(d))
      const removed = n.providers[idx]
      n.providers.splice(idx, 1)
      if (n.active_provider_id === removed.id) n.active_provider_id = ''
      setEditIdx(Math.max(0, idx - 1))
      return n
    })
  }

  const save = async () => {
    setBusy('save')
    setErr('')
    setMsg(null)
    try {
      await api.updateConfig(draft)
      await reload()
      setMsg('配置已保存')
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy('')
    }
  }

  const test = async () => {
    if (!cur) return
    setBusy('test')
    setErr('')
    setTestResult((t) => ({ ...t, [cur.id]: null }))
    try {
      // 先保存，保证测试的是真实生效的配置
      await api.updateConfig(draft)
      await reload()
      const r = await api.testProvider(cur.id, {
        base_url: cur.base_url,
        api_key: cur.api_key,
        model: cur.model,
      })
      setTestResult((t) => ({ ...t, [cur.id]: r }))
    } catch (e) {
      setTestResult((t) => ({ ...t, [cur.id]: { ok: false, error: e.message } }))
    } finally {
      setBusy('')
    }
  }

  const loadModels = async () => {
    if (!cur) return
    setBusy('models')
    try {
      await api.updateConfig(draft)
      await reload()
      const r = await api.listModels(cur.id)
      if (r.models?.length) {
        setTestResult((t) => ({
          ...t,
          [cur.id]: { ...(t[cur.id] || {}), available_models: r.models },
        }))
        setMsg(`获取到 ${r.models.length} 个可用模型，见下方列表`)
      } else {
        setMsg('该接口未提供模型列表，请手动填写模型名称')
      }
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy('')
    }
  }

  const reset = async () => {
    setBusy('reset')
    try {
      await api.resetConfig()
      await reload()
      setMsg('已恢复默认供应商列表（API Key 已保留）')
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy('')
    }
  }

  if (!draft) return <div className="empty">加载中…</div>

  const tr = cur ? testResult[cur.id] : null

  return (
    <div>
      <div className="page-head">
        <h2>系统设置</h2>
        <p>配置大模型接口与审查参数。支持任何 OpenAI 兼容接口，包括本地部署模型</p>
      </div>

      {err && <Alert kind="error" onClose={() => setErr('')}>{err}</Alert>}
      {msg && <Alert kind="success" onClose={() => setMsg(null)}>{msg}</Alert>}

      {/* ---------------- 模型接入 ---------------- */}
      <Card
        title="模型接入"
        sub="任意 OpenAI 兼容接口均可"
        extra={
          <div className="btn-row">
            <button className="btn btn-sm" onClick={addProvider}>
              + 新增
            </button>
            <Confirm text="恢复默认" onConfirm={reset}>
              恢复默认
            </Confirm>
          </div>
        }
      >
        <Alert kind="info">
          只需三步：① 选择或新增一个供应商 → ② 填写接口地址、API Key、模型名称 →
          ③ 点击「测试连接」确认可用 → 设为当前审查模型 → 保存。
          <br />
          API Key 仅保存在本机 <span className="mono">data/config.json</span>，不会上传任何服务器。
        </Alert>

        <div className="grid-2">
          <Field label="选择供应商档案">
            <select value={editIdx} onChange={(e) => setEditIdx(Number(e.target.value))}>
              {providers.map((p, i) => (
                <option key={p.id} value={i}>
                  {p.name || p.id}
                  {draft.active_provider_id === p.id ? '  ★当前' : ''}
                </option>
              ))}
            </select>
          </Field>

          <Field label="显示名称">
            <input
              type="text"
              value={cur?.name || ''}
              onChange={(e) => setCur({ name: e.target.value })}
              placeholder="如：DeepSeek 生产环境"
            />
          </Field>
        </div>

        <Field
          label="接口地址（base_url）"
          hint="需包含版本前缀，如 https://api.deepseek.com/v1。本地模型填 http://localhost:11434/v1"
        >
          <input
            type="text"
            value={cur?.base_url || ''}
            onChange={(e) => setCur({ base_url: e.target.value })}
            placeholder="https://api.deepseek.com/v1"
          />
        </Field>

        <div className="grid-2">
          <Field label="API Key" hint="本地模型可填任意值，如 ollama">
            <input
              type="password"
              value={cur?.api_key || ''}
              onChange={(e) => setCur({ api_key: e.target.value })}
              placeholder="sk-..."
              autoComplete="off"
            />
          </Field>

          <Field label="模型名称" hint="点下方「获取模型列表」可自动拉取">
            <input
              type="text"
              value={cur?.model || ''}
              onChange={(e) => setCur({ model: e.target.value })}
              placeholder="deepseek-chat"
            />
          </Field>
        </div>

        <div className="btn-row">
          <button className="btn btn-primary" disabled={busy === 'test'} onClick={test}>
            {busy === 'test' ? <Spinner /> : null} 测试连接
          </button>
          <button className="btn" disabled={busy === 'models'} onClick={loadModels}>
            {busy === 'models' ? <Spinner dark /> : null} 获取模型列表
          </button>
          <button
            className="btn"
            onClick={() => setTop({ active_provider_id: cur.id })}
            disabled={draft.active_provider_id === cur.id}
          >
            {draft.active_provider_id === cur.id ? '★ 当前审查模型' : '设为审查模型'}
          </button>
          {providers.length > 1 && (
            <Confirm text="删除" onConfirm={() => delProvider(editIdx)}>
              删除此档案
            </Confirm>
          )}
        </div>

        {tr && (
          <div className="mt12">
            {tr.ok ? (
              <Alert kind="success">
                连接成功！耗时 {tr.latency_ms} ms · 模型回复「{tr.reply}」
                <div className="mono dim" style={{ fontSize: 11, marginTop: 3 }}>
                  {tr.endpoint}
                </div>
              </Alert>
            ) : (
              <Alert kind="error">
                连接失败：{tr.error}
                <div className="dim" style={{ fontSize: 12, marginTop: 5 }}>
                  排查建议：① 检查 base_url 是否包含 /v1；② 确认 API Key 有效且有余额；
                  ③ 本地模型确认服务已启动；④ 若使用中转网关，确认其兼容 /chat/completions。
                </div>
              </Alert>
            )}

            {tr.available_models?.length > 0 && (
              <div>
                <div className="dim" style={{ fontSize: 12, marginBottom: 6 }}>
                  可用模型（点击填入）：
                </div>
                <div style={{ maxHeight: 160, overflowY: 'auto' }}>
                  {tr.available_models.map((m) => (
                    <span
                      key={m}
                      className="cite"
                      style={{ cursor: 'pointer' }}
                      onClick={() => setCur({ model: m })}
                    >
                      {m}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        <details className="mt12">
          <summary style={{ cursor: 'pointer', fontSize: 12.5 }} className="dim">
            高级参数
          </summary>
          <div className="grid-3 mt12">
            <Field label="温度" hint="审查任务建议 0～0.2，越低越稳定">
              <input
                type="number"
                step="0.05"
                min="0"
                max="2"
                value={cur?.temperature ?? 0.1}
                onChange={(e) => setCur({ temperature: Number(e.target.value) })}
              />
            </Field>
            <Field label="最大输出 tokens">
              <input
                type="number"
                value={cur?.max_tokens ?? 8192}
                onChange={(e) => setCur({ max_tokens: Number(e.target.value) })}
              />
            </Field>
            <Field label="超时（秒）">
              <input
                type="number"
                value={cur?.timeout ?? 300}
                onChange={(e) => setCur({ timeout: Number(e.target.value) })}
              />
            </Field>
          </div>
          <Field
            label="支持 JSON 模式"
            hint="若模型报 response_format 相关错误，取消勾选（系统会自动降级为提示词约束）"
          >
            <label style={{ display: 'flex', gap: 7, alignItems: 'center', fontSize: 13 }}>
              <input
                type="checkbox"
                style={{ width: 15, height: 15 }}
                checked={cur?.supports_json_mode ?? true}
                onChange={(e) => setCur({ supports_json_mode: e.target.checked })}
              />
              该模型支持 response_format=json_object
            </label>
          </Field>
        </details>
      </Card>

      {/* ---------------- 审查参数 ---------------- */}
      <Card title="审查参数">
        <div className="grid-3">
          <Field label="并发审查数" hint="越大越快，但可能触发接口限流">
            <input
              type="number"
              min="1"
              max="16"
              value={draft.concurrency}
              onChange={(e) => setTop({ concurrency: Number(e.target.value) })}
            />
          </Field>
          <Field label="送入模型的最大字符数" hint="文档过长会截断，优先保留关键章节">
            <input
              type="number"
              min="2000"
              max="50000"
              step="1000"
              value={draft.max_chunk_chars}
              onChange={(e) => setTop({ max_chunk_chars: Number(e.target.value) })}
            />
          </Field>
          <Field label="每条规则检索法规数" hint="影响 RAG 依据的丰富度">
            <input
              type="number"
              min="1"
              max="20"
              value={draft.retrieval_top_k}
              onChange={(e) => setTop({ retrieval_top_k: Number(e.target.value) })}
            />
          </Field>
        </div>

        <Field label="知识库自动更新">
          <label style={{ display: 'flex', gap: 7, alignItems: 'center', fontSize: 13 }}>
            <input
              type="checkbox"
              style={{ width: 15, height: 15 }}
              checked={draft.kb_auto_update}
              onChange={(e) => setTop({ kb_auto_update: e.target.checked })}
            />
            开启后台自动检查法规更新
          </label>
        </Field>

        {draft.kb_auto_update && (
          <Field label="检查间隔（小时）">
            <input
              type="number"
              min="1"
              max="720"
              value={draft.kb_update_interval_hours}
              onChange={(e) => setTop({ kb_update_interval_hours: Number(e.target.value) })}
            />
          </Field>
        )}
      </Card>

      <div className="btn-row">
        <button className="btn btn-primary btn-lg" disabled={busy === 'save'} onClick={save}>
          {busy === 'save' ? <Spinner /> : null} 保存全部设置
        </button>
      </div>
    </div>
  )
}
