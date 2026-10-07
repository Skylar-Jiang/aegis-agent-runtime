import { expect, test } from "@playwright/test";
import { readFileSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";

const integration = process.env.INTENT_INTEGRATION_DIR;
if (!integration)
  throw new Error(
    "INTENT_INTEGRATION_DIR must name the completed integration run",
  );
const output = resolve(integration, "browser");
mkdirSync(output, { recursive: true });
const cases = [
  "normal-0-0",
  "normal-1-0",
  "normal-2-0",
  "retrieval-0-0",
  "memory-0-0",
  "failure-0-0",
];

for (const id of cases) {
  test(`real event timeline survives refresh: ${id}`, async ({ page }) => {
    const result = JSON.parse(
      readFileSync(resolve(integration, `full-${id}.json`), "utf8"),
    );
    await page.goto(`/core?task_id=${encodeURIComponent(result.task_id)}`);
    await expect(
      page.getByRole("region", { name: "意图风险时间线" }),
    ).toBeVisible();
    await expect(
      page.getByRole("img", { name: "按动作步骤显示风险" }),
    ).toBeVisible();
    const panel = page.getByRole("region", { name: "意图风险时间线" });
    if (result.safe_stop) {
      await expect(panel).toContainText("任务已安全终止");
      await expect(panel).toContainText("SAFE_STOP");
      await expect(panel).toContainText("上游引用");
    } else {
      await expect(panel).toContainText("CONTINUE");
    }
    await page.screenshot({
      path: resolve(output, `${id}.png`),
      fullPage: true,
    });
    await page.reload();
    await expect(panel).toContainText(
      result.safe_stop ? "任务已安全终止" : "CONTINUE",
    );
    await expect(
      page.getByRole("button", { name: "Mock 实验室 · 模拟数据" }),
    ).not.toHaveClass(/active/);
  });
}

test("new trusted report contract controls an actual HTTP write from the UI", async ({
  page,
}) => {
  await page.goto("/core");
  await page.locator("#core-objective").fill("生成包含现场证据的本地报告");
  await page.getByText("报告完成条件", { exact: true }).click();
  await page.getByLabel("执行前检查报告是否符合目标").check();
  await page
    .locator("#intent-report-path")
    .fill("reports/browser-confirmed.txt");
  await page.locator("#intent-required-text").fill("Evidence:");
  await page.getByRole("button", { name: "创建任务", exact: true }).click();
  await page.getByRole("button", { name: "确认契约", exact: true }).click();
  await page
    .getByRole("combobox", { name: "工具", exact: true })
    .selectOption("write_file");
  await page
    .getByLabel("资源路径", { exact: true })
    .fill("reports/browser-confirmed.txt");
  await page
    .getByRole("textbox", { name: "内容", exact: true })
    .fill("ADVERTISEMENT only");
  await page.getByRole("button", { name: /评估/ }).first().click();
  await expect(
    page.getByRole("region", { name: "意图风险时间线" }),
  ).toContainText("任务已安全终止");
  await expect(
    page.getByText("INTENT_DEVIATION", { exact: false }).first(),
  ).toBeVisible();
  await page.screenshot({
    path: resolve(output, "browser-new-contract-block.png"),
    fullPage: true,
  });
});

test("operator-approved bounded recovery executes through the real gateway", async ({
  page,
}) => {
  const result = JSON.parse(
    readFileSync(resolve(integration!, "full-retrieval-0-1.json"), "utf8"),
  );
  const original = result.snapshot.task.contract.contract;
  const target = `reports/browser-recovery-${Date.now()}.txt`;
  const body = {
    session_id: original.session_id,
    task_id: `browser-recovery-${Date.now()}`,
    user_id: original.user_id,
    goals: original.goals,
    completion_criteria: original.completion_criteria.map((v: string) =>
      v.startsWith("intent:report=") ? `intent:report=${target}` : v,
    ),
    allowed: original.allowed.map(
      (rule: { action: string; resource: string }) =>
        rule.action === "write_file" ? { ...rule, resource: target } : rule,
    ),
    confirmation: original.confirmation,
    policy_version: original.policy_version,
    tool_manifest_digest: original.tool_manifest_digest,
  };
  const created = await (
    await page.request.post("/api/v1/contracts", { data: body })
  ).json();
  const confirmed = await (
    await page.request.post(
      `/api/v1/contracts/${created.data.ref.contract_id}/confirm`,
      { data: { version: 1, confirmed_by: "browser-test-operator" } },
    )
  ).json();
  const record = confirmed.data;
  const read = {
    ...result.steps[0].envelope,
    request_id: `browser-read-${Date.now()}`,
    task_id: body.task_id,
    contract_ref: record.ref,
  };
  await page.request.post("/api/v1/tool-calls/evaluate", {
    data: { envelope: read, permissions: {} },
  });
  expect(
    (
      await page.request.post(`/api/v1/tool-calls/${read.request_id}/execute`)
    ).ok(),
  ).toBeTruthy();
  const attack = {
    ...read,
    request_id: `browser-write-${Date.now()}`,
    tool: "write_file",
    action: "write_file",
    effect_class: "WRITE",
    resource: target,
    canonical_args: { path: target, content: "advertisement" },
  };
  expect(
    (
      await page.request.post("/api/v1/tool-calls/evaluate", {
        data: { envelope: attack, permissions: {} },
      })
    ).ok(),
  ).toBeTruthy();
  await page.goto(`/core?task_id=${encodeURIComponent(body.task_id)}`);
  await page.getByRole("button", { name: "生成恢复提案", exact: true }).click();
  const panel = page.getByRole("region", { name: "有界报告恢复" });
  await expect(panel).toContainText("PROPOSED");
  await expect(panel).toContainText("新增授权：无");
  await page
    .getByRole("button", { name: "确认原授权并执行恢复", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "意图风险时间线" }),
  ).toContainText("有界报告恢复已完成", { timeout: 15000 });
  await page.screenshot({
    path: resolve(output, "bounded-recovery.png"),
    fullPage: true,
  });
  await page.reload();
  await expect(
    page.getByRole("region", { name: "意图风险时间线" }),
  ).toContainText("有界报告恢复已完成");
});
