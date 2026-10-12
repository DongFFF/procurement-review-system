import React, { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { Alert, Card, Confirm, Empty, Field, Spinner, fmtSize, fmtTime } from './components'

/* =========================================================================
   知识库管理：法规文件入库、检索测试、自动更新
   ========================================================================= */

export default function KnowledgePage() {
  const [status, setStatus] = useState(null)
  const [docs, setDocs] = useState([])
  const [sources, setSources] = useState([])
  const [logs, setLogs] = useState([])
  const [scheduler, setScheduler] = useState(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [tab, setTab] = useState('docs')

  // 检索测试
  const [q, setQ] = useState('')
  const [hits, setHits] = useState(null)

  // 上传表单
  const [upFile, setUpFile] = useState(null)
  const [meta, setMeta] = useState({ title: '', doc_number: '', category: 'regulation', source_url: '' })
  const [fetchUrl, setFetchUrl] = useState('')

  const load = useCallback(async () => {
    try {
      const [st, dc, sc, lg, sch] = await Promise.all([
        api.kbStatus(),
        api.kbDocuments(),
        api.kbSources(),
        api.kbUpdateLog(),
        api.kbScheduler(),
      ])
      setStatus(st)
      setDocs(dc.documents || [])
      setSources(sc.sources || [])
      setLogs(lg.logs || [])
      setScheduler(sch)
    } catch (e) {
      setErr(e.message)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const rebuild = async () => {
    setBusy('rebuild')
    setErr('')
    setMsg('')
    try {
      const r = await api.kbRebuild()
      setMsg(`索引重建完成：${r.document_count} 个文档 / ${r.chunk_count} 个片段`)
      await load()
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy('')
    }
  }

  const doUpload = async () => {
    if (!upFile) return
    setBusy('upload')
    setErr('')
    setMsg('')
    try {
      const r = await api.kbUpload(upFile, meta)
      setMsg(`「${r.title}」已入库（${r.char_count} 字），索引已更新`)
      setUpFile(null)
      setMeta({ title: '', doc_number: '', category: 'regulation', source_url: '' })
      await load()
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy('')
    }
  }

  const doFetch = async () => {
    if (!fetchUrl.trim()) return
    setBusy('fetch')
    setErr('')
    setMsg('')
    try {
      const data = await api.kbFetch({ url: fetchUrl.trim() })
      setMsg(`已抓取入库：${data.title}（${data.char_count} 字）`)
      setFetchUrl('')
      await load()
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy('')
    }
  }

  const checkUpdate = async () => {
    setBusy('check')
    setErr('')
    setMsg('')
    try {
      const r = await api.kbCheckUpdate()
      setMsg(
        `来源检查完成：${r.source_count} 个来源，${r.reachable} 个可访问。${r.note || ''}`
      )
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy('')
    }
  }

  const runUpdate = async () => {
    setBusy('update')
    setErr('')
    setMsg('')
    try {
      const r = await api.kbRunUpdate(false)
      setMsg(r.message || '更新完成')
      await load()
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy('')
    }
  }

  const doSearch = async () => {
    if (!q.trim()) return
    setBusy('search')
    setErr('')
    try {
      const r = await api.kbSearch(q, 8)
      setHits(r.hits || [])
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy('')
    }
  }

  const delDoc = async (id) => {
    setErr('')
    try {
      await api.kbDelete(id)
      setMsg('已删除并重建索引')
      await load()
    } catch (e) {
      setErr(e.message)
    }
  }

  const kb = status?.kb
  const rl = status?.rules

  return (
    <div>
      <div className="page-head">
        <h2>知识库管理</h2>
        <p>存放政府采购法规文件，为审查提供法规依据。支持手动上传、网址抓取与自动更新</p>
      </div>

      {err && <Alert kind="error" onClose={() => setErr('')}>{err}</Alert>}
      {msg && <Alert kind="success" onClose={() => setMsg('')}>{msg}</Alert>}

      {/* ---------------- 概览 ---------------- */}
      <Card title="知识库概览">
        <div className="stat-row" style={{ marginBottom: 14 }}>
          <div className="s">
            <span className="n" style={{ color: 'var(--primary)' }}>{kb?.document_count ?? '—'}</span>
            <span>个法规文档</span>
          </div>
          <div className="s">
            <span className="n" style={{ color: 'var(--primary)' }}>{kb?.chunk_count ?? '—'}</span>
            <span>个检索片段</span>
          </div>
          <div className="s">
            <span className="n" style={{ color: 'var(--success)' }}>{rl?.rule_count ?? '—'}</span>
            <span>条审查规则</span>
          </div>
          <div className="s">
            <span className="n">{kb?.embed_mode === 'api' ? 'API' : '本地'}</span>
            <span>向量模式</span>
          </div>
        </div>

        {kb?.chunk_count === 0 && (
          <Alert kind="warn">
            知识库为空。审查时模型将只能依据自身知识判断，准确性和可溯源性会下降。
            建议先上传法规文件，或点击「立即更新」从内置权威来源自动抓取。
          </Alert>
        )}

        <div className="btn-row">
          <button className="btn btn-primary" disabled={busy === 'update'} onClick={runUpdate}>
            {busy === 'update' ? <Spinner /> : null} 立即更新法规
          </button>
          <button className="btn" disabled={busy === 'check'} onClick={checkUpdate}>
            {busy === 'check' ? <Spinner dark /> : null} 检查来源
          </button>
          <button className="btn" disabled={busy === 'rebuild'} onClick={rebuild}>
            {busy === 'rebuild' ? <Spinner dark /> : null} 重建索引
          </button>
        </div>

        {scheduler && (
          <div className="dim" style={{ fontSize: 12, marginTop: 10 }}>
            自动更新：
            {scheduler.running ? (
              <>
                已启用
                {scheduler.jobs?.[0]?.next_run && `，下次检查 ${scheduler.jobs[0].next_run}`}
              </>
            ) : (
              '未启用（可在「系统设置」中开启）'
            )}
          </div>
        )}
      </Card>

      <div className="tabs">
        {[
          ['docs', `法规文件 (${docs.length})`],
          ['add', '添加法规'],
          ['search', '检索测试'],
          ['sources', `更新来源 (${sources.length})`],
          ['logs', '更新日志'],
        ].map(([k, label]) => (
          <div key={k} className={`tab ${tab === k ? 'active' : ''}`} onClick={() => setTab(k)}>
            {label}
          </div>
        ))}
      </div>

      {/* ---------------- 法规列表 ---------------- */}
      {tab === 'docs' && (
        <Card>
          {docs.length === 0 ? (
            <Empty
              icon="📚"
              title="知识库中还没有法规文件"
              desc="切换到「添加法规」上传文件，或到「更新来源」一键抓取"
            />
          ) : (
            <table>
              <thead>
                <tr>
                  <th>法规名称</th>
                  <th style={{ width: 150 }}>文号</th>
                  <th style={{ width: 90 }}>类别</th>
                  <th style={{ width: 90 }}>字数</th>
                  <th style={{ width: 90 }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {docs.map((d) => (
                  <tr key={d.doc_id}>
                    <td>
                      <div style={{ fontWeight: 500 }}>{d.title}</div>
                      {d.source_url && (
                        <a
                          href={d.source_url}
                          target="_blank"
                          rel="noreferrer"
                          className="dim"
                          style={{ fontSize: 11 }}
                        >
                          来源链接 ↗
                        </a>
                      )}
                    </td>
                    <td style={{ fontSize: 12.5 }} className="dim">
                      {d.doc_number || '—'}
                    </td>
                    <td>
                      <span className="badge badge-gray">{d.category}</span>
                    </td>
                    <td className="dim" style={{ fontSize: 12.5 }}>
                      {d.char_count?.toLocaleString()}
                    </td>
                    <td>
                      <Confirm text="删除" onConfirm={() => delDoc(d.doc_id)}>
                        删除
                      </Confirm>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}

      {/* ---------------- 添加法规 ---------------- */}
      {tab === 'add' && (
        <>
          <Card title="上传法规文件" sub="支持 docx / pdf / txt / md">
            <input
              type="file"
              accept=".docx,.doc,.pdf,.txt,.md"
              onChange={(e) => {
                const f = e.target.files?.[0]
                setUpFile(f || null)
                if (f && !meta.title) {
                  setMeta((m) => ({ ...m, title: f.name.replace(/\.[^.]+$/, '') }))
                }
              }}
            />
            {upFile && (
              <div className="dim" style={{ fontSize: 12, marginTop: 6 }}>
                已选择：{upFile.name}（{fmtSize(upFile.size)}）
              </div>
            )}

            <div className="grid-2 mt12">
              <Field label="法规名称">
                <input
                  type="text"
                  value={meta.title}
                  onChange={(e) => setMeta({ ...meta, title: e.target.value })}
                  placeholder="如：政府采购需求管理办法"
                />
              </Field>
              <Field label="文号">
                <input
                  type="text"
                  value={meta.doc_number}
                  onChange={(e) => setMeta({ ...meta, doc_number: e.target.value })}
                  placeholder="如：财库〔2021〕22号"
                />
              </Field>
            </div>

            <div className="grid-2">
              <Field label="类别">
                <select
                  value={meta.category}
                  onChange={(e) => setMeta({ ...meta, category: e.target.value })}
                >
                  <option value="law">法律</option>
                  <option value="regulation">行政法规</option>
                  <option value="rule">部门规章</option>
                  <option value="notice">规范性文件</option>
                  <option value="standard">标准</option>
                </select>
              </Field>
              <Field label="来源网址（可选）">
                <input
                  type="text"
                  value={meta.source_url}
                  onChange={(e) => setMeta({ ...meta, source_url: e.target.value })}
                  placeholder="https://www.gov.cn/..."
                />
              </Field>
            </div>

            <button className="btn btn-primary" disabled={!upFile || busy === 'upload'} onClick={doUpload}>
              {busy === 'upload' ? <Spinner /> : null} 入库并重建索引
            </button>
          </Card>

          <Card title="从网址抓取" sub="适用于政府网站公开的法规原文">
            <Field
              label="法规页面网址"
              hint="系统会自动提取正文。若页面为动态加载或抓取失败，请改为下载文件后上传"
            >
              <input
                type="text"
                value={fetchUrl}
                onChange={(e) => setFetchUrl(e.target.value)}
                placeholder="https://www.gov.cn/zhengce/..."
              />
            </Field>
            <button className="btn btn-primary" disabled={!fetchUrl.trim() || busy === 'fetch'} onClick={doFetch}>
              {busy === 'fetch' ? <Spinner /> : null} 抓取并入库
            </button>
          </Card>
        </>
      )}

      {/* ---------------- 检索测试 ---------------- */}
      {tab === 'search' && (
        <Card title="检索测试" sub="验证知识库能否准确命中相关法规">
          <div style={{ display: 'flex', gap: 9 }}>
            <input
              type="text"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && doSearch()}
              placeholder="如：以不合理条件限制供应商 / 财库〔2021〕22号 / 评分办法"
            />
            <button className="btn btn-primary" disabled={busy === 'search'} onClick={doSearch}>
              {busy === 'search' ? <Spinner /> : null} 检索
            </button>
          </div>

          {hits && (
            <div className="mt12">
              {hits.length === 0 ? (
                <Empty icon="🔍" title="没有命中任何片段" desc="尝试换用法规中的原词" />
              ) : (
                hits.map((h, i) => (
                  <div key={i} className="finding">
                    <div className="finding-head" style={{ cursor: 'default' }}>
                      <div className="finding-title">
                        <div className="t">
                          {i + 1}. {h.citation}
                        </div>
                        <div className="m">
                          <span>综合得分 {h.score}</span>
                          <span className="dim">向量 {h.vec_score}</span>
                          <span className="dim">关键词 {h.bm25_score}</span>
                        </div>
                      </div>
                    </div>
                    <div className="finding-body">
                      <div className="quote">{h.text}</div>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}
        </Card>
      )}

      {/* ---------------- 更新来源 ---------------- */}
      {tab === 'sources' && (
        <Card
          title="法规更新来源"
          sub="系统定期检查这些来源，内容有变化时自动更新知识库"
          extra={
            <button className="btn btn-sm btn-primary" disabled={busy === 'update'} onClick={runUpdate}>
              立即更新
            </button>
          }
        >
          <Alert kind="info">
            自动更新采用「抓取正文 → 比对内容哈希 → 有变化才写入」的策略，
            不会重复入库，也不会覆盖你手动上传的文件。更新后索引会自动重建。
          </Alert>
          <table>
            <thead>
              <tr>
                <th>法规名称</th>
                <th style={{ width: 160 }}>文号</th>
                <th style={{ width: 90 }}>类别</th>
                <th style={{ width: 70 }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s, i) => (
                <tr key={i}>
                  <td>
                    <div>{s.title || '（未命名来源）'}</div>
                    <a
                      href={s.url}
                      target="_blank"
                      rel="noreferrer"
                      className="dim mono"
                      style={{ fontSize: 11 }}
                    >
                      {s.url}
                    </a>
                  </td>
                  <td style={{ fontSize: 12.5 }} className="dim">
                    {s.doc_number || '—'}
                  </td>
                  <td>
                    <span className="badge badge-gray">{s.category}</span>
                  </td>
                  <td>
                    <button
                      className="btn btn-sm"
                      onClick={async () => {
                        setBusy('fetch')
                        setErr('')
                        setMsg('')
                        try {
                          const data = await api.kbFetch({
                            url: s.url,
                            title: s.title,
                            doc_number: s.doc_number,
                            category: s.category,
                          })
                          setMsg(`已入库：${data.title}（${data.char_count} 字）`)
                          await load()
                        } catch (e) {
                          setErr(e.message)
                        } finally {
                          setBusy('')
                        }
                      }}
                    >
                      抓取
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {/* ---------------- 更新日志 ---------------- */}
      {tab === 'logs' && (
        <Card title="更新日志">
          {logs.length === 0 ? (
            <Empty icon="📋" title="暂无更新记录" desc="执行「立即更新」后会在此留痕" />
          ) : (
            <table>
              <thead>
                <tr>
                  <th style={{ width: 160 }}>时间</th>
                  <th style={{ width: 120 }}>操作</th>
                  <th>结果</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((l, i) => (
                  <tr key={i}>
                    <td className="mono" style={{ fontSize: 12 }}>
                      {l.at}
                    </td>
                    <td style={{ fontSize: 12.5 }}>{l.action}</td>
                    <td style={{ fontSize: 12.5 }} className="dim">
                      {l.action === 'auto_update'
                        ? `更新 ${l.updated} 个，跳过 ${l.skipped} 个，失败 ${l.failed} 个`
                        : l.error || JSON.stringify(l)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}
    </div>
  )
}
