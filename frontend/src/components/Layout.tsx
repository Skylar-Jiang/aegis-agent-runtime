import { useEffect } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useI18n } from '../i18n'

const icons: Record<string, string> = {
  core: 'M8 4h12M8 10h12M8 16h12M3 4h.01M3 10h.01M3 16h.01',
  chat: 'M20 10a8 8 0 0 1-8 8H4l-2 3V10a9 9 0 0 1 18 0Z',
  tasks: 'M6 3h10l4 4v14H4V3h2Zm8 0v5h6M8 12h8M8 16h6',
  audit: 'M6 3h12v18H6zM9 7h6M9 11h6M9 15h4',
  approval: 'M12 2 3 6v6c0 5 9 10 9 10s9-5 9-10V6l-9-4Zm-4 10 3 3 5-6',
  experiments: 'M4 20h16M7 20V11m5 9V4m5 16v-7',
  settings:
    'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1 1-3Z',
  runtime:
    'M4 5c0-3 16-3 16 0s-16 3-16 0v14c0 3 16 3 16 0V5M4 12c0 3 16 3 16 0',
}
function Icon({ name }: { name: string }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={icons[name]} />
    </svg>
  )
}
export function Layout() {
  const { language, setLanguage, text } = useI18n()
  const { pathname } = useLocation()
  useEffect(() => {
    document.documentElement.lang = language
  }, [language])
  const links = [
    { path: '/', icon: 'core', label: text('Task workbench', '任务工作台') },
    {
      path: '/conversations',
      icon: 'chat',
      label: text('Conversations', '连续对话'),
    },
    { path: '/tasks', icon: 'tasks', label: text('Task history', '任务历史') },
    {
      path: '/approvals',
      icon: 'approval',
      label: text('Approvals', '审批中心'),
    },
    { path: '/audit', icon: 'audit', label: text('Audit', '执行审计') },
    {
      path: '/experiments',
      icon: 'experiments',
      label: text('Experiments', '实验记录'),
    },
    {
      path: '/settings/security',
      icon: 'settings',
      label: text('Security settings', '安全设置'),
    },
  ]
  const title =
    pathname === '/core'
      ? links[0].label
      : pathname === '/runtime'
        ? text('Runtime', '运行时')
        : (links.find((link) => link.path === pathname)?.label ?? 'Aegis Core')
  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <NavLink to="/" className="app-brand">
          <span className="brand-mark">A</span>
          <span>
            Aegis <strong>Core</strong>
          </span>
        </NavLink>
        <div className="workspace-label">
          <span className="workspace-monogram">密</span>
          <div>
            {text('Cryptography workspace', '密码学实验空间')}
            <small>{text('Local development', '本地开发环境')}</small>
          </div>
        </div>
        <p className="nav-caption">{text('WORKSPACE', '工作空间')}</p>
        <nav aria-label={text('Main navigation', '主导航')}>
          {links.map((link) => (
            <NavLink
              key={link.path}
              to={link.path}
              end={link.path === '/'}
              className={({ isActive }) =>
                isActive || (link.path === '/' && pathname === '/core')
                  ? 'nav-item active'
                  : 'nav-item'
              }
            >
              <Icon name={link.icon} />
              <span>{link.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <NavLink
            to="/runtime"
            className={({ isActive }) =>
              isActive ? 'nav-item active' : 'nav-item'
            }
          >
            <Icon name="runtime" />
            <span>{text('Runtime foundation', 'Runtime 基础')}</span>
          </NavLink>
          <div className="sidebar-version">
            <span className="local-avatar">L</span>
            <div>
              {text('Local workspace', '本地工作区')}
              <small>Aegis Runtime / Core v1</small>
            </div>
          </div>
        </div>
      </aside>
      <div className="app-content">
        <header className="app-topbar">
          <div className="breadcrumbs">
            <span>{text('Workspace', '工作空间')}</span>
            <span>/</span>
            <strong>{title}</strong>
          </div>
          <div
            className="language-switcher"
            aria-label={text('Language switcher', '语言切换')}
          >
            <button
              onClick={() => setLanguage('zh-CN')}
              aria-pressed={language === 'zh-CN'}
            >
              中文
            </button>
            <button
              onClick={() => setLanguage('en')}
              aria-pressed={language === 'en'}
            >
              EN
            </button>
          </div>
        </header>
        <main className="app-main">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
