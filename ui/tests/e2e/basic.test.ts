import { test, expect } from '@playwright/test';

test('basic UI loads and shows title', async ({ page }) => {
  // Assuming the dev server runs on http://localhost:5173
  await page.goto('http://localhost:5173');
  // Wait for the main app element – this is just a placeholder check
  const title = page.locator('text=Альфа‑Бизнес');
  await expect(title).toBeVisible({ timeout: 5000 });
});
