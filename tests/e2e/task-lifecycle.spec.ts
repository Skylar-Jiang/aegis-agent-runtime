import { test, expect } from '@playwright/test';

test.describe('Task lifecycle', () => {
  test('home page loads and shows task creation form', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('h1')).toContainText('Tasks');
    await expect(page.getByPlaceholder('Enter task objective...')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Create Task' })).toBeVisible();
  });

  test('navigation works between pages', async ({ page }) => {
    await page.goto('/');
    await page.click('a[href="/approvals"]');
    await expect(page.locator('h1')).toContainText('Approvals');
    await page.click('a[href="/audit"]');
    await expect(page.locator('h1')).toContainText('Audit Log');
    await page.click('a[href="/"]');
    await expect(page.locator('h1')).toContainText('Tasks');
  });

  test('create task shows task detail with events', async ({ page }) => {
    await page.goto('/');
    await page.fill('[placeholder="Enter task objective..."]', 'e2e test task');
    await page.click('button:has-text("Create Task")');

    // Task ID should appear
    await expect(page.locator('text=task-')).toBeVisible({ timeout: 5000 });
    // Status badge should show
    await expect(page.locator('text=ACTIVE')).toBeVisible({ timeout: 5000 });
  });

  test('approval page allows grant/deny with error handling', async ({ page }) => {
    await page.goto('/approvals');
    await page.fill('[placeholder="Approval ID..."]', 'nonexistent-id');
    await page.click('button:has-text("Grant")');
    // Should show error for unknown approval
    await expect(page.locator('text=Error')).toBeVisible({ timeout: 5000 });
  });

  test('audit page shows live connection status', async ({ page }) => {
    await page.goto('/audit');
    await expect(page.locator('text=Offline')).toBeVisible();
    await page.fill('[placeholder="Task ID..."]', 'task-test');
    await expect(page.locator('h1')).toContainText('Audit Log');
  });

  test('experiments page loads and shows test cases', async ({ page }) => {
    await page.goto('/experiments');
    await expect(page.locator('h1')).toContainText('Experiments');
    await expect(page.locator('text=Test Cases')).toBeVisible();
    await expect(page.locator('text=Experiment Results')).toBeVisible();
  });

  test('experiments page shows run instructions when empty', async ({ page }) => {
    await page.goto('/experiments');
    await expect(page.locator('text=No results yet')).toBeVisible();
    await expect(page.locator('text=run_experiment.py')).toBeVisible();
  });
});
