import React, { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { Alert, Badge, Card, Confirm, Empty, Spinner, fmtSize, fmtTime } from './components'

/* =========================================================================
   审查记录与导出文件管理
   ========================================================================= */

export default function HistoryPage() {
  const [reports, setReports] = useState([])
  const [exports, setExports] = useState([])
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [detail, setDetail] = useState(null)

  const load = useCallback(async () => {
    try {
      const [r, e] = await Promise.all([api.listReports(), api.listExports()])
      setReports(r.reports || [])
      setExports(e.files || [])
    } catch (e) {
      setErr(e.message)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const openDetail = async (id) => {
    setBusy('detail')
    try {
      setDetail(await api.getReport(id))
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy('')
    }
  }

  const del = async (id) => {
    try {
      await api.deleteReport(id)
      if (detail?.review_id === id) setDetail(null)
      await load()
    } catch (e) {
      setErr(e.message)
    }
  }

  const delExport = async (name) => {
    try {
      await api.deleteExport(name)
      setMsg('已删除')
      await load()
    } catch (e) {
      setErr(e.message)
    }
  }

  const exportReport = async (id, fmt) => {
    setBusy('export')
    try {
      const r = await api.exportReport(id, fmt)
      window.open(api.downloadExport(r.filename), '_blank')
      await load()
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy('')
    }
  }

  return (
    <div>
      <div className="page-head">
        <h2>审查记录</h2>
        <p>查看历史审查结果与已生成的文件。注意：审查结果保存在内存中，重启程序后会清空</p>
      </div>

      {err && <Alert kind="error" onClose={() => setErr('')}>{err}</Alert>}
      {msg && <Alert kind="success" onClose={() => setMsg('')}>{msg}</Alert>}

      {detail && (
        <Card
          title={`审查详情：${detail.filename}`}
          extra={
            <button className="btn btn-sm" onClick={() => setDetail(null)}>
              关闭
            </button>
          }
        >
          <div className="stat-row" style={{ marginBottom: 14 }}>
            <div className="s">
              <span className="n" style={{ color: 'var(--primary)' }}>
                {detail.summary?.issue_count}
              </span>
              <span>项问题</span>
            </div>
            <div className="s">
              <span className="n" style={{ color: 'var(--danger)' }}>
                {detail.summary?.blocker_count}
              </span>
              <span>严重</span>
            </div>
            <div className="s">
              <span className="n" style={{ color: 'var(--success)' }}>
                {detail.summary?.pass_count}
              </span>
              <span>通过</span>
            </div>
          </div>

          <div className={`verdict verdict-${detail.summary?.verdict_level}`} style={{ marginBottom: 14 }}>
            <h3>{detail.summary?.verdict}</h3>
            <p className="mb0">{detail.summary?.verdict_text}</p>
          </div>

          <div className="btn-row">
            <button className="btn btn-sm" onClick={() => exportReport(detail.review_id, 'docx')}>
              导出报告（Word）
            </button>
            <button className="btn btn-sm" onClick={() => exportReport(detail.review_id, 'md')}>
              导出报告（Markdown）
            </button>
          </div>

          <div className="mt12">
            {(detail.findings || [])
              .filter((f) => f.violated)
              .map((f) => (
                <div key={f.id} style={{ marginBottom: 8 }}>
                  <Badge level={f.severity}>{f.severity_label}</Badge>{' '}
                  <span style={{ fontSize: 13 }}>{f.title || f.rule_name}</span>
                  {f.location && (
                    <span className="dim" style={{ fontSize: 11.5, marginLeft: 8 }}>
                      📍 {f.location}
                    </span>
                  )}
                </div>
              ))}
          </div>
        </Card>
      )}

      <Card title="审查记录" sub={`${reports.length} 条`}>
        {reports.length === 0 ? (
          <Empty icon="📁" title="暂无审查记录" desc="到「审查工作台」上传文件开始审查" />
        ) : (
          <table>
            <thead>
              <tr>
                <th>文件名</th>
                <th style={{ width: 150 }}>审查时间</th>
                <th style={{ width: 100 }}>结论</th>
                <th style={{ width: 80 }}>问题数</th>
                <th style={{ width: 170 }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {reports.map((r) => (
                <tr key={r.review_id}>
                  <td style={{ fontWeight: 500 }}>{r.filename}</td>
                  <td className="dim" style={{ fontSize: 12.5 }}>
                    {fmtTime(r.created_at)}
                  </td>
                  <td>
                    <span
                      className={`badge ${
                        r.verdict_level === 'fail'
                          ? 'badge-blocker'
                          : r.verdict_level === 'warn'
                          ? 'badge-medium'
                          : 'badge-pass'
                      }`}
                    >
                      {r.verdict}
                    </span>
                  </td>
                  <td>{r.issue_count}</td>
                  <td>
                    <div className="btn-row">
                      <button className="btn btn-sm" onClick={() => openDetail(r.review_id)}>
                        查看
                      </button>
                      <button
                        className="btn btn-sm"
                        onClick={() => exportReport(r.review_id, 'docx')}
                      >
                        导出
                      </button>
                      <Confirm text="删除" onConfirm={() => del(r.review_id)}>
                        删除
                      </Confirm>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="已生成的文件" sub={`${exports.length} 个`}>
        {exports.length === 0 ? (
          <Empty icon="📄" title="暂无导出文件" desc="执行自动修订或导出报告后会在此显示" />
        ) : (
          <table>
            <thead>
              <tr>
                <th>文件名</th>
                <th style={{ width: 100 }}>大小</th>
                <th style={{ width: 150 }}>生成时间</th>
                <th style={{ width: 130 }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {exports.map((f) => (
                <tr key={f.filename}>
                  <td style={{ fontSize: 12.5 }}>{f.filename}</td>
                  <td className="dim" style={{ fontSize: 12.5 }}>
                    {fmtSize(f.size)}
                  </td>
                  <td className="dim" style={{ fontSize: 12.5 }}>
                    {fmtTime(f.created_at)}
                  </td>
                  <td>
                    <div className="btn-row">
                      <a
                        className="btn btn-sm"
                        href={api.downloadExport(f.filename)}
                        target="_blank"
                        rel="noreferrer"
                      >
                        下载
                      </a>
                      <Confirm text="删除" onConfirm={() => delExport(f.filename)}>
                        删除
                      </Confirm>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  )
}
