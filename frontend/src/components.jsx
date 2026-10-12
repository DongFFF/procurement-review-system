import React from 'react'

export const SEV_CLASS = {
  blocker: 'badge-blocker',
  high: 'badge-high',
  medium: 'badge-medium',
  low: 'badge-low',
}

export function Badge({ level, children }) {
  return <span className={`badge ${SEV_CLASS[level] || 'badge-gray'}`}>{children}</span>
}

export function Alert({ kind = 'info', children, onClose }) {
  const icons = { error: '✕', warn: '⚠', info: 'ℹ', success: '✓' }
  return (
    <div className={`alert alert-${kind}`}>
      <span style={{ flexShrink: 0 }}>{icons[kind] || 'ℹ'}</span>
      <div style={{ flex: 1 }}>{children}</div>
      {onClose && (
        <span
          onClick={onClose}
          style={{ cursor: 'pointer', opacity: 0.55, flexShrink: 0 }}
          title="关闭"
        >
          ✕
        </span>
      )}
    </div>
  )
}

export function Spinner({ dark }) {
  return <span className={`spinner ${dark ? 'spinner-dark' : ''}`} />
}

export function Field({ label, hint, children }) {
  return (
    <div className="field">
      {label && <label>{label}</label>}
      {children}
      {hint && <div className="hint">{hint}</div>}
    </div>
  )
}

export function Empty({ icon = '📄', title, desc }) {
  return (
    <div className="empty">
      <div className="big">{icon}</div>
      <div style={{ fontWeight: 500, marginBottom: 5 }}>{title}</div>
      {desc && <div style={{ fontSize: 12.5 }}>{desc}</div>}
    </div>
  )
}

export function Card({ title, sub, extra, children, className = '' }) {
  return (
    <div className={`card ${className}`}>
      {(title || extra) && (
        <div className="flex-between" style={{ marginBottom: title ? 14 : 0 }}>
          <h3 className="card-title" style={{ margin: 0 }}>
            {title}
            {sub && <span className="sub">{sub}</span>}
          </h3>
          {extra}
        </div>
      )}
      {children}
    </div>
  )
}

export function KV({ k, children }) {
  if (children === null || children === undefined || children === '') return null
  return (
    <div className="kv">
      <div className="k">{k}</div>
      <div className="v">{children}</div>
    </div>
  )
}

export function Confirm({ text, onConfirm, children, className = 'btn btn-sm btn-danger' }) {
  const [armed, setArmed] = React.useState(false)
  React.useEffect(() => {
    if (!armed) return
    const t = setTimeout(() => setArmed(false), 4000)
    return () => clearTimeout(t)
  }, [armed])
  return (
    <button
      className={className}
      onClick={() => {
        if (armed) {
          onConfirm()
          setArmed(false)
        } else {
          setArmed(true)
        }
      }}
      title={armed ? '再次点击确认' : ''}
    >
      {armed ? `确认${text || ''}？` : children}
    </button>
  )
}

export function fmtSize(bytes) {
  if (!bytes) return '0 B'
  const u = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let n = bytes
  while (n >= 1024 && i < u.length - 1) {
    n /= 1024
    i++
  }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${u[i]}`
}

export function fmtTime(ts) {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}
