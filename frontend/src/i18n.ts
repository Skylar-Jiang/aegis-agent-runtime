import { create } from 'zustand'

export type Language = 'zh-CN' | 'en'

const STORAGE_KEY = 'aegis-language'

function initialLanguage(): Language {
  if (typeof window !== 'undefined') {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === 'zh-CN' || stored === 'en') return stored
  }
  if (
    typeof navigator !== 'undefined' &&
    navigator.language.toLowerCase().startsWith('zh')
  ) {
    return 'zh-CN'
  }
  return 'en'
}

interface LanguageState {
  language: Language
  setLanguage: (language: Language) => void
}

export const useLanguageStore = create<LanguageState>((set) => ({
  language: initialLanguage(),
  setLanguage: (language) => {
    if (typeof window !== 'undefined')
      window.localStorage.setItem(STORAGE_KEY, language)
    if (typeof document !== 'undefined')
      document.documentElement.lang = language
    set({ language })
  },
}))

export function useI18n() {
  const language = useLanguageStore((state) => state.language)
  const setLanguage = useLanguageStore((state) => state.setLanguage)
  return {
    language,
    setLanguage,
    text: (english: string, chinese: string) =>
      language === 'zh-CN' ? chinese : english,
  }
}

const statusLabels: Record<string, string> = {
  CREATED: '已创建',
  PLANNED: '已规划',
  ACTIVE: '进行中',
  RUNNING: '运行中',
  PENDING: '待处理',
  WAITING_APPROVAL: '等待审批',
  GRANTED: '已批准',
  DENIED: '已拒绝',
  EXPIRED: '已过期',
  CANCELLED: '已取消',
  INTERRUPTED: '已中断',
  COMPLETED: '已完成',
  COMMITTED: '已提交',
  BLOCKED: '已阻断',
  ROLLED_BACK: '已回滚',
  PRESERVED: '已保留',
  CONFLICT: '存在冲突',
  FAILED: '失败',
  READY: '就绪',
  RISK_CLASSIFYING: '风险分析中',
  CHECKPOINT_CREATING: '创建检查点',
  EXECUTING_FAST: '快速执行中',
  EXECUTING_SANDBOX: '受控执行中',
  SAFETY_CHECKING: '安全检查中',
  COMMITTING: '提交中',
  ROLLING_BACK: '回滚中',
}

export function localizeStatus(status: string, language: Language) {
  return language === 'zh-CN' ? (statusLabels[status] ?? status) : status
}

const toolLabels: Record<string, string> = {
  list_dir: '列出目录',
  read_file: '读取文件',
  create_file: '创建文件（仅新建）',
  write_file: '写入文件',
  delete_file: '删除文件',
  run_shell: '运行受限命令',
  download_url: '下载链接',
  memory_read: '读取记忆',
  memory_write: '写入记忆',
  send_email_dry_run: '邮件发送预演',
}

export function localizeTool(
  toolName: string | null | undefined,
  language: Language,
) {
  if (!toolName) return language === 'zh-CN' ? '运行时请求' : 'Runtime request'
  return language === 'zh-CN' ? (toolLabels[toolName] ?? toolName) : toolName
}
