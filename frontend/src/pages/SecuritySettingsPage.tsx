import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  getSecurityProfile,
  listToolDefinitions,
  updateSecurityProfile,
} from '../api/workbench'
import type { SecurityProfileUpdate } from '../types/contracts'
import { localizeTool, useI18n } from '../i18n'

export function SecuritySettingsPage() {
  const { language, text } = useI18n()
  const queryClient = useQueryClient()
  const profileQuery = useQuery({
    queryKey: ['security-profile', 'default'],
    queryFn: () => getSecurityProfile(),
  })
  const toolsQuery = useQuery({
    queryKey: ['tool-definitions'],
    queryFn: listToolDefinitions,
  })
  const [draft, setDraft] = useState<SecurityProfileUpdate | null>(null)
  const [scopeText, setScopeText] = useState('')

  useEffect(() => {
    if (!profileQuery.data) return
    setScopeText(profileQuery.data.resource_scopes.join('\n'))
    setDraft({
      allowed_actions: profileQuery.data.allowed_actions,
      resource_scopes: profileQuery.data.resource_scopes,
      allow_egress: profileQuery.data.allow_egress,
      max_affected_objects: profileQuery.data.max_affected_objects,
      approval_policy: profileQuery.data.approval_policy,
    })
  }, [profileQuery.data])

  const save = useMutation({
    mutationFn: (value: SecurityProfileUpdate) => updateSecurityProfile(value),
    onSuccess: (profile) => {
      setScopeText(profile.resource_scopes.join('\n'))
      queryClient.setQueryData(['security-profile', 'default'], profile)
      setDraft({
        allowed_actions: profile.allowed_actions,
        resource_scopes: profile.resource_scopes,
        allow_egress: profile.allow_egress,
        max_affected_objects: profile.max_affected_objects,
        approval_policy: profile.approval_policy,
      })
    },
  })

  if (profileQuery.isError || toolsQuery.isError) {
    return (
      <p role="alert" className="text-sm text-rose-700">
        {text('Security profile is unavailable.', '安全配置不可用。')}
      </p>
    )
  }
  if (profileQuery.isLoading || toolsQuery.isLoading || !draft) {
    return (
      <p className="text-sm text-slate-500">
        {text('Loading security profile…', '正在加载安全配置……')}
      </p>
    )
  }

  const toggle = (name: string) => {
    const next = draft.allowed_actions.includes(name)
      ? draft.allowed_actions.filter((item) => item !== name)
      : [...draft.allowed_actions, name]
    if (next.length) setDraft({ ...draft, allowed_actions: next })
  }
  const toggleApproval = (name: string) => {
    const current = draft.approval_policy.required_actions
    const next = current.includes(name)
      ? current.filter((item) => item !== name)
      : [...current, name]
    setDraft({
      ...draft,
      approval_policy: { ...draft.approval_policy, required_actions: next },
    })
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-800">
            {text('Persistent security profile', '长期安全配置')}
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500">
            {text(
              'These permissions persist across conversations. Every TaskRun records the exact profile version it inherited.',
              '这些权限会跨对话持续生效。每个 TaskRun 都会记录它继承的准确配置版本。',
            )}
          </p>
        </div>
        <span className="rounded-full border border-sky-400/25 bg-sky-400/10 px-3 py-1 text-xs text-indigo-600">
          {text('Active version', '当前版本')} v{profileQuery.data?.version}
        </span>
      </header>

      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <h2 className="font-medium text-slate-800">
          {text('Agent capabilities', 'Agent 能力权限')}
        </h2>
        <p className="mt-1 text-xs text-slate-500">
          {text(
            'Allowed means continuous authorization inside the resource scope—not approval for every call.',
            '“允许”表示在资源范围内持续授权，而不是每次调用都审批。',
          )}
        </p>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          {toolsQuery.data?.map((tool) => {
            const enabled = draft.allowed_actions.includes(tool.name)
            return (
              <div
                key={tool.name}
                className={`rounded-lg border p-3 ${enabled ? 'border-emerald-400/25 bg-emerald-400/5' : 'border-slate-200 bg-slate-50'}`}
              >
                <label className="flex cursor-pointer items-start gap-3">
                  <input
                    type="checkbox"
                    checked={enabled}
                    onChange={() => toggle(tool.name)}
                    className="mt-1"
                  />
                  <span>
                    <span className="block text-sm text-slate-800">
                      {localizeTool(tool.name, language)}{' '}
                      <code className="text-[11px] text-slate-500">
                        {tool.name}
                      </code>
                      {tool.base_risk && (
                        <span className="ml-2 rounded border border-slate-200 px-1.5 py-0.5 text-[10px] text-slate-500">
                          {tool.base_risk}
                        </span>
                      )}
                    </span>
                    <span className="mt-1 block text-xs leading-5 text-slate-500">
                      {tool.description}
                    </span>
                  </span>
                </label>
                {enabled && (
                  <label className="mt-3 flex items-center gap-2 border-t border-slate-200 pt-2 text-xs text-amber-700">
                    <input
                      type="checkbox"
                      checked={draft.approval_policy.required_actions.includes(
                        tool.name,
                      )}
                      onChange={() => toggleApproval(tool.name)}
                    />
                    {text(
                      'Always ask (system risk policy may also require approval)',
                      '始终审批（系统风险策略也可能独立要求审批）',
                    )}
                  </label>
                )}
              </div>
            )
          })}
        </div>
      </section>

      <section className="grid gap-4 rounded-xl border border-slate-200 bg-white p-5 md:grid-cols-2">
        <label className="text-sm text-slate-600">
          {text('Workspace resource scopes', '工作区资源范围')}
          <textarea
            aria-label={text('Workspace resource scopes', '工作区资源范围')}
            rows={4}
            value={scopeText}
            onChange={(event) => setScopeText(event.target.value)}
            className="mt-2 w-full rounded-md border border-slate-200 bg-slate-50 p-3 font-mono text-xs text-slate-800 outline-none focus:border-sky-400/50"
          />
          <span className="mt-1 block text-xs text-slate-500">
            {text(
              'Paths are relative to .runtime/workspace. Use ** for the whole isolated workspace.',
              '路径相对于 .runtime/workspace；使用 ** 表示整个隔离工作区。',
            )}
          </span>
        </label>
        <div className="space-y-4">
          <label className="block text-sm text-slate-600">
            {text('Maximum affected objects', '最大影响对象数')}
            <input
              type="number"
              min="1"
              value={draft.max_affected_objects}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  max_affected_objects: Number(event.target.value) || 1,
                })
              }
              className="mt-2 w-full rounded-md border border-slate-200 bg-slate-50 p-2 text-slate-800"
            />
          </label>
          <label className="block text-sm text-slate-600">
            {text('Bulk approval threshold', '批量操作审批阈值')}
            <input
              type="number"
              min="1"
              value={draft.approval_policy.bulk_action_threshold}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  approval_policy: {
                    ...draft.approval_policy,
                    bulk_action_threshold: Number(event.target.value) || 1,
                  },
                })
              }
              className="mt-2 w-full rounded-md border border-slate-200 bg-slate-50 p-2 text-slate-800"
            />
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={draft.allow_egress}
              onChange={(event) =>
                setDraft({ ...draft, allow_egress: event.target.checked })
              }
            />
            {text('Allow controlled data egress', '允许受控数据外发')}
          </label>
        </div>
      </section>

      <div className="flex items-center justify-between rounded-lg border border-slate-200 bg-slate-50 p-4">
        <p className="text-xs text-slate-500">
          {text(
            'Denied capabilities are visible and derived from unchecked tools; there is no hidden forbidden list.',
            '禁止能力由未勾选工具明确得出，不再存在隐藏的 forbidden 列表。',
          )}
        </p>
        <button
          disabled={save.isPending || !scopeText.trim()}
          onClick={() =>
            save.mutate({
              ...draft,
              resource_scopes: scopeText
                .split(/\r?\n/)
                .map((item) => item.trim())
                .filter(Boolean),
            })
          }
          className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
        >
          {save.isPending
            ? text('Saving…', '保存中……')
            : text('Save new version', '保存为新版本')}
        </button>
      </div>
      {save.isSuccess && (
        <p className="text-sm text-emerald-700">
          {text(
            'Security profile saved. New tasks will inherit the new version.',
            '安全配置已保存，新任务将继承新版本。',
          )}
        </p>
      )}
      {save.isError && (
        <p className="text-sm text-rose-700">{(save.error as Error).message}</p>
      )}
    </div>
  )
}
