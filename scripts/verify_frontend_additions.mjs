// Verify Core task recovery and recorded experiment provenance against live HTTP.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(path.join(root, "frontend", "package.json"));
const { chromium, expect } = require("@playwright/test");
const origin = process.env.AEGIS_BROWSER_URL || "http://127.0.0.1:5173";
const output = path.join(
  root,
  ".runtime",
  "review-fixes",
  `frontend-additions-${new Date().toISOString().replaceAll(/[:.]/g, "-")}`,
);
await mkdir(output, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  channel: process.env.AEGIS_BROWSER_CHANNEL || "msedge",
});
const page = await browser.newPage({
  viewport: { width: 1440, height: 1000 },
  locale: "en-US",
});
const errors = [];
page.on("pageerror", (error) => errors.push(String(error)));
try {
  const health = await (
    await page.request.get(`${origin}/api/v1/health`)
  ).json();
  assert.equal(health.data.crypto_mode, "sm2");
  await page.goto(`${origin}/core`);
  await expect(page.getByText("Core connected")).toBeVisible();
  await page
    .getByLabel("Task objective")
    .fill("Check Core task recovery after refresh");
  await page.getByRole("button", { name: "Create task", exact: true }).click();
  const taskId = (
    await page.locator(".workflow-steps code").textContent()
  ).trim();
  assert.match(taskId, /^task-/);
  await expect(page).toHaveURL(new RegExp(`/core\\?task_id=${taskId}$`));
  await page.reload();
  await expect(page.locator(".workflow-steps code")).toHaveText(taskId);
  await expect(
    page.getByRole("button", { name: "Confirm contract" }),
  ).toBeVisible();
  await page
    .getByLabel("Task objective")
    .fill("Second task for explicit reopen");
  await page.getByRole("button", { name: "Create new task" }).click();
  await expect
    .poll(async () =>
      (await page.locator(".workflow-steps code").textContent()).trim(),
    )
    .not.toBe(taskId);
  const secondTaskId = (
    await page.locator(".workflow-steps code").textContent()
  ).trim();
  await page.getByLabel("Reopen task ID").fill(taskId);
  await page.getByRole("button", { name: "Reopen task" }).click();
  await expect(page.locator(".workflow-steps code")).toHaveText(taskId);
  await expect(page).toHaveURL(new RegExp(`/core\\?task_id=${taskId}$`));
  await page.screenshot({
    path: path.join(output, "core-resumed.png"),
    fullPage: true,
  });

  await page.goto(`${origin}/experiments`);
  await expect(
    page.getByRole("heading", { name: "Core cryptography · recorded runs" }),
  ).toBeVisible();
  const response = await page.request.get(`${origin}/api/v1/experiments/core`);
  assert.equal(response.status(), 200);
  const evidence = (await response.json()).data;
  assert.equal(evidence.recorded, true);
  assert.equal(evidence.runs.length, 5);
  for (const run of evidence.runs) {
    assert.match(run.sha256, /^[a-f0-9]{64}$/);
    await expect(page.getByText(run.source, { exact: false })).toBeVisible();
  }
  await expect(
    page.getByText("Historical measurements from recorded evidence files.", {
      exact: false,
    }),
  ).toBeVisible();
  await page.screenshot({
    path: path.join(output, "core-recorded-experiments.png"),
    fullPage: true,
  });
  assert.deepEqual(errors, []);
  const result = {
    taskId,
    secondTaskId,
    recordedRuns: evidence.runs.map((run) => run.id),
    screenshots: ["core-resumed.png", "core-recorded-experiments.png"],
    errors,
  };
  await writeFile(
    path.join(output, "result.json"),
    JSON.stringify(result, null, 2),
  );
  console.log(JSON.stringify({ output, ...result }));
} catch (error) {
  await page.screenshot({
    path: path.join(output, "failure.png"),
    fullPage: true,
  });
  throw error;
} finally {
  await browser.close();
}
