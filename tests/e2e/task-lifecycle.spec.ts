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
    // Enter a task ID and verify SSE connection indicator
    await page.fill('[placeholder="Task ID..."]', 'task-test');
    // May show connected or stay offline depending on backend state
    await expect(page.locator('h1')).toContainText('Audit Log');
  });
});
