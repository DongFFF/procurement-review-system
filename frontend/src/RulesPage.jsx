import React, { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { Alert, Badge, Card, Empty, Spinner } from './components'

/* =========================================================================
   审查规则库：查看系统依据哪些规则进行审查
   ========================================================================= */

export default function RulesPage() {
  const [rules, setRules] = useState([])
  const [stats, setStats] = useState(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [q, setQ] = useState('')
  const [cat, setCat] = useState('')
  const [sev, setSev] = useState('')
  const [open, setOpen] = useState(null)

  const load = useCallback(async () => {
    try {
      const r = await api.listRules({ q, category: cat, severity: sev })
      setRules(r.rules || [])
      setStats(r.stats)
    } catch (e) {
      setErr(e.message)
    }
  }, [q, cat, sev])

  useEffect(() => {
    load()
  }, [load])

  const reload = async () => {
    setBusy('reload')
    setErr('')
    setMsg('')
    try {
      const r = await api.reloadRules()
      setMsg(`规则库已重载：${r.stats.rule_count} 条规则`)
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
        <h2>审查规则库</h2>
        <p>
          每条规则对应一项法规要求，审查时逐条核对。规则以 YAML 存放在
          <span className="mono"> knowledge/rules/</span>，可直接编辑扩展
        </p>
      </div>

      {err && <Alert kind="error" onClose={() => setErr('')}>{err}</Alert>}
      {msg && <Alert kind="success" onClose={() => setMsg('')}>{msg}</Alert>}

      {stats?.errors?.length > 0 && (
        <Alert kind="warn">
          规则加载时有 {stats.errors.length} 处警告：
          <div className="mono" style={{ fontSize: 11, marginTop: 4 }}>
            {stats.errors.slice(0, 5).join('；')}
          </div>
        </Alert>
      )}

      <Card title="规则库概览">
        <div className="stat-row" style={{ marginBottom: 14 }}>
          <div className="s">
            <span className="n" style={{ color: 'var(--primary)' }}>{stats?.rule_count ?? '—'}</span>
            <span>条规则</span>
          </div>
          <div className="s">
            <span className="n" style={{ color: 'var(--success)' }}>{stats?.enabled_count ?? '—'}</span>
            <span>已启用</span>
          </div>
        </div>

        {stats?.by_category && (
          <div style={{ marginBottom: 12 }}>
            <div className="dim" style={{ fontSize: 12, marginBottom: 6 }}>
              按类别：
            </div>
            {Object.entries(stats.by_category).map(([k, v]) => (
              <span
                key={k}
                className="cite"
                style={{ cursor: 'pointer' }}
                onClick={() => setCat(Object.keys(CATEGORY_MAP).find((c) => CATEGORY_MAP[c] === k) || '')}
              >
                {k} {v}
              </span>
            ))}
          </div>
        )}

        {stats?.by_severity && (
          <div>
            <div className="dim" style={{ fontSize: 12, marginBottom: 6 }}>
              按严重级别：
            </div>
            {Object.entries(stats.by_severity).map(([k, v]) => (
              <span key={k} className="cite">
                {k} {v}
              </span>
            ))}
          </div>
        )}

        <div className="btn-row mt12">
          <button className="btn btn-sm" disabled={busy === 'reload'} onClick={reload}>
            {busy === 'reload' ? <Spinner dark /> : null} 重载规则库
          </button>
          <span className="dim" style={{ fontSize: 12 }}>
            修改 YAML 后点此重载，无需重启程序
          </span>
        </div>
      </Card>

      <Card title="规则列表" sub={`当前显示 ${rules.length} 条`}>
        <div className="grid-3" style={{ marginBottom: 14 }}>
          <input
            type="text"
            placeholder="搜索规则名称、代码或检查内容…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <select value={cat} onChange={(e) => setCat(e.target.value)}>
            <option value="">全部类别</option>
            {Object.entries(CATEGORY_MAP).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
          <select value={sev} onChange={(e) => setSev(e.target.value)}>
            <option value="">全部级别</option>
            <option value="blocker">严重（必须整改）</option>
            <option value="high">高</option>
            <option value="medium">中</option>
            <option value="low">低</option>
          </select>
        </div>

        {rules.length === 0 ? (
          <Empty
            icon="📋"
            title="没有找到规则"
            desc="请确认 knowledge/rules/ 目录下存在 YAML 规则文件"
          />
        ) : (
          rules.map((r) => (
            <div key={r.code} className="finding">
              <div
                className="finding-head"
                onClick={() => setOpen(open === r.code ? null : r.code)}
              >
                <div className="finding-title">
                  <div className="t">{r.name}</div>
                  <div className="m">
                    <Badge level={r.severity}>{r.severity_label}</Badge>
                    <span>{r.category_label}</span>
                    <span className="mono dim" style={{ fontSize: 11 }}>
                      {r.code}
                    </span>
                    {r.applies_to?.length > 0 && (
                      <span className="dim">适用：{r.applies_to.join('、')}</span>
                    )}
                  </div>
                </div>
                <span className="dim" style={{ fontSize: 12, flexShrink: 0 }}>
                  {open === r.code ? '收起 ▲' : '详情 ▼'}
                </span>
              </div>

              {open === r.code && (
                <div className="finding-body">
                  <KV k="法定依据">{r.legal_basis || '—'}</KV>
                  <KV k="检查要点">{r.check_prompt || '—'}</KV>
                  {r.violation_example && (
                    <KV k="典型违规示例">
                      <div className="quote">{r.violation_example}</div>
                    </KV>
                  )}
                  {r.suggestion_template && (
                    <KV k="整改建议模板">
                      <div className="suggest">{r.suggestion_template}</div>
                    </KV>
                  )}
                  {r.keywords?.length > 0 && (
                    <KV k="触发关键词">
                      <div>
                        {r.keywords.map((k, i) => (
                          <span key={i} className="cite">
                            {k}
                          </span>
                        ))}
                      </div>
                    </KV>
                  )}
                  <KV k="来源文件">
                    <span className="mono dim">{r.source_file}</span>
                  </KV>
                </div>
              )}
            </div>
          ))
        )}
      </Card>
    </div>
  )
}

const CATEGORY_MAP = {
  legality: '合法合规性',
  completeness: '需求完整性',
  technical: '技术参数倾向性',
  commercial: '商务条款合理性',
  scoring: '评分办法',
  policy: '政策功能',
  budget: '预算与资金',
  procedure: '程序与时限',
}

function KV({ k, children }) {
  if (!children) return null
  return (
    <div className="kv">
      <div className="k">{k}</div>
      <div className="v">{children}</div>
    </div>
  )
}
