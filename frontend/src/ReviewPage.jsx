import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, streamReview } from './api'
import { Alert, Badge, Card, Confirm, Empty, KV, Spinner, fmtSize } from './components'

/* =========================================================================
   审查工作台：上传 → 审查 → 勾选意见 → 自动修订
   ========================================================================= */

export default function ReviewPage({ config, onGoSettings }) {
  const [file, setFile] = useState(null)          // 上传解析结果
  const [uploads, setUploads] = useState([])
  const [drag, setDrag] = useState(false)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [warn, setWarn] = useState('')

  const [progress, setProgress] = useState(null)   // {done,total,percent,message}
  const [logs, setLogs] = useState([])
  const [report, setReport] = useState(null)
  const [selected, setSelected] = useState(() => new Set())
  const [expanded, setExpanded] = useState(() => new Set())
  const [filter, setFilter] = useState('all')

  const [preview, setPreview] = useState(null)     // 修订预览
  const [result, setResult] = useState(null)       // 修订结果
  const streamRef = useRef(null)
  const fileInput = useRef(null)

  const activeProvider = useMemo(() => {
    const s = config?.settings
    if (!s) return null
    return s.providers?.find((p) => p.id === s.active_provider_id) || null
  }, [config])

  const modelReady = Boolean(activeProvider?.base_url && activeProvider?.model)

  /* -------------------- 上传 -------------------- */
  const refreshUploads = useCallback(async () => {
    try {
      const r = await api.listUploads()
      setUploads(r.files || [])
    } catch {
      /* 忽略 */
    }
  }, [])

  useEffect(() => {
    refreshUploads()
  }, [refreshUploads])

  const doUpload = useCallback(
    async (f) => {
      if (!f) return
      setErr('')
      setWarn('')
      setBusy('upload')
      setReport(null)
      setResult(null)
      setPreview(null)
      setSelected(new Set())
      setExpanded(new Set())
      try {
        const data = await api.upload(f)
        setFile(data)
        if (data.warning) setWarn(data.warning)
        refreshUploads()
      } catch (e) {
        setErr(e.message)
        setFile(null)
      } finally {
        setBusy('')
      }
    },
    [refreshUploads]
  )

  const loadUpload = useCallback(async (fileId) => {
    setErr('')
    setWarn('')
    setBusy('upload')
    try {
      const data = await api.getUpload(fileId)
      setFile(data)
      if (data.warning) setWarn(data.warning)
      setReport(null)
      setResult(null)
      setPreview(null)
      setSelected(new Set())
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy('')
    }
  }, [])

  /* -------------------- 审查 -------------------- */
  const startReview = useCallback(() => {
    if (!file) return
    if (!modelReady) {
      setErr('尚未配置模型。请先到「系统设置」填写模型接口地址、API Key 和模型名称。')
      return
    }
    setErr('')
    setReport(null)
    setResult(null)
    setPreview(null)
    setLogs([])
    setProgress({ done: 0, total: 0, percent: 0, message: '正在准备…' })
    setBusy('review')

    streamRef.current = streamReview(
      { fileId: file.file_id, providerId: config?.settings?.active_provider_id },
      {
        onStart: (p) => setProgress((s) => ({ ...s, message: p.message })),
        onProgress: (p) => {
          setProgress({
            done: p.done,
            total: p.total,
            percent: p.percent,
            message: p.message,
          })
          setLogs((l) => [...l.slice(-300), `[${p.done}/${p.total}] ${p.message}`])
        },
        onDone: (rep) => {
          setReport(rep)
          setBusy('')
          setProgress(null)
          // 默认勾选：严重和高风险
          const auto = new Set(
            rep.findings
              .filter((f) => f.violated && ['blocker', 'high'].includes(f.severity))
              .map((f) => f.id)
          )
          setSelected(auto)
          setExpanded(new Set(rep.findings.filter((f) => f.violated).slice(0, 3).map((f) => f.id)))
        },
        onError: (e) => {
          setErr(e.message)
          setBusy('')
          setProgress(null)
        },
      }
    )
  }, [file, modelReady, config])

  useEffect(() => () => streamRef.current?.cancel?.(), [])

  /* -------------------- 勾选 -------------------- */
  const toggle = (id) => {
    setSelected((s) => {
      const n = new Set(s)
      n.has(id) ? n.delete(id) : n.add(id)
      return n
    })
  }

  const toggleExpand = (id) => {
    setExpanded((s) => {
      const n = new Set(s)
      n.has(id) ? n.delete(id) : n.add(id)
      return n
    })
  }

  const issues = useMemo(
    () => (report?.findings || []).filter((f) => f.violated),
    [report]
  )
  const passed = useMemo(
    () => (report?.findings || []).filter((f) => !f.violated),
    [report]
  )

  const shown = useMemo(() => {
    if (filter === 'all') return issues
    if (filter === 'selected') return issues.filter((f) => selected.has(f.id))
    if (filter === 'review') return issues.filter((f) => f.needs_human_review)
    return issues.filter((f) => f.severity === filter)
  }, [issues, filter, selected])

  const selectAll = (sev) => {
    const ids = issues.filter((f) => !sev || f.severity === sev).map((f) => f.id)
    setSelected((s) => new Set([...s, ...ids]))
  }
  const clearAll = () => setSelected(new Set())

  /* -------------------- 修订 -------------------- */
  const doPreview = async () => {
    if (!report) return
    setErr('')
    setBusy('preview')
    try {
      const r = await api.previewRewrite(report.review_id, [...selected])
      setPreview(r)
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy('')
    }
  }

  const doApply = async () => {
    if (!report) return
    setErr('')
    setBusy('apply')
    try {
      const r = await api.applyRewrite(report.review_id, [...selected])
      setResult(r)
      setPreview(null)
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy('')
    }
  }

  const doExport = async (fmt) => {
    if (!report) return
    setErr('')
    setBusy('export')
    try {
      const r = await api.exportReport(report.review_id, fmt)
      window.open(api.downloadExport(r.filename), '_blank')
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy('')
    }
  }

  const s = report?.summary

  return (
    <div>
      <div className="page-head">
        <h2>审查工作台</h2>
        <p>上传采购需求文件，系统自动依据法规知识库逐条审查并给出可执行的整改意见</p>
      </div>

      {err && <Alert kind="error" onClose={() => setErr('')}>{err}</Alert>}
      {warn && <Alert kind="warn" onClose={() => setWarn('')}>{warn}</Alert>}

      {!modelReady && (
        <Alert kind="warn">
          尚未配置审查模型。请先到 <b>系统设置</b> 填写模型接口地址与 API Key。
          <button
            className="btn btn-sm btn-primary"
            style={{ marginLeft: 10 }}
            onClick={onGoSettings}
          >
            去配置
          </button>
        </Alert>
      )}

      {/* ---------------- 上传区 ---------------- */}
      {!file && (
        <Card title="① 上传采购需求文件">
          <div
            className={`dropzone ${drag ? 'drag' : ''}`}
            onClick={() => fileInput.current?.click()}
            onDragOver={(e) => {
              e.preventDefault()
              setDrag(true)
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDrag(false)
              const f = e.dataTransfer.files?.[0]
              if (f) doUpload(f)
            }}
          >
            {busy === 'upload' ? (
              <>
                <div className="big">
                  <Spinner dark />
                </div>
                <div className="t1">正在解析文件…</div>
              </>
            ) : (
              <>
                <div className="big">📄</div>
                <div className="t1">点击选择文件，或拖拽到此处</div>
                <div className="t2">
                  支持 Word（.docx）、PDF、纯文本（.txt/.md），最大 50MB
                </div>
              </>
            )}
          </div>
          <input
            ref={fileInput}
            type="file"
            hidden
            accept=".docx,.doc,.pdf,.txt,.md"
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) doUpload(f)
              e.target.value = ''
            }}
          />

          {uploads.length > 0 && (
            <div className="mt12">
              <div className="dim" style={{ fontSize: 12, marginBottom: 6 }}>
                最近上传：
              </div>
              <div className="btn-row">
                {uploads.slice(0, 6).map((u) => (
                  <button
                    key={u.file_id}
                    className="btn btn-sm"
                    onClick={() => loadUpload(u.file_id)}
                  >
                    {u.original_name} · {fmtSize(u.size)}
                  </button>
                ))}
              </div>
            </div>
          )}
        </Card>
      )}

      {/* ---------------- 文件信息 + 开始审查 ---------------- */}
      {file && (
        <Card
          title="① 已上传文件"
          extra={
            <button
              className="btn btn-sm"
              onClick={() => {
                setFile(null)
                setReport(null)
                setResult(null)
                setPreview(null)
                setErr('')
                setWarn('')
              }}
            >
              重新选择
            </button>
          }
        >
          <div className="flex-between">
            <div>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>{file.original_name}</div>
              <div className="dim" style={{ fontSize: 12.5 }}>
                {file.char_count?.toLocaleString()} 字 · {file.block_count} 个内容块 ·{' '}
                {file.sections?.length || 0} 个章节
                {file.size ? ` · ${fmtSize(file.size)}` : ''}
              </div>
            </div>
            <button
              className="btn btn-primary btn-lg"
              disabled={busy === 'review' || !modelReady}
              onClick={startReview}
            >
              {busy === 'review' ? (
                <>
                  <Spinner /> 审查中…
                </>
              ) : (
                '开始审查'
              )}
            </button>
          </div>

          {file.sections?.length > 0 && (
            <div style={{ marginTop: 13 }}>
              <div className="dim" style={{ fontSize: 12, marginBottom: 6 }}>
                识别到的章节：
              </div>
              <div>
                {file.sections.map((sec) => (
                  <span key={sec} className="cite">
                    {sec}
                  </span>
                ))}
              </div>
            </div>
          )}

          {file.warning && (
            <div style={{ marginTop: 12 }}>
              <Alert kind="warn">{file.warning}</Alert>
            </div>
          )}
        </Card>
      )}

      {/* ---------------- 进度 ---------------- */}
      {progress && (
        <Card title="② 审查进行中">
          <div className="flex-between">
            <span style={{ fontSize: 13 }}>{progress.message}</span>
            <span className="dim mono">
              {progress.done}/{progress.total || '—'}
            </span>
          </div>
          <div className="progress-bar">
            <div className="fill" style={{ width: `${progress.percent || 0}%` }} />
          </div>
          <div className="progress-log">
            {logs.length === 0 ? (
              <div className="dim">等待第一条结果…</div>
            ) : (
              logs.map((l, i) => <div key={i}>{l}</div>)
            )}
          </div>
          <div className="btn-row mt12">
            <button
              className="btn btn-sm btn-danger"
              onClick={() => {
                streamRef.current?.cancel?.()
                setBusy('')
                setProgress(null)
                setErr('审查已取消')
              }}
            >
              取消审查
            </button>
            <span className="dim" style={{ fontSize: 12 }}>
              并发 {config?.settings?.concurrency || 4} 条规则，规则越多耗时越长
            </span>
          </div>
        </Card>
      )}

      {/* ---------------- 审查结论 ---------------- */}
      {report && s && (
        <>
          <div className={`verdict verdict-${s.verdict_level}`}>
            <h3>总体判定：{s.verdict}</h3>
            <p>{s.verdict_text}</p>
            <div className="stat-row">
              {s.blocker_count > 0 && (
                <div className="s">
                  <span className="n" style={{ color: 'var(--danger)' }}>{s.blocker_count}</span>
                  <span>严重</span>
                </div>
              )}
              {s.high_count > 0 && (
                <div className="s">
                  <span className="n" style={{ color: '#c2410c' }}>{s.high_count}</span>
                  <span>高风险</span>
                </div>
              )}
              {s.medium_count > 0 && (
                <div className="s">
                  <span className="n" style={{ color: 'var(--warn)' }}>{s.medium_count}</span>
                  <span>中等</span>
                </div>
              )}
              {s.low_count > 0 && (
                <div className="s">
                  <span className="n" style={{ color: '#475569' }}>{s.low_count}</span>
                  <span>低</span>
                </div>
              )}
              <div className="s">
                <span className="n" style={{ color: 'var(--success)' }}>{s.pass_count}</span>
                <span>通过</span>
              </div>
            </div>
            <div className="dim" style={{ fontSize: 12, marginTop: 9 }}>
              共审查 {report.rules_evaluated} 条规则，耗时 {report.elapsed_sec} 秒 · 模型：
              {report.model_info?.provider} / {report.model_info?.model}
              {s.needs_human_review_count > 0 &&
                ` · ${s.needs_human_review_count} 项建议人工复核`}
            </div>
          </div>

          {/* 多智能体协同过程 */}
          {report.mode === 'multi' && (
            <Card title="协同过程" sub="多智能体协同审查的执行记录">
              {report.profile && (
                <div style={{ marginBottom: 12 }}>
                  <div className="dim" style={{ fontSize: 12, marginBottom: 5 }}>
                    项目画像
                  </div>
                  <div>
                    <span className="cite">类型 {report.profile.project_type || '未识别'}</span>
                    {report.profile.budget && (
                      <span className="cite">预算 {report.profile.budget}</span>
                    )}
                    <span className="cite">风险 {report.profile.risk_level}</span>
                    {(report.profile.focus_areas || []).map((a, i) => (
                      <span key={i} className="cite">
                        重点 {a}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              <div>
                <div className="dim" style={{ fontSize: 12, marginBottom: 5 }}>
                  各环节
                </div>
                {(report.traces || []).map((t, i) => (
                  <div
                    key={i}
                    style={{
                      display: 'flex',
                      gap: 10,
                      fontSize: 12.5,
                      padding: '3px 0',
                      alignItems: 'baseline',
                    }}
                  >
                    <span style={{ minWidth: 150, fontWeight: 500 }}>{t.agent}</span>
                    <span className="dim">调用 {t.calls} 次</span>
                    <span className="dim">{t.elapsed}s</span>
                    {t.note && <span className="dim">{t.note}</span>}
                  </div>
                ))}
              </div>

              {report.collaboration && (
                <div className="dim" style={{ fontSize: 12, marginTop: 9 }}>
                  前置拦截 {report.collaboration.ecv_blocked_count} 条 · 交叉验证{' '}
                  {report.collaboration.validator_calls} 条 · 仲裁{' '}
                  {report.collaboration.arbiter_calls} 条 · 驳回{' '}
                  {report.collaboration.rejected_count} 条 · 修正{' '}
                  {report.collaboration.revised_count} 条
                </div>
              )}

              {report.summary?.overall_comment && (
                <div className="mt12">
                  <div className="dim" style={{ fontSize: 12, marginBottom: 5 }}>
                    总体评述
                  </div>
                  <div style={{ fontSize: 13, lineHeight: 1.7 }}>
                    {report.summary.overall_comment}
                  </div>
                  {(report.summary.key_risks || []).length > 0 && (
                    <ul style={{ margin: '8px 0 0', paddingLeft: 20, fontSize: 12.5 }}>
                      {report.summary.key_risks.map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  )}
                  {report.summary.priority_action && (
                    <div className="suggest" style={{ marginTop: 8 }}>
                      优先整改：{report.summary.priority_action}
                    </div>
                  )}
                </div>
              )}
            </Card>
          )}

          {report.errors?.length > 0 && (
            <Alert kind="warn">
              有 {report.errors.length} 条规则执行异常（通常是模型接口波动），可点击「重审」重试。
              <div className="mono" style={{ fontSize: 11, marginTop: 5 }}>
                {report.errors.slice(0, 3).join('；')}
              </div>
            </Alert>
          )}

          {/* ---------------- 意见列表 ---------------- */}
          <Card
            title="② 审查意见"
            sub={`共 ${issues.length} 项问题，已勾选 ${selected.size} 项`}
            extra={
              <div className="btn-row">
                <button className="btn btn-sm" onClick={() => selectAll()}>
                  全选
                </button>
                <button className="btn btn-sm" onClick={() => selectAll('blocker')}>
                  选严重
                </button>
                <button className="btn btn-sm" onClick={clearAll}>
                  清空
                </button>
              </div>
            }
          >
            {issues.length === 0 ? (
              <Empty icon="✅" title="未发现合规性问题" desc="该采购需求文件符合现行法规要求" />
            ) : (
              <>
                <div className="tabs">
                  {[
                    ['all', `全部 (${issues.length})`],
                    ['selected', `已勾选 (${selected.size})`],
                    ['blocker', `严重 (${s.blocker_count})`],
                    ['high', `高 (${s.high_count})`],
                    ['medium', `中 (${s.medium_count})`],
                    ['low', `低 (${s.low_count})`],
                    [
                      'review',
                      `待复核 (${issues.filter((f) => f.needs_human_review).length})`,
                    ],
                  ].map(([k, label]) => (
                    <div
                      key={k}
                      className={`tab ${filter === k ? 'active' : ''}`}
                      onClick={() => setFilter(k)}
                    >
                      {label}
                    </div>
                  ))}
                </div>

                {shown.length === 0 ? (
                  <Empty icon="🔍" title="该筛选下没有内容" />
                ) : (
                  shown.map((f) => (
                    <FindingItem
                      key={f.id}
                      f={f}
                      selected={selected.has(f.id)}
                      expanded={expanded.has(f.id)}
                      onToggle={() => toggle(f.id)}
                      onExpand={() => toggleExpand(f.id)}
                    />
                  ))
                )}
              </>
            )}
          </Card>

          {/* ---------------- 修订 ---------------- */}
          {issues.length > 0 && (
            <Card title="③ 自动修订">
              <Alert kind="info">
                勾选要采纳的意见后，系统会在原文件基础上生成一份<b>修订版 Word 文档</b>
                （原文件不会被改动），并自动附加「修订说明表」，逐条列明改了什么、依据是什么。
                <br />
                黄色高亮 = 已替换内容；绿色高亮 = 新增条款。
              </Alert>

              <div className="btn-row">
                <button
                  className="btn btn-primary"
                  disabled={selected.size === 0 || busy === 'preview'}
                  onClick={doPreview}
                >
                  {busy === 'preview' ? <Spinner /> : null} 预览修订（{selected.size} 项）
                </button>
                <button
                  className="btn btn-primary"
                  disabled={selected.size === 0 || busy === 'apply'}
                  onClick={doApply}
                >
                  {busy === 'apply' ? <Spinner /> : null} 生成修订版文件
                </button>
                <button
                  className="btn"
                  disabled={busy === 'export'}
                  onClick={() => doExport('docx')}
                >
                  导出审查报告（Word）
                </button>
                <button className="btn" disabled={busy === 'export'} onClick={() => doExport('md')}>
                  导出报告（Markdown）
                </button>
              </div>

              {/* 修订预览 */}
              {preview && (
                <div className="mt12">
                  <div className="flex-between" style={{ marginBottom: 9 }}>
                    <b style={{ fontSize: 13.5 }}>修订预览</b>
                    <span className="dim" style={{ fontSize: 12 }}>
                      将替换 {preview.counts.replace} 处、新增 {preview.counts.insert} 处
                      {preview.counts.manual > 0 &&
                        `，${preview.counts.manual} 处需人工处理`}
                    </span>
                  </div>
                  <table>
                    <thead>
                      <tr>
                        <th style={{ width: 80 }}>处理</th>
                        <th style={{ width: 150 }}>问题</th>
                        <th>修改内容</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.items.map((it) => (
                        <tr key={it.finding_id}>
                          <td>
                            <Badge
                              level={
                                it.action === 'manual'
                                  ? 'medium'
                                  : it.action === 'insert'
                                  ? 'low'
                                  : 'high'
                              }
                            >
                              {it.action_label}
                            </Badge>
                          </td>
                          <td style={{ fontSize: 12.5 }}>
                            {it.rule_name}
                            {it.location && (
                              <div className="dim" style={{ fontSize: 11 }}>
                                {it.location}
                              </div>
                            )}
                          </td>
                          <td>
                            {it.before && (
                              <div className="quote" style={{ marginBottom: 6 }}>
                                原：{it.before}
                              </div>
                            )}
                            <div className="suggest">
                              {it.action === 'insert' ? '新增：' : '改为：'}
                              {it.after || it.suggestion || '（见审查意见）'}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* 修订结果 */}
              {result && (
                <div className="mt12">
                  <Alert kind="success">
                    修订完成！共处理 {result.total} 项：替换 {result.replaced} 处、
                    新增 {result.inserted} 处
                    {result.manual > 0 && `、${result.manual} 处已列入说明表待人工处理`}。
                  </Alert>
                  <div className="btn-row">
                    <a
                      className="btn btn-primary"
                      href={api.downloadExport(result.output_name)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      ⬇ 下载修订版：{result.output_name}
                    </a>
                  </div>
                  {result.manual > 0 && (
                    <div className="mt12">
                      <div className="dim" style={{ fontSize: 12, marginBottom: 6 }}>
                        以下 {result.manual} 项未能自动定位，已写入文档末尾的修订说明表：
                      </div>
                      <table>
                        <thead>
                          <tr>
                            <th>问题</th>
                            <th>原因</th>
                          </tr>
                        </thead>
                        <tbody>
                          {result.changes
                            .filter((c) => c.action === 'manual')
                            .map((c) => (
                              <tr key={c.finding_id}>
                                <td style={{ fontSize: 12.5 }}>{c.rule_name}</td>
                                <td style={{ fontSize: 12.5 }} className="dim">
                                  {c.note}
                                </td>
                              </tr>
                            ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </Card>
          )}

          {/* ---------------- 通过项 ---------------- */}
          {passed.length > 0 && (
            <Card title="④ 审查通过项" sub={`${passed.length} 条规则未发现问题`}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {passed.map((f) => (
                  <span key={f.id} className="cite" style={{ background: '#e8f5e9', color: '#1e7d32' }}>
                    ✓ {f.rule_name}
                  </span>
                ))}
              </div>
            </Card>
          )}
        </>
      )}

      {!file && !report && uploads.length === 0 && (
        <Card>
          <Empty
            icon="🏛️"
            title="欢迎使用中直高校采购需求审查系统"
            desc="上传采购需求文件即可开始合规审查。首次使用请先到「系统设置」配置模型。"
          />
        </Card>
      )}
    </div>
  )
}

/* =========================================================================
   单条审查意见
   ========================================================================= */

function FindingItem({ f, selected, expanded, onToggle, onExpand }) {
  return (
    <div className={`finding ${selected ? 'selected' : ''}`}>
      <div className="finding-head" onClick={onExpand}>
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          onClick={(e) => e.stopPropagation()}
          title="勾选后该意见将用于自动修订"
        />
        <div className="finding-title">
          <div className="t">{f.title || f.rule_name}</div>
          <div className="m">
            <Badge level={f.severity}>{f.severity_label}</Badge>
            <span>{f.category_label}</span>
            {f.location && <span>📍 {f.location}</span>}
            <span className="dim">置信度 {(f.confidence * 100).toFixed(0)}%</span>
            {f.needs_human_review && <span style={{ color: 'var(--warn)' }}>⚠ 建议复核</span>}
          </div>
        </div>
        <span className="dim" style={{ fontSize: 12, flexShrink: 0 }}>
          {expanded ? '收起 ▲' : '展开 ▼'}
        </span>
      </div>

      {expanded && (
        <div className="finding-body">
          {f.error && <Alert kind="error">{f.error}</Alert>}

          <KV k="问题说明">{f.detail}</KV>

          {f.original_text && (
            <KV k="原文摘录">
              <div className="quote">{f.original_text}</div>
            </KV>
          )}

          <KV k="法规依据">{f.legal_basis}</KV>

          {f.citations?.length > 0 && (
            <KV k="知识库检索依据">
              <div>
                {f.citations.slice(0, 4).map((c, i) => (
                  <span key={i} className="cite" title={c.excerpt}>
                    {c.citation}
                    {c.source_url && (
                      <a
                        href={c.source_url}
                        target="_blank"
                        rel="noreferrer"
                        style={{ marginLeft: 4 }}
                        onClick={(e) => e.stopPropagation()}
                      >
                        ↗
                      </a>
                    )}
                  </span>
                ))}
              </div>
            </KV>
          )}

          <KV k="整改建议">{f.suggestion}</KV>

          {f.replacement && (
            <KV k="建议修改为">
              <div className="suggest">{f.replacement}</div>
            </KV>
          )}
        </div>
      )}
    </div>
  )
}
