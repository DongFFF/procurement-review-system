import React, { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import ReviewPage from './ReviewPage'
import SettingsPage from './SettingsPage'
import KnowledgePage from './KnowledgePage'
import RulesPage from './RulesPage'
import HistoryPage from './HistoryPage'
import { Alert, Spinner } from './components'

export default function App() {
  const [page, setPage] = useState('review')
  const [config, setConfig] = useState(null)
  const [health, setHealth] = useState(null)
  const [bootErr, setBootErr] = useState('')

  const loadConfig = useCallback(async () => {
    try {
      const c = await api.getConfig()
      setConfig(c)
      setBootErr('')
    } catch (e) {
      setBootErr(`无法连接后端服务：${e.message}`)
    }
  }, [])

  useEffect(() => {
    ;(async () => {
      await loadConfig()
      try {
        setHealth(await api.health())
      } catch {
        /* 忽略 */
      }
    })()
  }, [loadConfig])

  const activeProvider = config?.settings?.providers?.find(
    (p) => p.id === config.settings.active_provider_id
  )

  if (!config) {
    return (
      <div
        style={{
          display: 'flex',
          height: '100vh',
          alignItems: 'center',
          justifyContent: 'center',
          flexDirection: 'column',
          gap: 14,
        }}
      >
        {bootErr ? (
          <div style={{ maxWidth: 520 }}>
            <Alert kind="error">{bootErr}</Alert>
            <button className="btn btn-primary" onClick={loadConfig}>
              重试连接
            </button>
          </div>
        ) : (
          <>
            <Spinner dark />
            <div className="dim">正在启动…</div>
          </>
        )}
      </div>
    )
  }

  const NAV = [
    ['review', '🔍', '审查工作台'],
    ['history', '📁', '审查记录'],
    ['knowledge', '📚', '知识库'],
    ['rules', '📋', '审查规则'],
    ['settings', '⚙️', '系统设置'],
  ]

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <h1>中直高校采购需求审查系统</h1>
          <div className="ver">v{health?.version || '0.1.0'}</div>
        </div>

        <nav className="nav">
          {NAV.map(([k, icon, label]) => (
            <div
              key={k}
              className={`nav-item ${page === k ? 'active' : ''}`}
              onClick={() => setPage(k)}
            >
              <span className="icon">{icon}</span>
              <span>{label}</span>
              {k === 'knowledge' && health?.kb_chunks > 0 && (
                <span className="nav-badge">{health.kb_documents}</span>
              )}
              {k === 'rules' && health?.rules > 0 && (
                <span className="nav-badge">{health.rules}</span>
              )}
            </div>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div style={{ marginBottom: 4 }}>
            {activeProvider ? (
              <>
                <span style={{ color: '#10b981' }}>●</span>{' '}
                {activeProvider.name || activeProvider.id}
                <div style={{ fontSize: 10, marginTop: 2, color: '#6b7280' }}>
                  {activeProvider.model}
                </div>
              </>
            ) : (
              <>
                <span style={{ color: '#ef4444' }}>●</span> 未配置模型
              </>
            )}
          </div>
        </div>
      </aside>

      <main className="main">
        {page === 'review' && (
          <ReviewPage config={config} onGoSettings={() => setPage('settings')} />
        )}
        {page === 'history' && <HistoryPage onOpen={() => setPage('review')} />}
        {page === 'knowledge' && <KnowledgePage />}
        {page === 'rules' && <RulesPage />}
        {page === 'settings' && (
          <SettingsPage
            config={config}
            reload={async () => {
              await loadConfig()
              try {
                setHealth(await api.health())
              } catch {
                /* 忽略 */
              }
            }}
          />
        )}
      </main>
    </div>
  )
}
