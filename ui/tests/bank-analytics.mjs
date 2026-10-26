// Requires Vite :5175 and an isolated API (default :8000). Creates only synthetic data.
// node ui/tests/bank-analytics.mjs <absolute path to installed playwright>
import { createRequire } from "node:module";
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
const require = createRequire(import.meta.url);
const { chromium } = require(process.argv[2] || "playwright");
const browser = await chromium.launch({ headless: true, channel: "msedge" });
let activePage;
try {
  const context = await browser.newContext({ baseURL: "http://127.0.0.1:5175", viewport: { width: 1440, height: 1000 } });
  const api = process.env.BANK_QA_API || "http://127.0.0.1:8000";
  // Developer materials must work in a fresh browser without a user account.
  assert.equal((await context.request.get(`${api}/api/real/profile`)).status(), 401);
  const page = await context.newPage();
  activePage = page;
  await page.route("**/api/**", async (route) => {
    const response = await route.fetch({ url: `${api}${new URL(route.request().url()).pathname}${new URL(route.request().url()).search}` });
    await route.fulfill({ response });
  });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByRole("button", { name: "Настройки разработки", exact: true }).click();
  await page.getByRole("button", { name: /^Аналитика банка/ }).click();
  await page.getByRole("heading", { name: /Библиотека материалов 9/ }).waitFor();
  await page.getByRole("button", { name: /Ключевая ставка, 11 сентября 2026/ }).click();
  await page.getByRole("heading", { name: "Проверенные оценки банка" }).waitFor();
  await page.getByLabel("Поиск по содержанию").fill("золото");
  await page.getByRole("button", { name: "Найти", exact: true }).click();
  await page.getByText(/Найдено фрагментов: [1-9]/).waitFor();
  await page.getByRole("button", { name: "Сбросить поиск", exact: true }).click();
  assert.equal(await page.getByRole("link", { name: /PDF/i }).count(), 0);
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
  mkdirSync("ui/test-results", { recursive: true });
  await page.screenshot({ path: "ui/test-results/bank-analytics.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
  await page.screenshot({ path: "ui/test-results/bank-analytics-mobile.png", fullPage: true });
  await page.getByRole("button", { name: "Добавить материал", exact: true }).click();
  await page.getByLabel("Название материала", { exact: true }).fill("Инфляция, тестовая неделя");
  await page.getByLabel("Дата публикации", { exact: true }).fill("2026-09-02");
  await page.getByLabel("Период", { exact: true }).fill("2026-08-25/2026-08-31");
  const text = "Инфляция замедляется на фоне сезонного снижения цен на продовольствие. По оценке банка, снижение инфляции уменьшает давление на закупочные расходы компаний.";
  await page.getByLabel(/^Текст аналитики/).fill(text);
  if (!(await page.getByLabel("Примечание", { exact: true }).isVisible())) await page.getByText("Тема и примечание", { exact: false }).click();
  await page.getByLabel(/^Тема материала/).selectOption("inflation");
  await page.getByLabel("Примечание", { exact: true }).fill("Оценки относятся к указанному периоду.");
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
  await page.screenshot({ path: "ui/test-results/bank-analytics-form-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({ path: "ui/test-results/bank-analytics-form.png", fullPage: true });
  // A failed write keeps the draft so the user can retry.
  await page.route("**/api/bank/analytics", async (route) => {
    if (route.request().method() === "POST") await route.fulfill({ status: 503, json: { detail: "Тестовая ошибка сохранения" } });
    else await route.fallback();
  });
  await page.getByRole("button", { name: "Сохранить материал", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "Тестовая ошибка сохранения" }).waitFor();
  assert.equal(await page.getByLabel(/^Текст аналитики/).inputValue(), text);
  await page.unroute("**/api/bank/analytics");
  const created = page.waitForResponse((response) => response.url().endsWith("/api/bank/analytics") && response.request().method() === "POST");
  await page.getByRole("button", { name: "Сохранить материал", exact: true }).click();
  assert.equal((await created).status(), 201);
  await page.getByRole("heading", { name: "Инфляция, тестовая неделя", exact: true }).waitFor();
  await page.getByText("Добавлено вручную", { exact: true }).waitFor();
  assert.equal(await page.locator(".bank-reader .bank-passage").textContent(), text);
  await page.reload();
  await page.getByRole("button", { name: "Настройки разработки", exact: true }).click();
  await page.getByRole("button", { name: /^Аналитика банка/ }).click();
  await page.getByRole("heading", { name: /Библиотека материалов 10/ }).waitFor();
  await page.getByLabel("Поиск по содержанию").fill("закупочные расходы");
  await page.getByRole("button", { name: "Найти", exact: true }).click();
  await page.locator(".bank-search-results summary").filter({ hasText: "Инфляция, тестовая неделя" }).waitFor();
  await page.getByRole("button", { name: "Сбросить поиск", exact: true }).click();
  await page.getByRole("button", { name: /Инфляция, тестовая неделя/ }).click();
  await page.getByRole("heading", { name: "Текст материала", exact: true }).waitFor();
  await page.screenshot({ path: "ui/test-results/bank-analytics-created.png", fullPage: true });
  await page.getByRole("button", { name: "Редактировать", exact: true }).click();
  assert.equal(await page.getByLabel("Название материала", { exact: true }).inputValue(), "Инфляция, тестовая неделя");
  assert.equal(await page.getByLabel(/^Текст аналитики/).inputValue(), text);
  assert.equal(await page.getByLabel(/^Оценки для применения/).count(), 0);
  await page.getByLabel("Название материала", { exact: true }).fill("Инфляция, исправленная оценка");
  const updatedText = "Инфляция снижается медленнее ожиданий банка. Закупочные расходы компаний остаются под давлением высоких цен на сырьё и перевозки.";
  await page.getByLabel(/^Текст аналитики/).fill(updatedText);
  await page.setViewportSize({ width: 390, height: 844 });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
  await page.screenshot({ path: "ui/test-results/bank-analytics-edit-mobile.png", fullPage: true });
  await page.getByRole("button", { name: "Сохранить изменения", exact: true }).click();
  await page.getByRole("heading", { name: "Инфляция, исправленная оценка", exact: true }).waitFor();
  assert.equal(await page.locator(".bank-reader .bank-passage").textContent(), updatedText);
  await page.getByText("Изменено вручную", { exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: /Инфляция, тестовая неделя/ }).count(), 0);
  await page.screenshot({ path: "ui/test-results/bank-analytics-updated-mobile.png", fullPage: true });
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("button", { name: "Удалить", exact: true }).click();
  await page.getByRole("heading", { name: "Инфляция, исправленная оценка", exact: true }).waitFor();
  await page.route("**/api/bank/analytics/*?expected_revision=*", async (route) => {
    await route.fulfill({ status: 503, json: { detail: "Тестовая ошибка удаления" } });
  });
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Удалить", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "Тестовая ошибка удаления" }).waitFor();
  assert.equal(await page.getByRole("heading", { name: "Инфляция, исправленная оценка", exact: true }).count(), 1);
  await page.unroute("**/api/bank/analytics/*?expected_revision=*");
  const removed = page.waitForResponse((response) => response.request().method() === "DELETE");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Удалить", exact: true }).click();
  assert.equal((await removed).status(), 204);
  await page.getByRole("heading", { name: /Библиотека материалов 9/ }).waitFor();
  assert.equal(await page.getByRole("button", { name: /Инфляция, исправленная оценка/ }).count(), 0);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: /Инфляция, 25–31 августа/ }).click();
  await page.getByRole("heading", { name: "Проверенные оценки банка", exact: true }).waitFor();
  await page.getByRole("button", { name: "Редактировать", exact: true }).click();
  assert((await page.getByLabel(/^Оценки для применения/).inputValue()).length > 40);
  if (!(await page.getByLabel("Примечание", { exact: true }).isVisible())) await page.getByText("Тема и примечание", { exact: false }).click();
  await page.getByLabel("Примечание", { exact: true }).fill("Уточнено в редакторе библиотеки.");
  const sourceRefreshed = page.waitForResponse((response) => response.request().method() === "GET" && response.url().endsWith("/api/bank/analytics/inflation"));
  await page.getByRole("button", { name: "Сохранить изменения", exact: true }).click();
  await sourceRefreshed;
  await page.locator(".bank-reader[aria-busy=false]").getByText("Уточнено в редакторе библиотеки.", { exact: true }).waitFor();
  await page.screenshot({ path: "ui/test-results/bank-analytics-edited-source.png", fullPage: true });
  await page.getByRole("button", { name: "Добавить материал", exact: true }).click();
  await page.getByLabel("Название материала", { exact: true }).fill("Несохранённый черновик");
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("button", { name: /^Модели/ }).click();
  assert.equal(await page.getByLabel("Название материала", { exact: true }).inputValue(), "Несохранённый черновик");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Отмена", exact: true }).click();
  await page.getByRole("button", { name: /^Модели/ }).click();
  assert.equal(await page.locator(".dev-route-row select").count(), 4);
  await page.getByText(/«Персональная подборка» также проверяет соответствие/).waitFor();
  const currentCsrf = (await (await context.request.get(`${api}/api/auth/csrf`)).json()).csrf_token;
  const library = await (await context.request.get(`${api}/api/bank/analytics`)).json();
  for (const item of library.items) {
    const response = await context.request.delete(`${api}/api/bank/analytics/${item.id}`, { headers: { "X-CSRF-Token": currentCsrf } });
    assert.equal(response.status(), 204);
  }
  await page.getByRole("button", { name: /^Аналитика банка/ }).click();
  await page.getByText("Библиотека пока пуста. Добавьте первый материал.").waitFor();
  assert.equal(await page.locator(".bank-reader").getAttribute("aria-busy"), "false");
  assert.equal(await page.getByRole("button", { name: "Удалить", exact: true }).count(), 0);
  assert.deepEqual(errors, []);
  console.log("Bank analytics browser checks passed (desktop and mobile).");
} catch (error) {
  if (activePage) {
    await activePage.screenshot({ path: "ui/test-results/bank-analytics-failure.png", fullPage: true });
    console.log(await activePage.locator(".bank-reader label").evaluateAll((labels) => labels.map((label) => label.textContent.slice(0, 100))));
    console.log(await activePage.locator(".bank-reader details").evaluateAll((items) => items.map((item) => ({ open: item.open, title: item.querySelector("summary")?.textContent }))));
  }
  throw error;
} finally {
  await browser.close();
}
