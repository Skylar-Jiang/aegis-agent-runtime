import { expect, test } from '@playwright/test';

const api = 'http://127.0.0.1:18000/api';

function graphPayload(scenario: string, nodes: object[]) {
  const taskId = `e2e-${scenario}-${Date.now()}`;
  return {
    taskId,
    graph: {
      graph_id: `graph-${taskId}`,
      task_id: taskId,
      max_parallelism: 2,
      nodes,
    },
  };
}

function request(taskId: string, nodeId: string, toolName: string, arguments_: object, dependencies: string[] = []) {
  return {
    task_id: taskId,
    graph_id: `graph-${taskId}`,
    node_id: nodeId,
    dependencies,
    parallel_safe: true,
    effect_targets: toolName === 'delete_file' || toolName === 'write_file' ? [`file:${String((arguments_ as { path?: string }).path)}`] : [],
    request: {
      task_id: taskId,
      step_id: nodeId,
      request_id: `request-${nodeId}-${taskId}`,
      tool_name: toolName,
      arguments: arguments_,
      objective: 'Controlled browser acceptance fixture',
      context_summary: 'Deterministic final-integration browser acceptance',
      source_type: 'user',
      requested_at: new Date().toISOString(),
      task_contract: {
        allowed_actions: ['list_dir', 'write_file', 'delete_file', 'run_shell'],
        allowed_resources: ['e2e-target.txt', '.'],
        max_affected_objects: 2,
      },
    },
  };
}

async function submit(page: import('@playwright/test').Page, graph: object) {
  const response = await page.request.post(`${api}/task-graphs`, { data: graph });
  expect(response.ok()).toBeTruthy();
}

async function waitForApproval(page: import('@playwright/test').Page, taskId: string) {
  await expect.poll(async () => {
    const response = await page.request.get(`${api}/approvals?status=PENDING&task_id=${taskId}`);
    return (await response.json()).data.length;
  }).toBe(1);
  const response = await page.request.get(`${api}/approvals?status=PENDING&task_id=${taskId}`);
  return (await response.json()).data[0] as { approval_id: string };
}

async function graphStatus(page: import('@playwright/test').Page, taskId: string) {
  const response = await page.request.get(`${api}/tasks/${taskId}/graph`);
  expect(response.ok()).toBeTruthy();
  return (await response.json()).data as { status: string; nodes: Array<{ node_id: string; status: string; blocked_reason?: string }> };
}

test.describe.serial('Final runtime browser scenarios', () => {
  test('Grant resumes a waiting graph and completes its dependent node', async ({ page }, testInfo) => {
    const { taskId, graph } = graphPayload('grant', []);
    graph.nodes.push(
      request(taskId, 'setup', 'write_file', { path: 'e2e-target.txt', content: 'controlled fixture' }),
      request(taskId, 'delete', 'delete_file', { path: 'e2e-target.txt' }, ['setup']),
      request(taskId, 'independent', 'list_dir', { path: '.' }),
    );
    await submit(page, graph);
    const approval = await waitForApproval(page, taskId);

    await page.goto(`/approvals?task_id=${taskId}`);
    await page.getByPlaceholder('Approver identity...').fill('e2e-reviewer');
    await page.screenshot({ path: testInfo.outputPath('waiting-approval.png') });
    await page.getByRole('button', { name: `Grant ${approval.approval_id}` }).click();
    await expect(page.getByText('GRANTED')).toBeVisible();
    await expect.poll(() => graphStatus(page, taskId).then((value) => value.status)).toBe('COMPLETED');

    await page.goto(`/runtime?task_id=${taskId}`);
    await expect(page.getByText(`graph-${taskId}`)).toBeVisible();
    await expect(page.getByText('COMPLETED').first()).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('grant-resumed.png') });
  });

  test('Deny blocks the target and its dependent graph node without committing deletion', async ({ page }, testInfo) => {
    const { taskId, graph } = graphPayload('deny', []);
    graph.nodes.push(
      request(taskId, 'setup', 'write_file', { path: 'e2e-target.txt', content: 'preserve on deny' }),
      request(taskId, 'delete', 'delete_file', { path: 'e2e-target.txt' }, ['setup']),
      request(taskId, 'dependent', 'list_dir', { path: '.' }, ['delete']),
    );
    await submit(page, graph);
    const approval = await waitForApproval(page, taskId);

    await page.goto(`/approvals?task_id=${taskId}`);
    await page.getByPlaceholder('Approver identity...').fill('e2e-reviewer');
    await page.getByRole('button', { name: `Deny ${approval.approval_id}` }).click();
    await expect(page.getByText('DENIED')).toBeVisible();
    await expect.poll(() => graphStatus(page, taskId).then((value) => value.status)).toBe('FAILED');

    await page.goto(`/runtime?task_id=${taskId}`);
    await expect(page.getByText('dependency_failed')).toBeVisible();
    await expect(page.getByText('BLOCKED').first()).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('deny-blocked.png') });
  });

  test('Cancel converges a waiting graph and rejects subsequent resume', async ({ page }, testInfo) => {
    const { taskId, graph } = graphPayload('cancel', []);
    graph.nodes.push(request(taskId, 'delete', 'delete_file', { path: 'e2e-target.txt' }));
    await submit(page, graph);
    const approval = await waitForApproval(page, taskId);

    const cancelled = await page.request.post(`${api}/task-graphs/${graph.graph_id}/cancel`);
    expect(cancelled.ok()).toBeTruthy();
    const resume = await page.request.post(`${api}/task-graphs/${graph.graph_id}/resume`, { data: { approval_id: approval.approval_id } });
    expect(resume.status()).toBe(409);

    await page.goto(`/runtime?task_id=${taskId}`);
    await expect(page.getByText('graph_cancelled')).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('cancelled.png') });
  });

  test('Cancel selectively rolls back a pending effect and preserves an independent effect', async ({ page }) => {
    const created = await page.request.post(`${api}/demo/selective-rollback`);
    expect(created.ok()).toBeTruthy();
    const fixture = (await created.json()).data as { task_id: string; graph_id: string; approval_id: string };

    await page.goto(`/runtime?task_id=${fixture.task_id}`);
    await expect(page.getByText('COMMITTED').first()).toBeVisible();
    await expect(page.getByText('PENDING').first()).toBeVisible();
    await page.getByRole('button', { name: 'Cancel graph' }).click();
    await expect(page.getByText('ROLLED_BACK').first()).toBeVisible();
    await expect(page.getByText('PRESERVED').first()).toBeVisible();
    await page.screenshot({ path: '../docs/final-integration-assets/selective-rollback-preserved.png' });

    const resume = await page.request.post(`${api}/task-graphs/${fixture.graph_id}/resume`, { data: { approval_id: fixture.approval_id } });
    expect(resume.status()).toBe(409);
    await page.goto(`/audit?task_id=${fixture.task_id}`);
    await expect(page.getByText('Task cancelled').first()).toBeVisible();
    await expect(page.getByText('Rollback').first()).toBeVisible();
    await expect(page.getByText('PRESERVED').first()).toBeVisible();
  });

  test('dangerous shell input is escalated and blocked with graph audit facts', async ({ page }, testInfo) => {
    const { taskId, graph } = graphPayload('security', []);
    graph.nodes.push(request(taskId, 'dangerous-shell', 'run_shell', { command: 'rm -rf e2e-target.txt' }));
    await submit(page, graph);
    await expect.poll(() => graphStatus(page, taskId).then((value) => value.status)).toBe('FAILED');

    await page.goto(`/runtime?task_id=${taskId}`);
    await expect(page.getByText('BLOCKED').first()).toBeVisible();
    await page.goto(`/audit?task_id=${taskId}`);
    await expect(page.getByRole('heading', { name: 'Audit timeline' })).toBeVisible();
    await expect(page.getByText('Safety block').first()).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('security-blocked-audit.png') });
  });
});
