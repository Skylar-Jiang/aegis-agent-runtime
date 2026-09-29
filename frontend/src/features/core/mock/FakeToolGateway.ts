import type { BehaviorEvent, PermissionGrant } from '../generated'
import type { CoreGatewayClient, CoreSnapshot, Scenario } from '../gateway'
import fixtureEvents from './events.json'

const fixture = fixtureEvents as BehaviorEvent[]

/** Fixed scenarios only: no permission evaluation, signing or tool execution. */
export class FakeToolGateway implements CoreGatewayClient {
  private snapshot: CoreSnapshot | null = null

  async load(scenario: Scenario): Promise<CoreSnapshot> {
    this.snapshot = null
    if (scenario === 'error') throw new Error('模拟服务暂时不可用，请重试。')
    const grant: PermissionGrant = {
      subject: 'fixture-user',
      skill: 'writing-demo',
      tool: 'write_file',
      action: 'write_file',
      resource: 'reports/demo.txt',
      effect: 'ALLOW',
      scope: 'TASK',
      scope_ref: 'p2-task-1',
      source: 'fixture:p2/permission',
    }
    const root = structuredClone(fixture[0])
    const decisions = {
      allow: ['ALLOW', 'ALLOWED'],
      deny: ['DENY', 'SYSTEM_DENY'],
      confirm: ['REQUIRE_CONFIRMATION', 'CONFIRMATION_REQUIRED'],
      replan: ['REQUIRE_REPLAN', 'VERSION_STALE'],
    } as const
    const pair = scenario === 'empty' ? null : decisions[scenario]
    const decision: CoreSnapshot['decision'] = pair
      ? {
          decision: pair[0],
          reason_code: pair[1],
          evidence_refs: ['fixture:p2/permission'],
          versions: { contract: 1, policy: 'fixture-policy-v1' },
          confirmation_id: scenario === 'confirm' ? 'p2-confirmation-1' : null,
        }
      : null
    if (decision) {
      root.decision = decision.decision
      root.reason_code = decision.reason_code
      root.state =
        scenario === 'confirm' ? 'WAITING_CONFIRMATION' : decision.decision
    }
    const events = scenario === 'empty' ? [] : [root]
    if (scenario === 'allow')
      events.push({
        ...structuredClone(fixture[3]),
        sequence: 2,
        parent_event_id: root.event_id,
      })
    this.snapshot = {
      demo: true,
      task_id: 'p2-task-1',
      request_id: 'p2-request-1',
      contract: {
        contract_id: 'p2-contract-1',
        session_id: 'p2-session-1',
        task_id: 'p2-task-1',
        user_id: 'fixture-user',
        version: scenario === 'replan' ? 2 : 1,
        goals: ['在授权目录中写入演示报告'],
        completion_criteria: ['报告仅写入 reports/demo.txt'],
        allowed: [
          {
            tool: 'write_file',
            action: 'write_file',
            resource: 'reports/demo.txt',
            effect: 'WRITE',
          },
        ],
        denied: [
          { tool: '*', action: 'run_shell', resource: '*', effect: '*' },
        ],
        limits: { max_affected_objects: 1 },
        confirmation: { required_actions: scenario === 'confirm' ? ['write_file'] : [] },
        policy_version: 'fixture-policy-v1',
        tool_manifest_digest: 'fixture:tools-not-a-real-digest',
      },
      effective_permission: {
        allowed: scenario === 'deny' ? [] : [grant],
        denied:
          scenario === 'deny'
            ? [{ ...grant, effect: 'DENY', source: 'fixture:system-deny' }]
            : [],
        constraints: { max_affected_objects: 1 },
        matched_sources: ['fixture:p2/permission'],
        conflict_reason: scenario === 'deny' ? '系统策略拒绝此模拟请求' : null,
        requires_confirmation: scenario === 'confirm',
      },
      decision,
      confirmation_status:
        scenario === 'confirm' ? 'WAITING_CONFIRMATION' : null,
      events,
    }
    return structuredClone(this.snapshot)
  }

  async resolveConfirmation(
    confirmationId: string,
    confirmed: boolean,
  ): Promise<CoreSnapshot> {
    if (
      confirmationId !== 'p2-confirmation-1' ||
      this.snapshot?.confirmation_status !== 'WAITING_CONFIRMATION'
    ) {
      throw new Error('不存在待处理的模拟确认请求。')
    }
    const snapshot = structuredClone(this.snapshot)
    snapshot.confirmation_status = confirmed ? 'CONFIRMED' : 'REJECTED'
    snapshot.effective_permission.requires_confirmation = false
    snapshot.decision = {
      decision: confirmed ? 'ALLOW' : 'DENY',
      reason_code: confirmed ? 'ALLOWED' : 'USER_DENY',
      evidence_refs: ['fixture:p2/confirmation'],
      versions: { contract: 1, policy: 'fixture-policy-v1' },
      confirmation_id: null,
    }
    snapshot.events = confirmed
      ? structuredClone(fixture)
      : [
          snapshot.events[0],
          {
            ...structuredClone(fixture[1]),
            state: 'REJECTED',
            decision: 'DENY',
            reason_code: 'USER_DENY',
          },
        ]
    this.snapshot = snapshot
    return structuredClone(snapshot)
  }
}
