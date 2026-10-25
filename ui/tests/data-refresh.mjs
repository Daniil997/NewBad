// node tests/data-refresh.mjs <absolute path to installed playwright>
import { createRequire } from "node:module";
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
const require = createRequire(import.meta.url);
const { chromium } = require(process.argv[2] || "playwright");
const browser = await chromium.launch({ headless: true, channel: process.env.CHAT_QA_BROWSER || "msedge" });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
let state = { status: "idle" };
let posts = 0;
let feeds = 0;
let markets = 0;
let failCsrf = false;
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
await page.addInitScript(() => {
  localStorage.setItem("newbad-app-mode", "demo");
  sessionStorage.setItem("newbad-client", "old-company");
});
await page.route("**/api/**", async (route) => {
  const path = new URL(route.request().url()).pathname;
  let data;
  if (path === "/api/data/refresh") {
    if (route.request().method() === "POST") { posts++; state = { status: "running" }; }
    data = state;
  } else if (path === "/api/auth/csrf") {
    if (failCsrf) return route.fulfill({ status: 503, json: { detail: "offline" } });
    data = { csrf_token: "qa-csrf" };
  } else if (path === "/api/auth/me") data = { authenticated: true, user: { id: "qa-owner", login: "qa-owner" } };
  else if (path === "/api/real/profile") data = { profile: { company_name: "Компания", status: "complete", profile_version: 1 } };
  else if (path === "/api/real/profile/suggestions") data = { suggestions: [] };
  else if (path === "/api/real/profile/options") data = { industry_tags: [], focus_topics: [] };
  else if (path === "/api/real/profile/sources") data = { sources: [] };
  else if (path === "/api/real/feed") { feeds++; data = { items: [], pending_count: 0, job: null, personalization_available: true }; }
  else if (path === "/api/real/markets") { markets++; data = { snapshot: { items: [] }, latest: null }; }
  else if (path === "/api/real/chat/sessions") data = { sessions: [] };
  else throw new Error(`Unexpected request: ${path}`);
  await route.fulfill({ status: 200, json: data });
});
try {
  await page.goto(process.env.CHAT_QA_URL || "http://127.0.0.1:5175");
  await page.getByRole("button", { name: "Обновить данные", exact: true }).waitFor();
  mkdirSync("test-results", { recursive: true });
  await page.screenshot({ path: "test-results/data-refresh-home.png", fullPage: true });
  assert.equal(await page.getByText("Тестовые данные", { exact: true }).count(), 0);
  assert.equal(await page.evaluate(() => localStorage.getItem("newbad-app-mode")), null);
  await page.getByRole("button", { name: "Обновить данные", exact: true }).click();
  await page.getByRole("button", { name: "Обновляем данные…", exact: true }).waitFor();
  assert.equal(posts, 1);
  assert.equal(await page.getByRole("button", { name: "Обновляем данные…", exact: true }).isDisabled(), true);
  await page.getByRole("button", { name: /Что важно для вашей компании/ }).click();
  await page.getByText("qa-owner", { exact: true }).waitFor();
  await page.waitForFunction(() => document.querySelector(".news-page"));
  const beforeFeed = feeds;
  const beforeMarkets = markets;
  state = { status: "partial", message: "Обновлено частично. Часть источников недоступна." };
  await page.getByText(state.message, { exact: true }).waitFor();
  await page.waitForTimeout(250);
  assert.ok(feeds > beforeFeed);
  assert.ok(markets > beforeMarkets);
  failCsrf = true;
  await page.getByRole("button", { name: "Обновить данные", exact: true }).click();
  await page.getByText("Не удалось подготовить обновление", { exact: true }).waitFor();
  assert.equal(posts, 1);
  failCsrf = false;
  await Promise.all([
    page.waitForResponse((response) => new URL(response.url()).pathname === "/api/data/refresh" && response.request().method() === "POST"),
    page.getByRole("button", { name: "Обновить данные", exact: true }).click(),
  ]);
  await page.getByRole("button", { name: "Обновляем данные…", exact: true }).waitFor();
  assert.equal(posts, 2);
  await page.getByRole("button", { name: "Альфа-Бизнес", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "test-results/data-refresh-mobile.png", fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
  assert.deepEqual(errors, []);
  console.log("Data refresh UI checks passed");
} finally { await browser.close(); }
