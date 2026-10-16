/**
 * Дымовой тест интерфейса панели: крутим настоящий app.js в jsdom против живого сервера.
 *
 *   python3 tests/panel_demo.py &                      # демо-панель на 127.0.0.1:8098
 *   node tests/panel_jsdom.mjs                           # нужен jsdom (npm i jsdom)
 *
 * Проверяем, что каждый раздел рендерится без исключений, табы/поиск/модалки живые,
 * а POST-действия доходят до бэкенда. JS_PATH/BASE/PASSWORD — переменные окружения.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
/* jsdom ищем рядом с репозиторием, а если его нет — по JSDOM_PATH (npm i jsdom куда угодно) */
let JSDOM, VirtualConsole;
try {
  ({ JSDOM, VirtualConsole } = await import("jsdom"));
} catch {
  const p = process.env.JSDOM_PATH || "/tmp/jstest/node_modules/jsdom/lib/api.js";
  ({ JSDOM, VirtualConsole } = await import(p));
}

const HERE = dirname(fileURLToPath(import.meta.url));
const BASE = process.env.BASE || "http://127.0.0.1:8098/priora/panel/";
const PASSWORD = process.env.PASSWORD || "Djdaqdj157";
const TABS = ["overview", "users", "econ", "play", "promo", "market", "clans", "chats", "pay",
              "audit", "db", "console"];

let okN = 0, failN = 0;
const check = (name, cond, extra = "") => {
  if (cond) { okN++; console.log("  ✅ " + name); }
  else { failN++; console.log("  ❌ " + name + (extra ? "  " + String(extra).slice(0, 300) : "")); }
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ── 1. логин по HTTP, чтобы вытащить куку и CSRF-токен ── */
const jar = new Map();
async function req(path, { method = "GET", body, headers = {} } = {}) {
  const h = { ...headers };
  if (jar.size) h.Cookie = [...jar].map(([k, v]) => `${k}=${v}`).join("; ");
  if (body !== undefined) { h["Content-Type"] = "application/json"; }
  const r = await fetch(BASE + path, { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body) });
  for (const sc of (r.headers.getSetCookie ? r.headers.getSetCookie() : [])) {
    const [kv] = sc.split(";");
    const i = kv.indexOf("=");
    jar.set(kv.slice(0, i).trim(), kv.slice(i + 1).trim());
  }
  const text = await r.text();
  let json = null;
  try { json = JSON.parse(text); } catch { /* html/csv */ }
  return { status: r.status, text, json };
}

const login = await req("login", { method: "POST", body: { password: PASSWORD } });
if (!login.json?.ok) {
  console.error("не удалось войти в панель:", login.status, login.text.slice(0, 200));
  process.exit(2);
}
const CSRF = login.json.csrf;
check("логин отдан, кука и CSRF на руках", login.status === 200 && jar.size > 0 && !!CSRF);

/* ── 2. собираем страницу без <script src> и исполняем app.js руками ── */
const page = await req("");
const html = page.text.replace(/<script[^>]*src=[^>]*><\/script>/g, "");
const appJs = readFileSync(join(HERE, "..", "simbot", "web", "panel", "app.js"), "utf8");

const errors = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => errors.push("jsdom: " + (e.detail?.message || e.message)));
vc.on("error", (...a) => errors.push("console.error: " + a.join(" ")));

const dom = new JSDOM(html, { url: BASE, runScripts: "outside-only", pretendToBeVisual: true, virtualConsole: vc });
const win = dom.window;
const BASE_PATH = new URL(BASE).pathname;
win.fetch = async (url, opts = {}) => {
  let u = String(url);
  try { u = new URL(u, BASE).pathname; } catch { /* относительный путь */ }
  let path = u.startsWith(BASE_PATH) ? u.slice(BASE_PATH.length) : u.replace(/^\//, "");
  path = path.replace(/^priora\/panel\/?/, "");
  const method = (opts.method || "GET").toUpperCase();
  let body;
  if (opts.body) { try { body = JSON.parse(opts.body); } catch { body = undefined; } }
  const r = await req(path, { method, body, headers: opts.headers || {} });
  return { ok: r.status < 400, status: r.status, json: async () => r.json, text: async () => r.text };
};
win.sessionStorage.setItem("pnl-csrf", CSRF);
win.addEventListener("error", (e) => errors.push("window.onerror: " + (e.error?.message || e.message)));
win.addEventListener("unhandledrejection", (e) => errors.push("rejection: " + (e.reason?.message || e.reason)));

/* приложение ждёт document.readyState — подсказываем, что DOM готов, и стартуем */
win.eval(appJs);
await sleep(400);

/* ── 3. обходим вкладки ── */
check("приложение показало shell без гейта", !win.document.getElementById("app").classList.contains("hidden"),
      win.document.getElementById("app").className);
check("гейт скрыт после автологина", win.document.getElementById("gate").classList.contains("hidden"),
      win.document.getElementById("gate").className);
const pill = win.document.getElementById("bot-name");
check("в шапке видно бота и версию", /@priora_demo_bot/.test(pill.textContent) && /v\d/.test(pill.textContent),
      pill.textContent);
const kpis = win.document.getElementById("top-kpis");
check("KPI в шапке заполнены", (kpis.textContent || "").length > 10, kpis.textContent.slice(0, 80));
const tabs = [...win.document.querySelectorAll("#tabs button")].map((b) => b.textContent.trim());
check("вкладки нарисованы", tabs.length >= TABS.length, tabs.join(","));

const view = () => win.document.getElementById("view");
for (const tab of TABS) {
  errors.length = 0;
  win.location.hash = "#/" + tab;
  win.dispatchEvent(new win.HashChangeEvent("hashchange"));
  await sleep(450);
  const txt = (view().textContent || "").replace(/\s+/g, " ");
  check(`раздел «${tab}» отрисован`, txt.length > 40 && !/ошибка|не загрузилось/i.test(txt.slice(0, 60)),
        txt.slice(0, 120));
  if (errors.length) check(`раздел «${tab}» без исключений`, false, errors.join(" | "));
}

/* ── 4. профиль игрока, поиск, сортировка, действия ── */
const users = await req("api/users?limit=5");
const list = users.json.users || users.json.rows || [];
const first = list[0] || {};
const uid = first.id;
errors.length = 0;
win.location.hash = "#/user/" + uid;
win.dispatchEvent(new win.HashChangeEvent("hashchange"));
await sleep(700);
const prof = (view().textContent || "").replace(/\s+/g, " ");
check("профиль игрока открыт", prof.includes("№") || prof.length > 200, prof.slice(0, 100));
check("в профиле есть статистика", /баланс/i.test(prof), prof.slice(0, 120));
check("профиль без исключений", errors.length === 0, errors.join(" | "));

const srch = await req("api/search?q=" + encodeURIComponent(first.username || "a"));
check("список игроков непустой", list.length > 0 && !!users.json.total, users.json.total)
check("поиск по нику работает", srch.status === 200 && Array.isArray(srch.json.results),
      JSON.stringify(srch.json).slice(0, 150));

/* кнопка «json» в профиле → модалка */
const jsonBtn = [...view().querySelectorAll("button")].find((b) => b.textContent.trim() === "json");
if (jsonBtn) {
  jsonBtn.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  await sleep(200);
  const modal = win.document.getElementById("modal");
  check("кнопка «json» открыла модалку", !modal.classList.contains("hidden") && modal.textContent.length > 40,
        modal.className);
  const close = [...modal.querySelectorAll("button")].find((b) => /закрыть/.test(b.textContent));
  close?.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  await sleep(120);
  check("модалка закрывается", win.document.getElementById("modal").classList.contains("hidden"));
}

/* живое действие из карточки игрока: «начислить» должно дойти до БД */
const back = await req("api/user/" + uid);
const wasBal = Number(back.json.user.balance);
const amountEl = [...view().querySelectorAll("input")].find((i) => /^\d+$/.test(i.value || ""));
if (amountEl) amountEl.value = "1000";
const giveBtn = [...view().querySelectorAll("button")].find((b) => /начислить/.test(b.textContent));
check("в карточке игрока есть кнопка «начислить»", !!giveBtn && !!amountEl, giveBtn?.textContent);
if (giveBtn) {
  errors.length = 0;
  giveBtn.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  await sleep(900);
  const after = await req("api/user/" + uid);
  check("действие из UI дошло до базы", Number(after.json.user.balance) === wasBal + 1000,
        `${wasBal} → ${after.json.user.balance}`);
  check("после действия нет ошибок страницы", errors.length === 0, errors.join(" | "));
  await req("api/action", { method: "POST", headers: { "X-Panel-CSRF": CSRF },
                            body: { op: "balance", uid: uid, mode: "set", amount: wasBal } });
}

/* ── 5. POST-действие прямо из DOM (консоль) ── */
errors.length = 0;
win.location.hash = "#/console";
win.dispatchEvent(new win.HashChangeEvent("hashchange"));
await sleep(400);
const cmd = win.document.getElementById("cmd");
check("поле консоли на месте", !!cmd);
if (cmd) {
  cmd.value = "настройка min_bet 700";
  const runBtn = [...win.document.querySelectorAll("#view button")].find((b) => /исполнить/.test(b.textContent));
  runBtn?.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  await sleep(700);
  check("команда консоли отработала из UI", /min_bet|настрой/i.test(view().textContent), view().textContent.slice(0, 160));
  const after = await req("api/settings");
  check("настройка действительно изменилась", Number(after.json.values.min_bet) === 700, after.json.values.min_bet);
  await req("api/action", { method: "POST", body: { op: "setting", key: "min_bet", value: 500 },
                            headers: { "X-Panel-CSRF": CSRF } });
}

console.log(`\n  ${errors.length ? "ошибки страницы: " + errors.join(" | ") : "ошибок страницы нет"}`);
console.log("==============================================");
console.log(`  интерфейс панели: OK ${okN} · провалов ${failN}`);
console.log("==============================================");
process.exit(failN ? 1 : 0);
