import { test, expect } from '@playwright/test';

test.describe('Task lifecycle', () => {
  test('workbench loads and shows the task composer', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByText('What should the runtime do?')).toBeVisible();
    await expect(page.getByPlaceholder('Describe a task for the runtime…')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Send task' })).toBeVisible();
  });

  test('navigation works between pages', async ({ page }) => {
    await page.goto('/');
    await page.click('a[href="/approvals"]');
    await expect(page.getByRole('heading', { name: 'Approvals' })).toBeVisible();
    await page.click('a[href="/audit"]');
    await expect(page.getByRole('heading', { name: 'Audit timeline' })).toBeVisible();
    await page.click('a[href="/"]');
    await expect(page.getByText('What should the runtime do?')).toBeVisible();
  });

  test('approval page loads the global pending-approval workspace without a manual ID field', async ({ page }) => {
    await page.goto('/approvals');
    await expect(page.getByPlaceholder('Approver identity...')).toBeVisible();
    await expect(page.getByPlaceholder('Approval ID...')).toHaveCount(0);
  });

  test('audit page shows live connection status', async ({ page }) => {
    await page.goto('/audit');
    await expect(page.getByText('Offline')).toBeVisible();
    await page.fill('[placeholder="Task ID..."]', 'task-test');
    await expect(page.getByRole('heading', { name: 'Audit timeline' })).toBeVisible();
  });

  test('experiments page loads and shows test cases', async ({ page }) => {
    await page.goto('/experiments');
    await expect(page.getByRole('heading', { name: 'Experiment dashboard' })).toBeVisible();
  });

  test('experiments page exposes the formal dashboards', async ({ page }) => {
    await page.goto('/experiments');
    await expect(page.getByRole('heading', { name: 'Safety formal dashboard' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Graph formal dashboard' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Rollback formal dashboard' })).toBeVisible();
  });
});
