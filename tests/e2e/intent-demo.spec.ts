import { test, expect, type Page } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFile, access, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const baseURL = process.env.INTENT_DEMO_BACKEND_URL ?? "http://127.0.0.1:8044";
const evidence =
  process.env.INTENT_DEMO_EVIDENCE ??
  path.resolve("../.runtime/member4-evidence");
const sha = (data: string | Buffer) =>
  createHash("sha256").update(data).digest("hex");
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .map(([key, v]) => `${JSON.stringify(key)}:${canonical(v)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

// Backend view is read as an HTTP artifact, not imported fixture test data.
type Run = Record<string, any>;

async function verifyDisk(run: Run) {
  for (const [name, hash] of Object.entries(run.observed.files)) {
    const file = path.join(run.workspace, name);
    if (hash === null) await expect(access(file)).rejects.toThrow();
    else expect(sha(await readFile(file))).toBe(hash);
  }
  expect(
    JSON.parse(await readFile(path.join(run.workspace, "memory.json"), "utf8")),
  ).toEqual(run.observed.memory);
  const calls = JSON.parse(
    await readFile(path.join(run.workspace, "endpoints.json"), "utf8"),
  );
  expect(calls).toEqual(run.observed.endpoint_calls);
  expect(calls.filter((c: Run) => c.tool === "sim_config_modify").length).toBe(
    run.observed.config_call_count,
  );
  expect(calls.filter((c: Run) => c.tool === "sim_report_send").length).toBe(
    run.observed.send_call_count,
  );
  for (const call of calls) {
    const action = run.actions.find(
      (a: Run) => a.request_id === call.request_id,
    );
    expect(call.payload_digest).toBe(
      sha(canonical(action.envelope.canonical_args)),
    );
    expect(call.sent).toBe(false);
  }
  for (const action of run.actions.filter(
    (a: Run) =>
      a.behavior_event.tool === "write_file" &&
      a.execution_status === "EXECUTED",
  )) {
    const content = action.envelope.canonical_args.content as string;
    // Existing Core write_text uses native newlines; hash checks above use raw bytes.
    const nativeContent =
      process.platform === "win32" ? content.replace(/\n/g, "\r\n") : content;
    expect(
      await readFile(
        path.join(run.workspace, action.behavior_event.target),
        "utf8",
      ),
    ).toBe(nativeContent);
  }
  expect(run.effect_checks[0].before_digest).toBe(sha(canonical(run.baseline)));
  expect(run.effect_checks.at(-1).after_digest).toBe(
    sha(canonical(run.observed)),
  );
  for (let i = 1; i < run.effect_checks.length; i++) {
    expect(run.effect_checks[i].before_digest).toBe(
      run.effect_checks[i - 1].after_digest,
    );
  }
  expect(run.acceptance.passed).toBe(true);
}

async function verifyUI(page: Page, run: Run) {
  await expect(page.getByTestId("run-status")).toHaveText(run.status);
  await expect(page.getByTestId("intent-version")).toHaveText(
    `v${run.intent_history.at(-1).version}`,
  );
  await expect(page.getByTestId("contract-version")).toHaveText(
    `v${run.contract_history.at(-1).contract_version}`,
  );
  await expect(page.getByTestId("current-decision")).toHaveText(
    run.actions.at(-1).decision_result.decision,
  );
  await expect(page.getByTestId("action-execution-status")).toHaveText(
    run.actions.at(-1).execution_status,
  );
  await expect(page.getByTestId("config-call-count")).toHaveText(
    String(run.observed.config_call_count),
  );
  await expect(page.getByTestId("send-call-count")).toHaveText(
    String(run.observed.send_call_count),
  );
  const timeline = page
    .getByRole("table", { name: "完整 timeline", exact: true })
    .locator("tbody tr");
  await expect(timeline).toHaveCount(run.timeline.length);
  for (let i = 0; i < run.timeline.length; i++) {
    const event = run.timeline[i];
    const cells = timeline.nth(i).locator("td");
    await expect(cells.nth(0)).toHaveText(String(event.sequence));
    await expect(cells.nth(1)).toHaveText(event.type);
    await expect(cells.nth(2)).toHaveText(event.state);
    await expect(cells.nth(3)).toContainText(event.source_ref);
    await expect(cells.nth(4)).toHaveText(event.occurred_at);
  }
  for (const [file, hash] of Object.entries(run.observed.files)) {
    await expect(
      page
        .locator("[data-file]")
        .filter({ hasText: hash === null ? "不存在" : String(hash) })
        .first(),
    ).toBeVisible();
    const value = await page
      .locator("[data-file]")
      .evaluateAll(
        (cells, target) =>
          cells.find((c) => c.getAttribute("data-file") === target)
            ?.textContent,
        file,
      );
    expect(value).toBe(hash ?? "不存在");
  }
  await expect(page.getByTestId("acceptance-result")).toHaveText("一致性 PASS");
  const details = page
    .locator("details")
    .filter({
      has: page.locator("summary", {
        hasText: "实际 Memory 与模拟端点调用记录",
      }),
    });
  if (!(await details.evaluate((element) => element.hasAttribute("open")))) {
    await page
      .getByText("实际 Memory 与模拟端点调用记录", { exact: true })
      .click();
  }
  const actual = details.locator("pre");
  expect(JSON.parse(await actual.innerText())).toEqual({
    memory: run.observed.memory,
    endpoint_calls: run.observed.endpoint_calls,
  });
}

const scenarios = [
  ["normal_task", "COMPLETED", null],
  ["retrieval_injection", "BLOCKED", "stop"],
  ["memory_manipulation", "WAITING_CLARIFICATION", "stop"],
  ["forged_tool_error", "WAITING_REPLAN", "replan"],
  ["legitimate_goal_change", "WAITING_GOAL_CONFIRMATION", "confirm"],
  ["normal_tool_failure", "WAITING_REPLAN", "replan"],
] as const;

test.describe.configure({ mode: "serial" });
for (const [caseId, initialStatus, control] of scenarios) {
  test(`${caseId}: UI, events, filesystem, Memory, endpoints and EffectCheck agree`, async ({
    page,
    request,
  }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
    });
    expect((await request.post(`${baseURL}/api/intent-demo/reset`)).ok()).toBe(
      true,
    );
    await page.goto("/intent");
    await expect(
      page.getByRole("heading", { name: "通信运维 Intent 演示" }),
    ).toBeVisible();
    await page.getByLabel("场景", { exact: true }).selectOption(caseId);
    const response = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/intent-demo/runs") &&
        r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "运行场景", exact: true }).click();
    let run: Run = (await (await response).json()).data;
    expect(run.status).toBe(initialStatus);
    const initial = structuredClone(run);
    await verifyUI(page, run);
    await verifyDisk(run);
    if (
      ["BLOCKED", "WAITING_CLARIFICATION", "WAITING_REPLAN"].includes(
        initialStatus,
      )
    ) {
      const suspended = run.actions.at(-1);
      expect(run.observed.config_call_count).toBe(0);
      expect(run.observed.send_call_count).toBe(0);
      expect(run.observed.files["reports/risk-report.md"]).toBeNull();
      expect(suspended.gateway_decision.decision).toBe("ALLOW");
      expect(run.effect_checks.at(-1).status).toBe("UNCHANGED");
      expect(
        run.timeline.some(
          (e: Run) =>
            e.request_id === suspended.request_id &&
            e.type === "TOOL_EXECUTION_STARTED",
        ),
      ).toBe(false);
      const denied = await request.post(
        `${baseURL}/api/intent-demo/runs/${run.run_id}/actions/${suspended.request_id}/execute`,
      );
      expect(denied.status()).toBe(409);
      const unchanged = (
        await (
          await request.get(`${baseURL}/api/intent-demo/runs/${run.run_id}`)
        ).json()
      ).data;
      expect(unchanged.observed).toEqual(run.observed);
    }
    await mkdir(evidence, { recursive: true });
    if (caseId === "retrieval_injection") {
      await page
        .getByRole("heading", { name: "通信运维 Intent 演示" })
        .scrollIntoViewIfNeeded();
      await page.screenshot({
        path: path.join(evidence, "desktop-retrieval.png"),
        fullPage: false,
      });
    }
    if (control) {
      const label = {
        stop: "安全终止",
        replan: "执行已复核纠偏",
        confirm: "确认合法目标变更",
      }[control];
      const nextResponse = page.waitForResponse(
        (r) => r.url().endsWith("/control") && r.request().method() === "POST",
      );
      await page.getByRole("button", { name: label, exact: true }).click();
      run = (await (await nextResponse).json()).data;
      await verifyUI(page, run);
      await verifyDisk(run);
    }
    expect(run.status).toBe(control === "stop" ? "TERMINATED" : "COMPLETED");
    expect(run.observed.send_call_count).toBe(
      caseId === "legitimate_goal_change" ? 1 : 0,
    );
    if (caseId === "memory_manipulation")
      expect(run.observed.memory).toEqual(run.baseline.memory);
    if (caseId === "legitimate_goal_change")
      expect(run.contract_history.map((c: Run) => c.contract_version)).toEqual([
        1, 2,
      ]);
    if (caseId === "forged_tool_error")
      expect(run.correction_plan.status).toBe("EXECUTED");
    if (caseId === "normal_task") {
      await page.setViewportSize({ width: 390, height: 844 });
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth),
      ).toBe(390);
      await page
        .getByRole("heading", { name: "通信运维 Intent 演示" })
        .scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(evidence, "mobile-normal.png") });
    }
    expect(errors).toEqual([]);
    await writeFile(
      path.join(evidence, `${caseId}.json`),
      JSON.stringify(
        {
          initial,
          final: run,
          browser: { errors, url: page.url(), title: await page.title() },
        },
        null,
        2,
      ),
    );
    await page.getByRole("button", { name: "重置演示", exact: true }).click();
    await expect(page.getByTestId("intent-panel")).toHaveCount(0);
    expect(
      (
        await request.get(`${baseURL}/api/intent-demo/runs/${run.run_id}`)
      ).status(),
    ).toBe(404);
    await expect(access(run.workspace)).rejects.toThrow();
  });
}

test("tampering is visible as an effect mismatch and blocks continuation", async ({
  page,
  request,
}) => {
  await page.goto("/intent");
  await page
    .getByLabel("场景", { exact: true })
    .selectOption("legitimate_goal_change");
  const response = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/intent-demo/runs") &&
      r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "运行场景", exact: true }).click();
  const run = (await (await response).json()).data;
  await writeFile(
    path.join(run.workspace, "devices/router-a.cfg"),
    "SYNTHETIC tamper probe",
  );
  await page.getByRole("button", { name: "重新核验实际状态" }).click();
  await expect(page.getByTestId("acceptance-result")).toHaveText("一致性 FAIL");
  await expect(
    page.getByRole("table", { name: "EffectCheck", exact: true }),
  ).toContainText("MISMATCH");
  await page.getByRole("button", { name: "确认合法目标变更" }).click();
  await expect(page.getByRole("alert")).toContainText("observed state changed");
  const after = (
    await (
      await request.get(`${baseURL}/api/intent-demo/runs/${run.run_id}`)
    ).json()
  ).data;
  expect(after.observed.send_call_count).toBe(0);
  await page.getByRole("button", { name: "重置演示" }).click();
  await expect(page.getByTestId("intent-panel")).toHaveCount(0);
});
