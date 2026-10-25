// Run against a Vite server; uses mocked API data, never a real account/model.
// node tests/chat-workspace.mjs <absolute path to installed playwright>
import { createRequire } from "node:module";
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
const require = createRequire(import.meta.url);
const { chromium } = require(process.argv[2] || "playwright");
const browser = await chromium.launch({ headless: true, channel: process.env.CHAT_QA_BROWSER || "msedge" });
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
const errors = [];
const outputDir = resolve(process.env.CHAT_QA_OUTPUT_DIR || "test-results");
mkdirSync(outputDir, { recursive: true });
page.on("pageerror", (error) => errors.push(error.message));
const date = "2026-10-25T10:00:00Z";
const sessions = [
  { id: "chat-one", title: "Условия кредитования", focused_news_id: null, created_at: date, updated_at: date, messages: [{ role: "user", text: "У нас кредит с плавающей ставкой", citations: [], created_at: date }] },
  { id: "chat-two", title: "Поставки сырья", focused_news_id: "news-one", created_at: date, updated_at: date, messages: [{ role: "assistant", text: "Обсудим условия поставок.", citations: [], created_at: date }] },
];
let releaseAnswer;
let answerStarted;
const started = new Promise((resolve) => { answerStarted = resolve; });
const answerGate = new Promise((resolve) => { releaseAnswer = resolve; });
let deleteFailures = [405, 503];
await page.route("**/api/**", async (route) => {
  const url = new URL(route.request().url());
  const path = url.pathname;
  const method = route.request().method();
  let data;
  if (path === "/api/auth/csrf") data = { csrf_token: "qa-csrf" };
  else if (path === "/api/data/refresh") data = { status: "idle" };
  else if (path === "/api/auth/me") data = { authenticated: true, user: { id: "qa-owner", login: "qa-owner" } };
  else if (path === "/api/real/profile") data = { profile: { company_name: "Северные перевозки", status: "complete", profile_version: 1 } };
  else if (path === "/api/real/profile/suggestions") data = { suggestions: [] };
  else if (path === "/api/real/feed") data = { items: [], pending_count: 0, job: null, personalization_available: true };
  else if (path === "/api/real/markets") data = { snapshot: { items: [] }, latest: null };
  else if (path === "/api/real/chat/sessions" && method === "GET") data = { sessions: sessions.map(({ messages, ...row }) => ({ ...row, message_count: messages.length, preview: messages.at(-1)?.text })), has_more: false };
  else if (path === "/api/real/chat/sessions" && method === "POST") {
    data = { id: `chat-${sessions.length + 1}`, title: "Общий чат по бизнесу", focused_news_id: null, created_at: date, updated_at: date, messages: [] };
    sessions.unshift(data);
  } else {
    const id = path.split("/")[5];
    const session = sessions.find((row) => row.id === id);
    if (!session) return route.fulfill({ status: 404, json: { detail: "Диалог не найден" } });
    if (method === "DELETE") {
      if (deleteFailures.length) {
        const status = deleteFailures.shift();
        return route.fulfill({ status, json: { detail: status === 405 ? "Method Not Allowed" : "Удаление временно недоступно" } });
      }
      sessions.splice(sessions.indexOf(session), 1);
      return route.fulfill({ status: 204 });
    }
    if (method === "PATCH") { session.title = route.request().postDataJSON().title; data = session; }
    else if (path.endsWith("/messages")) {
      const body = route.request().postDataJSON();
      const user = { role: "user", text: body.question, request_id: body.request_id, request_status: "pending", citations: [], created_at: date };
      session.messages.push(user); answerStarted(); await answerGate;
      user.request_status = "completed";
      data = { role: "assistant", text: "Учтём вашу долю импорта 35% при оценке поставок.", citations: [], created_at: date };
      session.messages.push(data);
    } else data = session;
  }
  return route.fulfill({ json: data });
});

try {
  await page.goto(process.env.CHAT_QA_URL || "http://127.0.0.1:5175");
  await page.getByRole("button", { name: /Что важно для вашей компании/ }).click();
  assert.equal(await page.getByRole("button", { name: "Спросить о бизнесе", exact: true }).count(), 0);
  await page.getByRole("button", { name: "Мои чаты", exact: true }).click();
  await page.getByRole("heading", { name: "Условия кредитования", exact: true }).waitFor();
  const input = page.getByRole("textbox", { name: "Ваш вопрос" });
  await input.fill("Черновик про кредит");
  await page.getByRole("button", { name: /По новости.*Поставки сырья/ }).click();
  await page.getByRole("heading", { name: "Поставки сырья", exact: true }).waitFor();
  assert.equal(await input.inputValue(), "");
  await page.getByRole("button", { name: /О бизнесе.*Условия кредитования/ }).click();
  assert.equal(await input.inputValue(), "Черновик про кредит");
  await input.fill("Импортируем 35% сырья");
  await page.getByRole("button", { name: "Отправить", exact: true }).click();
  await started;
  await page.getByRole("button", { name: /По новости.*Поставки сырья/ }).click();
  releaseAnswer();
  await page.getByRole("heading", { name: "Поставки сырья", exact: true }).waitFor();
  assert.equal(await page.locator(".chat-conversation").getByText(/Учтём вашу долю/).count(), 0);
  await page.getByRole("button", { name: /О бизнесе.*Условия кредитования/ }).click();
  await page.locator(".chat-conversation").getByText(/Учтём вашу долю/).waitFor();
  await page.getByRole("button", { name: "Изменить название" }).click();
  await page.getByRole("textbox", { name: "Название диалога" }).fill("Кредит и импорт");
  await page.getByRole("button", { name: "Сохранить", exact: true }).click();
  await page.getByRole("heading", { name: "Кредит и импорт", exact: true }).waitFor();
  await page.reload();
  await page.getByRole("button", { name: /Что важно для вашей компании/ }).click();
  await page.getByRole("heading", { name: "Кредит и импорт", exact: true }).waitFor();
  await page.screenshot({ path: resolve(outputDir, "chat-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: resolve(outputDir, "chat-mobile.png"), fullPage: true });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "Mobile page overflows horizontally");
  await page.getByRole("button", { name: "Новый чат", exact: true }).click();
  await page.getByRole("heading", { name: "Общий чат по бизнесу", exact: true }).waitFor();
  await page.getByRole("button", { name: "Удалить чат", exact: true }).click();
  await page.getByRole("button", { name: "Отмена", exact: true }).click();
  await page.getByRole("heading", { name: "Общий чат по бизнесу", exact: true }).waitFor();
  assert.equal(sessions.length, 3);
  await page.getByRole("button", { name: "Удалить чат", exact: true }).click();
  await page.getByRole("button", { name: "Удалить навсегда", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "Сервер ещё работает на предыдущей версии" }).waitFor();
  assert.equal(sessions.length, 3);
  await page.getByRole("button", { name: "Удалить навсегда", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "Удаление временно недоступно" }).waitFor();
  assert.equal(sessions.length, 3);
  await page.getByRole("button", { name: "Удалить навсегда", exact: true }).click();
  await page.getByRole("heading", { name: "Кредит и импорт", exact: true }).waitFor();
  assert.equal(sessions.length, 2);
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem("business-chat-view:qa-owner")).id), "chat-one");
  await input.fill("Удаляемый черновик");
  await page.getByRole("button", { name: "Удалить чат", exact: true }).click();
  await page.getByRole("button", { name: "Удалить навсегда", exact: true }).click();
  await page.getByRole("heading", { name: "Поставки сырья", exact: true }).waitFor();
  assert.equal(await page.evaluate(() => localStorage.getItem("business-chat-draft:qa-owner:chat-one")), null);
  await page.getByRole("button", { name: "Удалить чат", exact: true }).click();
  await page.getByRole("button", { name: "Удалить навсегда", exact: true }).click();
  await page.getByRole("heading", { name: "Обсудим ваш бизнес", exact: true }).waitFor();
  await page.reload();
  await page.getByRole("button", { name: /Что важно для вашей компании/ }).click();
  await page.getByRole("heading", { name: "Обсудим ваш бизнес", exact: true }).waitFor();
  await page.getByRole("button", { name: "Новый чат", exact: true }).click();
  await page.getByRole("heading", { name: "Общий чат по бизнесу", exact: true }).waitFor();
  assert.deepEqual(errors, []);
  console.log("PASS: chat switching, drafts, in-flight isolation, rename, reload restoration, mobile layout, new chat, delete/cancel/error/last chat");
} finally { releaseAnswer(); await browser.close(); }
