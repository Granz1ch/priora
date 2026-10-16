/* ═══ Панель управления СИМ-Рулеткой — SPA без зависимостей и сборки ═══
   Файл собран из трёх частей; синтаксис проверяется `node --check`. */
"use strict";

/* ─────────── адреса ─────────── */
const SELF = document.currentScript || (function () {
  const all = document.getElementsByTagName("script");
  return all[all.length - 1];
}());
const BASE = (function () {
  try {
    const u = new URL(SELF.src, location.href);
    return u.pathname.replace(/\/app\.js$/, "").replace(/\/$/, "");
  } catch (e) {
    return "/priora/panel";
  }
}());
const API = BASE + "/api/";

const S = {
  csrf: sessionStorage.getItem("pnl-csrf") || "",
  live: true,
  timer: 0,
  cache: {},
  session: null,
  users: { q: "", filter: "all", sort: "balance", desc: 1, page: 1, limit: 50 },
  db: { table: "users", q: "", user: "", page: 1, limit: 50, order: "", desc: 1 },
  games: { kind: "", status: "finished", user: "", limit: 60 },
  audit: { limit: 100, action: "" },
  hist: [],
};

const RANKS = { 0: "Игрок", 1: "Модер", 2: "Гл. модер", 3: "Админ", 4: "Гл. админ", 5: "Владелец" };
const TIERS = ["common", "uncommon", "rare", "epic", "legendary", "mythic"];
const SECTIONS = {
  crash: "краш", mines: "мины", slots: "слоты", dice: "кубик", upgrader: "ап", duel: "дуэль",
  spin: "крутить", clans: "кланы", quiz: "викторина", transfer: "передача", deposit: "пополнение",
  bonus: "бонус", promo: "промо", market: "рынок",
};
const KIND_ICON = { crash: "🚀", mines: "💣", slots: "🎰", dice: "🎲", upgrader: "🔼", upgrade_num: "📱", duel: "⚔️" };
const TABS = [
  ["overview", "📊 Обзор"], ["users", "👥 Игроки"], ["games", "🎲 Лента игр"], ["econ", "⚙️ Экономика"],
  ["play", "🕹 Игра"], ["promo", "🎟 Промо"], ["market", "🏷 Рынок"], ["clans", "🏰 Кланы"],
  ["chats", "💬 Чаты"], ["pay", "⭐ Платежи"], ["audit", "🧾 Аудит"], ["db", "🗄 База"], ["console", "⌨️ Консоль"],
];
const AUTO = { overview: 1, games: 1, users: 1, play: 1 };

/* ─────────── формат ─────────── */
const ESCM = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ESCM[c]);
const nf = (n) => Math.round(Number(n) || 0).toLocaleString("ru-RU");
const money = (n) => (Number(n) < 0 ? "−" : "") + nf(Math.abs(Number(n) || 0)) + " ₽";
const signed = (n) => (Number(n) > 0 ? "+" : Number(n) < 0 ? "−" : "") + nf(Math.abs(Number(n) || 0)) + " ₽";
const pct = (x) => (Number(x) || 0).toFixed(2).replace(/\.?0+$/, "") + "%";
const sum = (arr, k) => arr.reduce((a, x) => a + (Number(x[k]) || 0), 0);
const kindLabel = (k) => (KIND_ICON[k] || "🎮") + " " + (k === "upgrade_num" ? "ап номера" : k);
const short = (n) => {
  n = Number(n) || 0;
  const a = Math.abs(n);
  const s = n < 0 ? "−" : "";
  if (a >= 1e9) return s + (a / 1e9).toFixed(1) + "B";
  if (a >= 1e6) return s + (a / 1e6).toFixed(1) + "M";
  if (a >= 1e4) return s + Math.round(a / 1e3) + "K";
  return s + nf(a);
};
const dt = (ts) => (ts ? new Date(Number(ts) * 1000).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
const dstr = (ts) => (ts ? new Date(Number(ts) * 1000).toLocaleDateString("ru-RU") : "—");
const ago = (ts) => {
  const d = Math.max(0, Math.floor(Date.now() / 1000 - Number(ts || 0)));
  if (!ts) return "—";
  if (d < 60) return "только что";
  if (d < 3600) return Math.floor(d / 60) + " мин назад";
  if (d < 86400) return Math.floor(d / 3600) + " ч назад";
  if (d < 2592000) return Math.floor(d / 86400) + " дн назад";
  return dstr(ts);
};
const dur = (s) => {
  s = Math.max(0, Math.round(Number(s) || 0));
  if (s >= 86400) return Math.floor(s / 86400) + " д " + Math.floor((s % 86400) / 3600) + " ч";
  if (s >= 3600) return Math.floor(s / 3600) + " ч " + Math.floor((s % 3600) / 60) + " м";
  if (s >= 60) return Math.floor(s / 60) + " мин";
  return s + " с";
};
const numCell = (v) => h("span", { class: Number(v) > 0 ? "pos" : Number(v) < 0 ? "neg" : "dim" }, signed(v));

/* ─────────── DOM ─────────── */
function h(tag, attrs) {
  const el = document.createElement(tag);
  if (typeof attrs === "string") el.className = attrs;
  else if (attrs) {
    for (const k in attrs) {
      const v = attrs[k];
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "html") el.innerHTML = sanitize(v);
      else if (k === "value") el.value = v;
      else if (k.indexOf("on") === 0 && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, v);
    }
  }
  for (let i = 2; i < arguments.length; i++) addKids(el, arguments[i]);
  return el;
}
function addKids(el, kids) {
  if (kids == null || kids === false) return;
  if (Array.isArray(kids)) { kids.forEach((k) => addKids(el, k)); return; }
  el.appendChild(typeof kids === "object" ? kids : document.createTextNode(String(kids)));
}
/** Разметку из ответов бота пропускаем через белый список тегов. */
function sanitize(html) {
  const box = document.createElement("div");
  box.innerHTML = String(html || "");
  const allow = { B: 1, STRONG: 1, I: 1, EM: 1, U: 1, S: 1, CODE: 1, PRE: 1, BR: 1, A: 1, DIV: 1, SPAN: 1, UL: 1, OL: 1, LI: 1, HR: 1 };
  (function walk(node) {
    Array.from(node.childNodes).forEach((n) => {
      if (n.nodeType === 3) return;
      if (n.nodeType !== 1) { n.remove(); return; }
      if (!allow[n.tagName]) { n.replaceWith(document.createTextNode(n.textContent || "")); return; }
      Array.from(n.attributes).forEach((a) => {
        if (a.name === "href" && /^(https?:|tg:)/i.test(a.value)) return;
        n.removeAttribute(a.name);
      });
      if (n.tagName === "A") { n.target = "_blank"; n.rel = "noopener noreferrer"; }
      walk(n);
    });
  }(box));
  return box.innerHTML;
}
const rich = (htmlStr) => h("div", { class: "rich", html: htmlStr });
const kv = (pairs) => h("dl", "kv", pairs.map((p) => [h("dt", null, p[0]), h("dd", null, String(p[1] == null || p[1] === "" ? "—" : p[1]))]));
const badge = (txt, cls) => h("span", "badge " + (cls || ""), txt);
const userLink = (id, name) => h("a", { href: "#/user/" + id, title: "карточка игрока" }, name || ("#" + id));
const tgLink = (name) => h("a", { href: "https://t.me/" + name, target: "_blank", rel: "noopener" }, "@" + name);
const block = (title, kids) => h("div", "card", h("h4", null, title), kids);
function card(title) {
  const el = h("div", "card");
  if (title) el.appendChild(h("h3", null, title));
  for (let i = 1; i < arguments.length; i++) addKids(el, arguments[i]);
  return el;
}
const stat = (label, value, sub, cls) => {
  const el = h("div", "stat" + (cls ? " " + cls : ""));
  el.appendChild(h("b", null, value));
  el.appendChild(h("span", null, label));
  if (sub) el.appendChild(h("i", { class: "dim" }, sub));
  return el;
};
const input = (props) => h("input", Object.assign({ class: "ipt" }, props));
const ta = (props) => h("textarea", Object.assign({ class: "ipt" }, props));
function sel(opts, cur, onchange, cls) {
  const el = h("select", { class: (cls || "ipt") + "", onchange: (e) => onchange(e.target.value) });
  el.append(...opts.map((o) => h("option", { value: o[0], selected: String(o[0]) === String(cur) }, o[1])));
  return el;
}
function chips(pairs, cur, onPick) {
  return h("div", "chips", pairs.map((p) => h("button", {
    class: "chip" + (String(cur) === String(p[0]) ? " on" : ""),
    onclick: () => onPick(p[0]),
  }, p[1])));
}
function rowEl() {
  const el = h("div", "row");
  for (let i = 0; i < arguments.length; i++) addKids(el, arguments[i]);
  return el;
}
function inlineEl() {
  const el = h("div", "inline");
  for (let i = 0; i < arguments.length; i++) addKids(el, arguments[i]);
  return el;
}
function listEl(items, empty) {
  if (!items || !items.length) return h("div", "dim small", empty || "пусто");
  return h("div", "list", items.map((it) => h("div", "li", h("div", "l", it[0]), h("div", { class: "r dim" }, it[1] == null ? "" : it[1]))));
}

/* ─────────── таблицы/пагинация/графики ─────────── */
function table(spec) {
  const cols = spec.cols;
  const rows = spec.rows || [];
  const th = cols.map((c) => h("th", {
    class: (c.cls || "") + (c.sort && spec.onSort ? " sortable" : ""),
    onclick: c.sort && spec.onSort ? () => spec.onSort(c.sort) : null,
  }, c.label + (spec.sortKey === c.sort ? (spec.desc ? " ↓" : " ↑") : "")));
  const body = rows.length
    ? rows.map((r, i) => h("tr", {
        class: (spec.rowCls ? spec.rowCls(r) : "") + (spec.onRow ? " click" : ""),
        onclick: spec.onRow ? (e) => { if (!e.target.closest("button,a")) spec.onRow(r, i, e); } : null,
      }, cols.map((c) => h("td", c.cls ? { class: c.cls } : null, cellOf(c, r, i)))))
    : [h("tr", null, h("td", { colspan: cols.length, class: "dim center" }, spec.empty || "пусто"))];
  return h("div", "tbl-wrap", h("table", "tbl" + (spec.compact ? " compact" : ""),
    h("thead", null, h("tr", null, th)), h("tbody", null, body)));
}
function cellOf(c, r, i) {
  if (c.get) return c.get(r, i);
  const v = r[c.k];
  return v == null || v === "" ? "—" : v;
}
function pager(meta, onGo) {
  if (!meta) return null;
  if (meta.pages <= 1) return h("div", "pager dim tiny", nf(meta.total) + " строк");
  const p = meta.page, n = meta.pages;
  const btn = (label, page, dis) => h("button", { class: "btn sm", disabled: dis, onclick: () => onGo(page) }, label);
  const near = [];
  for (let i = Math.max(1, p - 2); i <= Math.min(n, p + 2); i++) near.push(i);
  return h("div", "pager", btn("«", 1, p <= 1), btn("‹", p - 1, p <= 1),
    near.map((i) => h("button", { class: "btn sm" + (i === p ? " primary" : ""), onclick: () => onGo(i) }, i)),
    btn("›", p + 1, p >= n), btn("»", n, p >= n), h("span", "dim tiny", nf(meta.total) + " всего"));
}
function bars(items, key, color, title) {
  const max = Math.max(1, ...items.map((x) => Math.abs(Number(x[key]) || 0)));
  return h("div", "chart", items.map((x) => {
    const v = Math.abs(Number(x[key]) || 0);
    const tip = h("div", "tip", (x.day || "") + " · " + (title || key) + ": " + nf(v));
    const bar = h("div", { class: "b", style: "height:" + Math.max(1, v / max * 80) + "px;background:" + color });
    return h("div", "col", tip, bar);
  }));
}
function meter(parts) {
  const total = parts.reduce((a, p) => a + p[1], 0) || 1;
  return h("div", "splitbar", parts.map((p) => h("i", { style: "width:" + (p[1] / total * 100).toFixed(2) + "%;background:" + p[2], title: p[0] + ": " + nf(p[1]) })));
}

/* ─────────── сеть ─────────── */
async function get(path, params) {
  const qs = params ? "?" + new URLSearchParams(clean(params)).toString() : "";
  const r = await fetch(API + path + qs, { credentials: "same-origin", headers: { Accept: "application/json" } });
  const data = await r.json().catch(() => ({}));
  if (r.status === 401) { showGate(); throw new Error("сессия истекла — войди заново"); }
  if (!r.ok) throw new Error(data.error || "HTTP " + r.status);
  return data;
}
async function post(path, body) {
  const r = await fetch(API + path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Panel-CSRF": S.csrf },
    body: JSON.stringify(body || {}),
  });
  const data = await r.json().catch(() => ({}));
  if (r.status === 401) { showGate(); throw new Error("сессия истекла — войди заново"); }
  if (!r.ok || data.ok === false) throw new Error(data.error || "HTTP " + r.status);
  return data;
}
function clean(o) {
  const out = {};
  for (const k in o) if (o[k] !== "" && o[k] != null) out[k] = o[k];
  return out;
}
const api = (op, payload) => post("action", Object.assign({ op: op }, payload || {}));
async function act(op, payload) {
  const r = await api(op, payload);
  const res = r.result || {};
  const msg = res.msg || res.note || "готово ✓";
  toast(typeof msg === "string" ? msg : "готово ✓", "good");
  return r;
}
let toastT = 0;
function toast(msg, kind) {
  const el = document.getElementById("toast");
  el.textContent = "";
  addKids(el, msg);
  el.className = "toast show " + (kind || "");
  clearTimeout(toastT);
  toastT = setTimeout(() => { el.className = "toast " + (kind || ""); }, kind === "bad" ? 6500 : 2400);
}
const bad = (e) => toast((e && e.message) ? e.message : String(e), "bad");
function confirmIt(text, fn) {
  if (window.confirm(text)) fn();
}
function openModal(node, title) {
  const m = document.getElementById("modal");
  m.innerHTML = "";
  const closeBtn = h("button", { class: "btn sm ghost", onclick: () => m.classList.add("hidden") }, "закрыть");
  m.appendChild(h("div", "card", h("div", { class: "row between" }, h("h3", null, title || ""), closeBtn), node));
  m.classList.remove("hidden");
  m.onclick = (e) => { if (e.target === m) m.classList.add("hidden"); };
}

/* ─────────── раковина приложения ─────────── */
function showGate() {
  document.getElementById("gate").classList.remove("hidden");
  document.getElementById("app").classList.add("hidden");
  const i = document.getElementById("pw");
  if (i) setTimeout(() => i.focus(), 30);
}
async function boot() {
  let s;
  try {
    s = await get("session");
  } catch (e) {
    return;
  }
  if (!s.logged_in) return showGate();
  S.session = s;
  const b = s.bot || {};
  const pill = document.getElementById("bot-name");
  if (pill) pill.textContent = (b.username ? "@" + b.username : "Priora") + " · v" + (s.version || "?")
    + (b.uptime != null ? " · аптайм " + dur(b.uptime * 1000) : "");
  document.getElementById("gate").classList.add("hidden");
  document.getElementById("app").classList.remove("hidden");
  buildTabs();
  if (!location.hash) location.hash = "#/overview";
  await draw();
  clearInterval(S.timer);
  S.timer = setInterval(() => { if (S.live && AUTO[route().v]) draw(true); }, 15000);
}
function buildTabs() {
  const nav = document.getElementById("tabs");
  nav.innerHTML = "";
  addKids(nav, TABS.map((t) => h("button", { "data-v": t[0], onclick: () => { location.hash = "#/" + t[0]; } }, t[1])));
}
function route() {
  const parts = (location.hash || "#/overview").replace(/^#\/?/, "").split("/");
  return { v: parts[0] || "overview", arg: parts.slice(1) };
}
async function draw(silent) {
  const r = route();
  const view = VIEWS[r.v] ? r.v : "overview";
  Array.from(document.querySelectorAll(".tabs button")).forEach((b) => {
    b.classList.toggle("on", b.dataset.v === view || (view === "user" && b.dataset.v === "users"));
  });
  const el = document.getElementById("view");
  if (!silent) {
    el.innerHTML = "";
    el.appendChild(h("div", "loading", "загрузка…"));
  }
  try {
    const node = await VIEWS[view](r.arg);
    if (node) {
      el.innerHTML = "";
      el.appendChild(node);
    }
  } catch (e) {
    if (silent) return bad(e);
    el.innerHTML = "";
    el.appendChild(card("не загрузилось", h("code", "neg", String(e.message || e))));
  }
}
const VIEWS = {};
function renderKpis(ov) {
  const box = document.getElementById("top-kpis");
  if (!ov || !box) return;
  const s = ov.stats;
  const data = [["игроков", nf(s.users), "+" + s.new_today + " за сутки"], ["в обороте", short(s.balance_sum), "сумма балансов"],
    ["GGR", short(ov.ggr), "hold " + pct(ov.hold)], ["игр", nf(s.games), "+" + s.games_today + " за сутки"],
    ["прокруток", nf(s.spins), nf(s.numbers) + " номеров"], ["⭐", nf(s.stars), s.payments + " платежей"]];
  box.innerHTML = "";
  addKids(box, data.map((d) => h("div", { class: "kpi", title: d[2] }, h("b", null, d[1]), h("span", null, d[0]))));
  const b = ov.bot || {};
  const info = document.getElementById("foot-info");
  if (info) {
    info.textContent = "@" + (b.username || "?") + " · v" + b.version + " · аптайм " + dur(b.uptime)
      + " · " + nf(b.updates) + " апдейтов · " + (b.api_base || "") + " · обновлено "
      + new Date().toLocaleTimeString("ru-RU");
  }
}
async function refresh() { await draw(); }

/* ═══════════ ОБЗОР ═══════════ */
VIEWS.overview = async function () {
  const ov = await get("overview");
  S.cache.ov = ov;
  renderKpis(ov);
  const st = ov.stats;
  const fr = ov.series;
  const wrap = h("div", "sec");
  const kpi = [["Игроков", nf(st.users), "+" + ov.day.new + " за сутки · активных " + st.active_today, ""],
    ["Забанено", nf(st.banned), ov.muted + " в муте · " + ov.warned + " с варнами", "acc"],
    ["В обороте", money(st.balance_sum), "сумма балансов игроков", ""],
    ["GGR казино", money(ov.ggr), "отдача " + pct(100 - ov.hold) + " · маржа " + pct(ov.hold), "acc"],
    ["Игр сыграно", nf(st.games), "+" + ov.day.games + " за сутки · идёт " + ov.active_games, ""],
    ["Ставок принято", money(st.total_bet), "за час " + money(ov.hour.bet), ""],
    ["Выплат", money(st.total_payout), "за час " + money(ov.hour.payout), ""],
    ["Прокруток", nf(st.spins), st.numbers + " номеров на " + money(st.numbers_value), ""],
    ["Stars", nf(st.stars) + " ⭐", st.payments + " платежей · сегодня " + ov.day.stars + " ⭐", ""],
    ["Дуэли/обмены", st.open_duels + " / " + ov.open_trades, "лотów на рынке: " + ov.market_lots, ""],
    ["Кланы", nf(ov.clans), "казна " + money(ov.treasury), ""],
    ["Чаты", nf(ov.chats), "в ЧС " + ov.chats_banned + " · викторин " + ov.quizzes, ""]];
  addKids(wrap, h("div", "grid g4", kpi.map((k) => stat(k[0], k[1], k[2], k[3]))));

  const chart = (title, key, color) => {
    const c = h("div", "card");
    c.appendChild(h("h3", null, title));
    c.appendChild(bars(fr, key, color, title));
    c.appendChild(h("div", { class: "row between tiny dim mt" },
      h("span", null, fr.length ? fr[0].day : ""),
      h("span", null, "итого: " + nf(sum(fr, key))),
      h("span", null, fr.length ? fr[fr.length - 1].day : "")));
    return c;
  };
  addKids(wrap, h("div", { class: "grid g3 sec" }, [
    chart("🆕 Новые игроки · 14 дней", "new_users", "var(--blue)"),
    chart("🟢 Активные (DAU)", "active", "var(--green)"),
    chart("💰 Ставки ₽", "bet", "var(--gold)"),
    chart("💸 Выплаты ₽", "payout", "var(--violet)"),
    chart("📈 Профит казино ₽", "ggr", "var(--green)"),
    chart("⭐ Пополнения ⭐", "stars", "var(--blue)"),
  ]));

  const kinds = table({
    compact: true,
    rows: ov.kinds,
    empty: "игр ещё не было",
    cols: [
      { label: "режим", get: (r) => kindLabel(r.kind) },
      { label: "игр", k: "games", cls: "num" },
      { label: "игроков", k: "players", cls: "num" },
      { label: "ставок", cls: "num", get: (r) => money(r.bet) },
      { label: "выплат", cls: "num", get: (r) => money(r.payout) },
      { label: "игрокам", cls: "num", get: (r) => numCell(r.net) },
      { label: "% побед", cls: "num", get: (r) => (r.games ? Math.round(r.wins / r.games * 100) + "%" : "—") },
      { label: "макс. ставка", cls: "num", get: (r) => money(r.max_bet) },
    ],
  });
  const tiersTbl = table({
    compact: true,
    rows: ov.tiers,
    cols: [
      { label: "редкость", get: (r) => h("span", "t-" + r.tier, r.emoji + " " + r.name) },
      { label: "штук", cls: "num", get: (r) => nf(r.count) },
      { label: "доля", cls: "num", get: (r) => (st.numbers ? (r.count / st.numbers * 100).toFixed(1) + "%" : "—") },
      { label: "сумма", cls: "num", get: (r) => money(r.value) },
    ],
  });
  addKids(wrap, h("div", { class: "grid g2 sec" }, [card("Разрез по играм", kinds), card("Номера по редкости", tiersTbl)]));

  const miniUsers = (rows, mode) => table({
    compact: true,
    rows: rows,
    empty: "пусто",
    cols: [
      { label: "id", cls: "dim", get: (r) => r.id },
      { label: "игрок", get: (r) => userLink(r.id, r.first_name) },
      { label: "ник", get: (r) => (r.username ? "@" + r.username : "—") },
      { label: mode === "created" ? "регистр." : "баланс", cls: "num",
        get: (r) => (mode === "created" ? ago(r.created) : money(r.balance)) },
    ],
  });
  const netTbl = (rows, title) => table({
    compact: true,
    rows: rows,
    empty: "нет данных",
    cols: [
      { label: "игрок", get: (r) => userLink(r.id, r.first_name) },
      { label: "поставил", cls: "num", get: (r) => money(r.total_bet) },
      { label: "выиграл", cls: "num", get: (r) => money(r.total_won) },
      { label: "net", cls: "num", get: (r) => numCell(r.net) },
    ],
  });
  const whales = table({
    compact: true,
    rows: ov.whales,
    empty: "крупных ставок не было",
    cols: [
      { label: "когда", get: (r) => dt(r.finished || r.created) },
      { label: "игрок", get: (r) => userLink(r.user_id, r.first_name) },
      { label: "режим", get: (r) => kindLabel(r.kind) },
      { label: "ставка", cls: "num", get: (r) => money(r.bet) },
      { label: "итог", cls: "num", get: (r) => numCell(r.payout - r.bet) },
    ],
  });
  addKids(wrap, h("div", { class: "grid g2 sec" }, [
    card("💰 Топ по балансу", miniUsers(ov.top_balance)),
    card("📈 Больше всех выиграли", netTbl(ov.top_net)),
    card("📉 Больше всех проиграли", netTbl(ov.bottom_net)),
    card("🐋 Крупные ставки (≥100К)", whales),
    card("🆕 Последние игроки", miniUsers(ov.new_users, "created")),
    card("🧾 Журнал администрации", auditTable(ov.audit)),
  ]));

  addKids(wrap, card("⚡ Сейчас в игре (" + ov.active_games + ")", liveList(ov.live)));
  const staff = table({
    compact: true,
    rows: ov.staff,
    empty: "титулов никому не выдавали",
    cols: [
      { label: "id", k: "id", cls: "dim" },
      { label: "ник", get: (r) => userLink(r.id, r.first_name) },
      { label: "титул", get: (r) => badge(r.rank + " · " + (RANKS[r.rank] || "?"), "rank") },
      { label: "баланс", cls: "num", get: (r) => money(r.balance) },
    ],
  });
  addKids(wrap, h("div", { class: "grid g2" }, [card("👑 Администрация", staff), card("🩺 Состояние бота", botCard(ov))]));
  return wrap;
};
function auditTable(rows) {
  return table({
    compact: true,
    rows: rows,
    empty: "журнал пуст",
    cols: [
      { label: "когда", get: (r) => dt(r.created) },
      { label: "кто", get: (r) => (r.admin ? userLink(r.admin_id, r.admin) : "#" + r.admin_id) },
      { label: "действие", get: (r) => badge(r.action, "rank") },
      { label: "детали", get: (r) => h("code", { class: "dim", title: r.details }, String(r.details).slice(0, 80)) },
    ],
  });
}
function liveList(live) {
  if (!live || !live.length) return h("div", "dim small", "никто не играет прямо сейчас");
  return h("div", "list", live.map((g) => {
    const left = h("div", "r", money(g.bet), badge("id " + g.id));
    const btn = h("button", {
      class: "btn sm danger",
      onclick: () => confirmIt("Вернуть ставку и снять игрока из игры?", async () => {
        try {
          await act("game_refund_live", { game: g.id, notify: 1 });
          draw();
        } catch (e) { bad(e); }
      }),
    }, "вернуть ставку");
    const info = [];
    if (g.kind === "crash" && g.state) {
      if (g.state.crash_at != null) info.push(badge("краш был бы ×" + Number(g.state.crash_at).toFixed(2)));
      if (g.state.auto) info.push(badge("авто ×" + Number(g.state.auto).toFixed(2)));
    }
    const who = g.first_name + (g.username ? " @" + g.username : "");
    const leftPart = h("div", "l", kindLabel(g.kind), userLink(g.user_id, who), h("span", "dim tiny", "идёт " + dur(g.age)));
    return h("div", "li", leftPart, h("div", "row", info, left, btn));
  }));
}
function botCard(ov) {
  const b = ov.bot || {};
  const pairs = [
    ["бот", "@" + (b.username || "?")], ["id", b.id], ["версия", b.version], ["аптайм", dur(b.uptime)],
    ["обработано", nf(b.updates) + " обновлений"], ["Bot API", b.api_base], ["база", (b.db_path || "") + " · " + (b.db_size ? nf(b.db_size) + " байт" : "?")],
    ["краш-циклов", b.active_crash], ["фоновых задач", b.bg_tasks], ["python", b.python],
    ["сессия панели", (b.panel_hours || "?") + " ч"], ["публичный URL", b.external],
  ];
  return h("div", null, kv(pairs), h("div", { class: "row mt" },
    h("a", { class: "btn sm ghost", href: "https://t.me/" + (b.username || ""), target: "_blank", rel: "noopener" }, "открыть бота"),
    h("button", { class: "btn sm ghost", onclick: () => openModal(h("pre", "out", JSON.stringify(ov.bot, null, 1)), "bot") }, "json")));
}

/* ═══════════ ИГРОКИ ═══════════ */
VIEWS.users = async function () {
  const f = S.users;
  const data = await get("users", { q: f.q, filter: f.filter, sort: f.sort, desc: f.desc, page: f.page, limit: f.limit });
  const go = (patch) => { Object.assign(f, patch); draw(); };
  const wrap = h("div", "sec");
  const search = input({ class: "ipt search", placeholder: "имя, @username или ID — Enter", value: f.q,
    onkeydown: (e) => { if (e.key === "Enter") { f.q = e.target.value.trim(); f.page = 1; draw(); } } });
  const csv = BASE + "/api/export?" + new URLSearchParams(clean({ q: f.q, filter: f.filter, sort: f.sort, desc: f.desc })).toString();
  const bar = rowEl(search,
    h("button", { class: "btn", onclick: () => { f.q = search.value.trim(); f.page = 1; draw(); } }, "найти"),
    h("button", { class: "btn ghost", onclick: () => { Object.assign(f, { q: "", filter: "all", page: 1 }); draw(); } }, "сброс"),
    h("a", { class: "btn ghost", href: csv }, "⬇ CSV"),
    h("span", { class: "dim small" }, nf(data.total) + " игроков · балансы " + money(data.balance_sum)));
  const filters = [["all", "все"], ["active", "активные 24ч"], ["week", "заходили неделю"], ["new", "новички недели"],
    ["quiet", "тише недели"], ["banned", "забанены"], ["muted", "в муте"], ["staff", "администрация"],
    ["clan", "в клане"], ["rich", "богатые 500К+"], ["zero", "ноль на счету"], ["paid", "платили ⭐"],
    ["plus", "в плюсе"], ["minus", "в минусе"]];
  const box = h("div", "card", bar, h("div", { class: "chips mt" }, filters.map((p) => h("button", {
    class: "chip" + (f.filter === p[0] ? " on" : ""), onclick: () => go({ filter: p[0], page: 1 }),
  }, p[1]))));
  wrap.appendChild(box);

  const cols = [
    { label: "ID", sort: "id", cls: "dim", get: (r) => r.id },
    { label: "игрок", get: (r) => [r.tag ? badge("[" + r.tag + "]", "clan") : null, " ", r.first_name,
        r.username ? h("span", "dim", " @" + r.username) : null] },
    { label: "титул", sort: "rank", get: (r) => (r.rank ? badge(RANKS[r.rank], "rank") : h("span", "dim", "—")) },
    { label: "баланс", sort: "balance", cls: "num", get: (r) => money(r.balance) },
    { label: "коллекция", sort: "collection", cls: "num", get: (r) => (r.nums ? r.nums + " 📱 · " + money(r.coll) : "—") },
    { label: "net", sort: "net", cls: "num", get: (r) => numCell(r.net) },
    { label: "игр", sort: "games", cls: "num", get: (r) => r.games + (r.games ? " (" + Math.round((r.wins || 0) / r.games * 100) + "%)" : "") },
    { label: "прокруток", sort: "spins", cls: "num", k: "spins" },
    { label: "варнов", sort: "warns", cls: "num", get: (r) => (r.warns ? badge(r.warns, "mute") : "—") },
    { label: "статус", get: (r) => [r.banned ? badge("бан", "ban") : null, r.muted_until * 1000 > Date.now() ? badge("мут", "mute") : null] },
    { label: "был", sort: "last_seen", cls: "num dim", get: (r) => ago(r.last_seen) },
    { label: "создан", sort: "created", cls: "num dim", get: (r) => dstr(r.created) },
    { label: "", cls: "acts", get: (r) => [
      h("button", { class: "btn sm ghost", title: "начислить / списать", onclick: () => quickAmount(r.id) }, "₽"),
      h("a", { class: "btn sm", href: "#/user/" + r.id }, "карточка"),
    ] },
  ];
  const tbl = table({
    cols: cols, rows: data.users, sortKey: f.sort, desc: !!f.desc,
    rowCls: (r) => (r.banned ? "dead" : ""),
    onSort: (k) => { if (f.sort === k) f.desc = f.desc ? 0 : 1; else { f.sort = k; f.desc = 1; } draw(); },
    onRow: (r) => { location.hash = "#/user/" + r.id; },
    empty: "никого не нашлось",
  });
  wrap.appendChild(h("div", { class: "mt" }, tbl));
  wrap.appendChild(pager(data, (p) => go({ page: p })));
  return wrap;
};
async function quickAmount(uid) {
  const v = window.prompt("Сколько начислить (минус — списать), ₽", "5000");
  if (v == null || !String(v).trim()) return;
  const n = Number(String(v).replace(/[^\d.\-]/g, "")) || 0;
  try {
    await act("balance", { uid: uid, mode: n < 0 ? "take" : "give", amount: Math.abs(n) });
    draw();
  } catch (e) { bad(e); }
}

/* ═══════════ КАРТОЧКА ИГРОКА ═══════════ */
VIEWS.user = async function (arg) {
  const uid = arg[0];
  if (!uid) { location.hash = "#/users"; return null; }
  const d = await get("user/" + encodeURIComponent(uid));
  const u = d.user;
  S.user = { id: u.id, name: u.first_name };
  const wrap = h("div", "sec");
  wrap.appendChild(headCard(uid, d));
  wrap.appendChild(h("div", { class: "sec mt" }, actionsCard(uid, d)));
  wrap.appendChild(h("div", { class: "grid g2 sec" }, [inventoryCard(uid, d), gamesCard(uid, d)]));
  wrap.appendChild(h("div", { class: "grid g2 sec" }, [ledgerCard(d), bonusCard(d)]));
  wrap.appendChild(h("div", { class: "grid g2 sec" }, [duelsCard(d), tradesCard(d)]));
  wrap.appendChild(h("div", { class: "grid g2 sec" }, [movesCard(d), moneyCard(uid, d)]));
  wrap.appendChild(h("div", { class: "grid g2 sec" }, [rawCard(d), clanCard(d)]));
  return wrap;
};
function headCard(uid, d) {
  const u = d.user;
  const badges = [badge(RANKS[u.rank] + (u.rank ? " (" + u.rank + ")" : ""), "rank")];
  if (u.is_admin) badges.push(badge("legacy is_admin", "rank"));
  if (u.banned) badges.push(badge("🚫 бан: " + (u.ban_reason || "без причины"), "ban"));
  if (u.muted_left) badges.push(badge("🔇 мут ещё " + dur(u.muted_left), "mute"));
  if (d.clan) badges.push(badge("🏰 [" + d.clan.tag + "] " + d.clan.name + " · " + (u.clan_role || "участник"), "clan"));
  if (u.warns) badges.push(badge("⚠️ варнов " + u.warns, "mute"));
  if (u.ref_by) badges.push(badge("приглашён #" + u.ref_by));
  const cells = [["баланс", money(u.balance)], ["net игроку", signed(u.net)], ["поставил", money(u.total_bet)],
    ["выиграл", money(u.total_won)], ["игр", nf(u.games) + " · " + (u.games ? Math.round(u.wins / u.games * 100) : 0) + "% побед"],
    ["рекорд", "+" + money(u.biggest_win)], ["прокруток", nf(u.spins)],
    ["номеров", d.inventory_count + " на " + money(d.inventory_value)], ["⭐ paid", nf(u.stars_paid)],
    ["рефералов", nf(u.refs)], ["варнов", nf(u.warns)], ["серия бонуса", u.daily_streak + " дн"],
    ["с нами", dur(u.hours * 3600)], ["заходил", ago(u.last_seen)]];
  const titleRow = rowEl(h("span", "ptitle", u.first_name), u.username ? tgLink(u.username) : null,
    h("code", "dim", "#" + u.id),
    h("button", { class: "btn sm ghost", onclick: () => history.length > 1 ? history.back() : (location.hash = "#/users") }, "← назад"),
    h("button", { class: "btn sm ghost", onclick: () => openModal(h("pre", "out", JSON.stringify(d, null, 1)), "профиль JSON") }, "json"));
  const head = h("div", null, titleRow, h("div", { class: "row mt" }, badges),
    h("div", "pgrid", cells.map((c) => h("div", "pcell", h("span", null, c[0]), h("b", null, String(c[1]))))));
  return card(null, h("div", "phead", h("div", "avatar", "👤"), head));
}
function actionsCard(uid, d) {
  const amount = input({ class: "ipt ipt-sm", value: "1000" });
  const reason = input({ class: "ipt grow", placeholder: "причина / комментарий (уйдёт в журнал)" });
  const bal = (mode) => api("balance", { uid: uid, mode: mode, amount: amount.value, note: reason.value })
    .then(() => { toast(mode + " ✓", "good"); draw(); }, bad);
  const muteLen = input({ class: "ipt ipt-sm", value: "30м", placeholder: "30м / 2ч / 1д / 0" });
  const dmText = ta({ placeholder: "Личное сообщение игроку (можно HTML: <b>, <code>…)" });
  const note = input({ class: "ipt grow", placeholder: "заметка в журнал" });
  const numId = input({ class: "ipt ipt-sm", placeholder: "#ID или цифры" });
  const gOp = input({ class: "ipt ipt-sm", value: "testo", placeholder: "оператор" });
  const gTier = input({ class: "ipt ipt-sm", value: "", placeholder: "редкость" });
  let pick = String(d.user.rank);
  const rankSel = sel(Object.keys(RANKS).map((k) => [k, k + " · " + RANKS[k]]), pick, (v) => { pick = v; });
  const cards = [
    block("💰 Баланс", [
      inlineEl(amount, reason),
      inlineEl(h("button", { class: "btn ok", onclick: () => bal("give") }, "начислить"),
        h("button", { class: "btn danger", onclick: () => bal("take") }, "списать"),
        h("button", { class: "btn", onclick: () => bal("set") }, "установить"),
        h("button", { class: "btn ghost", onclick: () => bal("reset") }, "обнулить")),
    ]),
    block("🛡 Модерация", [
      inlineEl(h("button", { class: "btn danger", onclick: () => api("ban", { uid: uid, on: 1, reason: reason.value }).then(() => { toast("забанен ✓", "good"); draw(); }, bad) }, "🚫 бан"),
        h("button", { class: "btn ok", onclick: () => api("ban", { uid: uid, on: 0 }).then(() => { toast("разбан ✓", "good"); draw(); }, bad) }, "разбан"),
        h("button", { class: "btn", onclick: () => api("warn", { uid: uid, reason: reason.value }).then(() => { toast("варн ✓", "good"); draw(); }, bad) }, "⚠️ варн")),
      inlineEl(muteLen,
        h("button", { class: "btn", onclick: () => api("mute", { uid: uid, duration: muteLen.value }).then(() => { toast("мут ✓", "good"); draw(); }, bad) }, "🔇 мут"),
        h("button", { class: "btn ghost", onclick: () => api("mute", { uid: uid, seconds: 0 }).then(() => draw(), bad) }, "размут"),
        h("button", { class: "btn ghost", onclick: () => api("warn_remove", { uid: uid, all: 1 }).then(() => { toast("варны сняты ✓", "good"); draw(); }, bad) }, "снять варны")),
    ]),
    block("👑 Титул и статистика", [
      inlineEl(rankSel, h("button", { class: "btn", onclick: () => api("rank", { uid: uid, rank: pick }).then(() => { toast("титул обновлён ✓", "good"); draw(); }, bad) }, "выдать")),
      inlineEl(h("button", { class: "btn ghost", onclick: () => api("rank", { uid: uid, rank: 0 }).then(() => draw(), bad) }, "снять титул"),
        h("button", { class: "btn ghost", onclick: () => confirmIt("Обнулить игровую статистику (игры, ставки, прокрутки)?", () => api("reset_stats", { uid: uid }).then(() => draw(), bad)) }, "обнулить статистику")),
    ]),
    block("📱 Номера", [
      inlineEl(gOp, gTier, h("button", { class: "btn", onclick: () => api("give_number", { uid: uid, operator: gOp.value.trim(), tier: gTier.value.trim() }).then(() => { toast("номер выдан ✓", "good"); draw(); }, bad) }, "выдать номер"),
        h("button", { class: "btn ghost", onclick: () => confirmIt("Изъять ВСЕ номера игрока?", () => api("wipe_inventory", { uid: uid }).then(() => draw(), bad)) }, "изъять все")),
      inlineEl(numId,
        h("button", { class: "btn sm ghost", onclick: () => api("take_number", { number: numId.value }).then(() => { toast("изъят ✓", "good"); draw(); }, bad) }, "изъять по ID"),
        h("button", { class: "btn sm ghost", onclick: () => {
          const to = window.prompt("Кому передать номер " + numId.value + "? (ID игрока)");
          if (to) api("move_number", { number: numId.value, to: to.trim() }).then(() => { toast("передан ✓", "good"); draw(); }, bad);
        } }, "передать")),
    ]),
    block("✉️ Связь и заметки", [
      dmText,
      inlineEl(h("button", { class: "btn", onclick: () => {
        const t = dmText.value.trim();
        if (!t) return toast("пусто", "bad");
        api("dm", { uid: uid, text: t, html: 1 }).then(() => toast("отправлено ✓", "good"), bad);
      } }, "написать в личку"),
        h("button", { class: "btn ghost", onclick: () => {
          const t = note.value.trim();
          if (!t) return toast("пусто", "bad");
          api("note", { uid: uid, text: t }).then(() => { toast("в журнале ✓", "good"); note.value = ""; }, bad);
        } }, "заметить"))
      ,
    ]),
    block("🧨 Жёстко", [
      inlineEl(h("button", { class: "btn danger", onclick: () => confirmIt("Забанить и изъять все номера?", () => hardAction(uid, "banWipe")) }, "бан + изъять"),
        h("button", { class: "btn danger", onclick: () => confirmIt("Полный откат: обнулить баланс и статистику, изъять номера, снять титул и варны?", () => hardAction(uid, "wipe")) }, "полный откат")),
      h("div", { class: "tiny dim mt" }, "Действия пишутся в журнал «аудит», изменения баланса — в леджер игрока."),
    ]),
  ];
  return card("🎛 Управление игроком", h("div", "actions", cards));
}
async function hardAction(uid, mode) {
  try {
    if (mode === "banWipe") {
      await api("ban", { uid: uid, on: 1, reason: "из панели" });
      try { await api("wipe_inventory", { uid: uid }); } catch (e) { /* номеров может не быть */ }
    } else {
      await api("balance", { uid: uid, mode: "reset" });
      await api("reset_stats", { uid: uid });
      try { await api("wipe_inventory", { uid: uid }); } catch (e) { /* ignore */ }
      await api("rank", { uid: uid, rank: 0 });
      await api("warn_remove", { uid: uid, all: 1 });
      await api("mute", { uid: uid, seconds: 0 });
    }
    draw();
  } catch (e) { bad(e); }
}
function inventoryCard(uid, d) {
  const rows = d.inventory || [];
  const byTier = {};
  rows.forEach((n) => { byTier[n.tier] = (byTier[n.tier] || 0) + 1; });
  const chipsRow = h("div", "chips", TIERS.filter((t) => byTier[t]).map((t) => badge(t + " ×" + byTier[t], "t-" + t)));
  const cols = [
    { label: "номер", get: (r) => h("code", null, r.full) },
    { label: "редкость", get: (r) => h("span", "t-" + r.tier, r.tier_emoji + " " + r.tier_name) },
    { label: "оператор", get: (r) => r.op_emoji + " " + r.op_name },
    { label: "цена", cls: "num", get: (r) => money(r.value) },
    { label: "лот", cls: "num", get: (r) => (r.listed ? badge("🏷 " + money(r.listed), "mute") : h("span", "dim", "—")) },
    { label: "id", get: (r) => h("code", "dim", "#" + r.id) },
    { label: "откуда", get: (r) => (r.origin === "spin" ? "крутка" : r.origin) },
    { label: "", cls: "acts", get: (r) => [
      h("button", { class: "btn sm ghost", title: "переоценить", onclick: () => revalue(uid, r) }, "₽"),
      h("button", { class: "btn sm danger", title: "изъять", onclick: () => confirmIt("Изъять " + r.full + "?", () => api("take_number", { number: r.id }).then(() => draw(), bad)) }, "×"),
    ] },
  ];
  const body = rows.length ? table({ compact: true, cols: cols, rows: rows.slice(0, 200), empty: "пусто" })
    : h("div", "dim small", "номеров нет");
  return card("🎒 Инвентарь · " + d.inventory_count + " шт · " + money(d.inventory_value), chipsRow,
    h("div", { class: "mt" }, body),
    rows.length > 200 ? h("div", { class: "dim tiny mt" }, "показаны первые 200 из " + rows.length) : null);
}
function revalue(uid, n) {
  const v = window.prompt("Новая стоимость " + n.full + " (₽)", n.value);
  if (v == null) return;
  api("revalue_number", { number: n.id, value: v }).then(() => { toast("переоценён ✓", "good"); draw(); }, bad);
}
function gamesCard(uid, d) {
  const cols = [
    { label: "когда", get: (r) => dt(r.finished || r.created) },
    { label: "режим", get: (r) => kindLabel(r.kind) },
    { label: "ставка", cls: "num", get: (r) => money(r.bet) },
    { label: "итог", cls: "num", get: (r) => numCell(r.net) },
    { label: "статус", get: (r) => badge(r.status, r.status === "active" ? "mute" : "") },
    { label: "детали", get: (r) => h("span", { class: "dim small", title: r.result }, String(r.result || "").slice(0, 26)) },
    { label: "", cls: "acts", get: (r) => (r.status === "active"
      ? h("button", { class: "btn sm danger", onclick: () => api("game_refund_live", { game: r.id, notify: 1 }).then(() => draw(), bad) }, "вернуть")
      : h("button", { class: "btn sm ghost", title: "вернуть ставку / откатить выигрыш", onclick: () => confirmIt("Изменить исход игры " + r.id + "?", () => api("game_refund", { game: r.id }).then(() => draw(), bad)) }, "рефанд")) },
  ];
  const kinds = h("div", { class: "chips" }, (d.kinds || []).map((k) => badge(kindLabel(k.kind) + " " + money(k.net), k.net > 0 ? "ban" : "ok")));
  return card("🎮 Игры (80) · разрез по режимам", kinds, h("div", { class: "mt" }, table({ compact: true, cols: cols, rows: d.games, empty: "игр нет" })));
}
function ledgerCard(d) {
  const cols = [
    { label: "когда", get: (r) => dt(r.created) },
    { label: "дельта", cls: "num", get: (r) => numCell(r.delta) },
    { label: "после", cls: "num dim", get: (r) => nf(r.balance_after) },
    { label: "причина", get: (r) => h("code", null, r.reason) },
  ];
  return card("🧾 Леджер (80)", table({ compact: true, cols: cols, rows: d.ledger, empty: "пусто" }));
}
function bonusCard(d) {
  const cols = [
    { label: "причина", get: (r) => h("code", null, r.reason) },
    { label: "раз", cls: "num", k: "c" },
    { label: "сумма", cls: "num", get: (r) => numCell(r.s) },
  ];
  return card("💠 Начисления по причинам (бонусы/админ/депозиты/промо)", table({ compact: true, cols: cols, rows: d.ledger_sum, empty: "нет служебных начислений" }));
}
function duelsCard(d) {
  const cols = [
    { label: "id", k: "id" },
    { label: "соперник", get: (r) => (r.opponent ? "#" + r.opponent : "—") },
    { label: "банк", cls: "num", get: (r) => money(r.bet) },
    { label: "статус", get: (r) => badge(r.status, r.status === "playing" ? "mute" : "") },
    { label: "раунды", get: (r) => h("span", "dim small", String(r.rounds || "").slice(0, 40)) },
    { label: "когда", get: (r) => dt(r.created) },
  ];
  return card("⚔️ Дуэли", table({ compact: true, cols: cols, rows: d.duels, empty: "дуэлей не было" }));
}
function tradesCard(d) {
  const cols = [
    { label: "id", k: "id" },
    { label: "отдаёт", k: "give_id" },
    { label: "берёт", k: "take_id" },
    { label: "доплата", cls: "num", get: (r) => money(r.extra) },
    { label: "статус", get: (r) => badge(r.status, r.status === "open" ? "mute" : "") },
    { label: "когда", get: (r) => dt(r.created) },
  ];
  const offers = table({
    compact: true,
    rows: d.offers,
    empty: "нет",
    cols: [{ label: "лот", get: (r) => h("code", null, r.number_id) }, { label: "номер", get: (r) => r.full },
      { label: "цена", cls: "num", get: (r) => money(r.price) }, { label: "когда", get: (r) => dt(r.created) }],
  });
  return card("🔄 Обмены · адресные предложения", table({ compact: true, cols: cols, rows: d.trades, empty: "обменов не было" }),
    h("div", { class: "mt" }, offers));
}
function movesCard(d) {
  const cols = [
    { label: "когда", get: (r) => dt(r.created) },
    { label: "номер", get: (r) => h("code", null, r.full) },
    { label: "действие", get: (r) => badge(r.action, "rank") },
    { label: "от → кому", get: (r) => ["#", r.from_user || "—", " → #", r.to_user || "—"].join("") },
  ];
  return card("📱 Перемещения номеров игрока", table({ compact: true, cols: cols, rows: d.numbers_moved, empty: "пусто" }));
}
function moneyCard(uid, d) {
  const pay = (d.payments || []).concat((d.invoices || []).map((i) => ({ created: i.created, payload: i.payload, stars: i.stars, status: "invoice:" + i.status })));
  const cols = [
    { label: "когда", get: (r) => dt(r.created) },
    { label: "charge / payload", get: (r) => h("code", null, String(r.charge_id || r.payload || "").slice(0, 24)) },
    { label: "⭐", cls: "num", k: "stars" },
    { label: "₽", cls: "num", get: (r) => money(r.rubles || 0) },
    { label: "", get: (r) => (r.status ? badge(r.status) : (r.refunded ? badge("возврат", "ban") : badge("ок", "ok"))) },
    { label: "", cls: "acts", get: (r) => (r.charge_id && !r.refunded ? h("button", { class: "btn sm danger", onclick: () => refund(r.charge_id) }, "рефанд") : null) },
  ];
  const misc = [
    listEl(d.warns.map((w) => [dt(w.created) + " · от #" + w.by, w.reason]), "варнов нет"),
    h("div", { class: "mt" }, listEl(d.promos.map((p) => [dt(p.created), p.code]), "промо не вводил")),
    h("div", { class: "mt" }, listEl(d.chats.map((c) => [c.chat_id + " · " + c.title, c.games + " игр"]), "не играл в чатах")),
    h("div", { class: "mt" }, listEl(d.refs_from.map((r) => [userLink(r.id, r.first_name), dstr(r.created)]), "никого не пригласил")),
  ];
  return card("⭐ Платежи · ⚠️ варны · промо · чаты · рефералы",
    table({ compact: true, cols: cols, rows: pay, empty: "платежей не было" }),
    h("div", { class: "grid g2 mt" }, misc));
}
function clanCard(d) {
  if (!d.clan) return card("🏰 Клан", h("div", "dim small", "игрок без клана"));
  const c = d.clan;
  const head = h("div", "row", badge("[" + c.tag + "]", "clan"), h("b", null, c.name), h("span", "dim small", "казна " + money(c.treasury) + " · открыт: " + (c.open ? "да" : "нет")));
  const members = (d.clan_members || []).map((m) => h("div", "li", h("div", "l", userLink(m.id, m.first_name), badge(m.clan_role || "member")), h("div", "r", money(m.balance))));
  return card("🏰 Клан игрока", head, h("div", { class: "list mt" }, members));
}
function rawCard(d) {
  const u = d.user;
  const pairs = Object.keys(u).map((k) => [k, u[k]]);
  const fsm = h("div", "row", h("span", "dim small", "состояние FSM:"), h("code", null, d.state.state || "—"),
    h("code", "dim", JSON.stringify(d.state.data || {})),
    h("button", { class: "btn sm ghost", onclick: () => api("clear_state", { uid: u.id }).then(() => { toast("сброшено ✓", "good"); draw(); }, bad) }, "сбросить"));
  return card("🧬 Сырая строка players", kv(pairs), h("div", { class: "mt" }, fsm),
    h("pre", { class: "out mt" }, JSON.stringify({ active: d.active, quizzes_won: d.quizzes_won, ref_by: d.ref_by }, null, 1)));
}
async function refund(charge) {
  if (!window.confirm("Вернуть звёзды и списать ₽ с баланса игрока?")) return;
  try {
    const r = await api("payment_refund", { charge: charge });
    toast("возврат " + (r.result && r.result.stars) + " ⭐ ✓", "good");
    draw();
  } catch (e) { bad(e); }
}

/* ═══════════ ЭКОНОМИКА ═══════════ */
VIEWS.econ = async function () {
  const [st, ops, cts, ov] = await Promise.all([get("settings"), get("operators"), get("countries"), get("overview")]);
  const wrap = h("div", "sec");
  const cells = Object.keys(st.values).map((k) => {
    const inp = input({ value: st.values[k] });
    const save = h("button", { class: "btn sm", onclick: () => api("setting", { key: k, value: inp.value }).then(() => toast(k + " сохранено ✓", "good"), bad) }, "ок");
    return h("div", "pcell", h("span", null, k), inlineEl(inp, save));
  });
  wrap.appendChild(card("⚙️ Настройки экономики (то же, что «настройка ключ значение» в чате)",
    h("div", "grid g4", cells)));

  const sections = chips(Object.keys(SECTIONS).map((k) => [k, (ov.sections[k] !== false ? "✅ " : "⛔ ") + SECTIONS[k]]),
    null, (k) => { const on = ov.sections[k] !== false; api("section", { section: k, on: on ? 0 : 1 }).then(() => draw(), bad); });
  const maint = h("button", {
    class: "btn " + (ov.maintenance ? "danger" : "ok"),
    onclick: () => api("maintenance", { on: ov.maintenance ? 0 : 1 }).then(() => draw(), bad),
  }, ov.maintenance ? "🛠 техработы включены — выключить" : "✅ техработы выключены — включить");
  wrap.appendChild(h("div", { class: "grid g2 sec mt" }, [
    card("🧩 Разделы и техработы", sections, h("div", { class: "row mt" }, maint)),
    card("🎰 Математика казино", table({
      compact: true,
      rows: ov.kinds,
      empty: "игр нет",
      cols: [
        { label: "режим", get: (r) => kindLabel(r.kind) },
        { label: "игр", cls: "num", k: "games" },
        { label: "ставок", cls: "num", get: (r) => money(r.bet) },
        { label: "выплат", cls: "num", get: (r) => money(r.payout) },
        { label: "казино", cls: "num", get: (r) => numCell(-r.net) },
        { label: "% побед", cls: "num", get: (r) => (r.games ? Math.round(r.wins / r.games * 100) + "%" : "—") },
      ],
    })),
  ]));

  wrap.appendChild(h("div", "grid g3 sec", ops.map((o) => opCard(o))));
  const ctry = table({
    compact: true,
    rows: cts,
    cols: [
      { label: "код", k: "code" }, { label: "флаг", k: "flag" }, { label: "страна", k: "name" },
      { label: "префикс", k: "dial" }, { label: "операторов", cls: "num", k: "operators" },
      { label: "номеров", cls: "num", k: "numbers" },
      { label: "", cls: "acts", get: (r) => h("button", { class: "btn sm", onclick: () => api("country", { code: r.code, on: r.enabled ? 0 : 1 }).then(() => draw(), bad) }, r.enabled ? "выключить" : "включить") },
    ],
  });
  wrap.appendChild(card("🌍 Страны", ctry));

  const qUid = input({ placeholder: "ID игрока" });
  const qOp = sel(ops.map((o) => [o.code, o.emoji + " " + o.name + " · " + money(o.price)]), "", (v) => { qOp.v = v; });
  const qTier = sel([["", "случайно"]].concat(TIERS.map((t) => [t, t])), "", (v) => { qTier.v = v; });
  wrap.appendChild(card("🎁 Выдать номер вручную", inlineEl(qUid, qOp, qTier,
    h("button", { class: "btn primary", onclick: () => {
      const uid = qUid.value.trim();
      if (!/^\d+$/.test(uid)) return toast("нужен числовой ID игрока", "bad");
      api("give_number", { uid: uid, operator: qOp.v || "", tier: qTier.v || "" }).then(() => { toast("выдано ✓", "good"); draw(); }, bad);
    } }, "выдать"))));
  return wrap;
};
function opCard(o) {
  const price = input({ class: "ipt ipt-sm", value: o.price });
  const name = input({ class: "ipt grow", value: o.name });
  const emoji = input({ class: "ipt", value: o.emoji, style: "width:56px" });
  const prefix = input({ class: "ipt ipt-sm", value: o.prefix });
  const odds = TIERS.map((t) => [t, input({ class: "ipt", style: "width:100%", value: o.odds[t] == null ? 0 : o.odds[t] })]);
  const save = h("button", { class: "btn sm primary", onclick: () => {
    const patch = { price: price.value, name: name.value, emoji: emoji.value, prefix: prefix.value };
    patch.odds = {};
    odds.forEach((p) => { patch.odds[p[0]] = Number(p[1].value) || 0; });
    api("operator", { code: o.code, patch: patch }).then(() => toast(o.name + " сохранён ✓", "good"), bad);
  } }, "сохранить");
  const toggle = h("button", { class: "btn sm", onclick: () => api("operator", { code: o.code, patch: { enabled: o.enabled ? 0 : 1 } }).then(() => draw(), bad) }, o.enabled ? "выключить" : "включить");
  const ev = h("div", "row between tiny", h("span", "dim", "мат.ожидание номера"),
    h("b", null, money(o.expected) + " при цене " + money(o.price)));
  return card(o.emoji + " " + o.name, [
    h("div", "row between tiny", h("span", "dim", o.country_name + " " + o.flag + " · " + o.numbers + " номеров в игре"),
      badge(o.enabled ? "включён" : "выключен", o.enabled ? "ok" : "ban")),
    h("div", { class: "mt" }, ev),
    inlineEl(h("span", "tiny dim", "цена"), price, h("span", "tiny dim", "префикс"), prefix),
    inlineEl(name, emoji),
    h("div", { class: "grid g3 mt" }, odds.map((p) => h("div", "pcell", h("span", "t-" + p[0], p[0]), p[1]))),
    h("div", { class: "inline mt" }, save, toggle),
  ]);
}

/* ═══════════ ИГРА ═══════════ */
VIEWS.play = async function () {
  const [ov, feed] = await Promise.all([get("overview"), get("feed", { limit: 25, status: "finished" })]);
  const wrap = h("div", "sec");
  const sections = chips(Object.keys(SECTIONS).map((k) => [k, (ov.sections[k] !== false ? "✅ " : "⛔ ") + SECTIONS[k]]),
    null, (k) => { const on = ov.sections[k] !== false; api("section", { section: k, on: on ? 0 : 1 }).then(() => draw(), bad); });
  const maint = h("button", { class: "btn " + (ov.maintenance ? "danger" : "ok"), onclick: () => api("maintenance", { on: ov.maintenance ? 0 : 1 }).then(() => draw(), bad) },
    ov.maintenance ? "🛠 включить" : "✅ выключены");
  wrap.appendChild(card("🕹 Разделы игры", sections, inlineEl(h("span", "dim small", "техработы:"), maint,
    h("button", { class: "btn ghost", onclick: () => { location.hash = "#/console"; setTimeout(() => { const t = document.getElementById("cmd"); if (t) { t.value = "раздел краш выкл"; t.focus(); } }, 80); } }, "в консоль"))));
  wrap.appendChild(h("div", { class: "grid g4 sec" }, [
    stat("ставок за час", money(ov.hour.bet), ov.hour.games + " игр · " + ov.hour.players + " игроков"),
    stat("выплат за час", money(ov.hour.payout), "профит " + money(ov.hour.bet - ov.hour.payout)),
    stat("новых сегодня", nf(ov.day.new), "пополнений " + ov.day.deposits),
    stat("открытых дуэлей", nf(ov.stats.open_duels), "викторин " + ov.quizzes),
  ]));
  wrap.appendChild(card("⚡ Живые игры", liveList(ov.live)));
  wrap.appendChild(h("div", { class: "mt" }, feedTable(feed)));
  return wrap;
};

/* ═══════════ ЛЕНТА ИГР ═══════════ */
VIEWS.games = async function () {
  const g = S.games;
  const data = await get("feed", { kind: g.kind, status: g.status, user: g.user, limit: g.limit });
  const wrap = h("div", "sec");
  const usr = input({ placeholder: "игрок: @ник / ID", value: g.user });
  const bar = rowEl(h("span", "dim small", "статус"),
    [["finished", "завершённые"], ["active", "активные"], ["big", "крупные"]].map((p) => h("button", {
      class: "chip" + (g.status === p[0] ? " on" : ""), onclick: () => { g.status = p[0]; draw(); },
    }, p[1])),
    h("span", "dim small", "режим"),
    sel([["", "все"], ["crash", "краш"], ["mines", "мины"], ["slots", "слоты"], ["dice", "кубик"],
      ["upgrader", "ап рублей"], ["upgrade_num", "ап номера"], ["duel", "дуэль"]], g.kind, (v) => { g.kind = v; draw(); }),
    usr,
    h("button", { class: "btn", onclick: () => { g.user = usr.value.trim(); draw(); } }, "фильтр"),
    h("button", { class: "btn ghost", onclick: () => { Object.assign(g, { kind: "", status: "finished", user: "" }); draw(); } }, "сброс"),
    h("span", "dim tiny", "live-обновление каждые 15 с"));
  wrap.appendChild(h("div", "card", bar));
  wrap.appendChild(h("div", { class: "mt" }, feedTable(data)));
  wrap.appendChild(h("div", { class: "mt" }, card("⚡ Живые игры (" + (data.live || []).length + ")", liveList(data.live))));
  return wrap;
};
function feedTable(data) {
  const rows = (data && data.games) || [];
  const cols = [
    { label: "когда", get: (r) => dt(r.finished || r.created) },
    { label: "игрок", get: (r) => [r.tag ? badge("[" + r.tag + "]", "clan") : null, userLink(r.user_id, r.first_name)] },
    { label: "режим", get: (r) => kindLabel(r.kind) },
    { label: "ставка", cls: "num", get: (r) => money(r.bet) },
    { label: "выплата", cls: "num", get: (r) => money(r.payout) },
    { label: "итог игроку", cls: "num", get: (r) => numCell(r.net) },
    { label: "статус", get: (r) => badge(r.status, r.status === "active" ? "mute" : "") },
    { label: "детали", get: (r) => h("span", { class: "dim small", title: r.result }, String(r.result || "").slice(0, 34)) },
    { label: "", cls: "acts", get: (r) => h("button", { class: "btn sm ghost", onclick: () => confirmIt("Вернуть ставку по игре " + r.id + "?", () => {
        const op = r.status === "active" ? "game_refund_live" : "game_refund";
        api(op, { game: r.id, notify: 1 }).then(() => { toast("готово ✓", "good"); draw(); }, bad);
      }) }, "рефанд") },
  ];
  return table({ compact: true, cols: cols, rows: rows, empty: "ставок нет" });
}

/* ═══════════ ПРОМОКОДЫ ═══════════ */
VIEWS.promo = async function () {
  const rows = await get("promos");
  const wrap = h("div", "sec");
  const code = input({ placeholder: "КОД" });
  const amount = input({ class: "ipt ipt-sm", value: "1000" });
  const uses = input({ class: "ipt ipt-sm", value: "100" });
  const days = input({ class: "ipt ipt-sm", value: "7" });
  const create = () => {
    if (!code.value.trim()) return toast("пустой код", "bad");
    api("promo_create", { code: code.value.trim(), amount: amount.value, uses: uses.value, days: days.value })
      .then(() => { toast("промокод создан ✓", "good"); draw(); }, bad);
  };
  wrap.appendChild(card("🎟 Создать промокод", inlineEl(code, amount, uses, days,
    h("button", { class: "btn primary", onclick: create }, "создать"),
    h("span", "dim tiny", "код · сумма ₽ · активаций · дней (0 = бессрочно)"))));
  const cols = [
    { label: "код", get: (r) => h("code", null, r.code) },
    { label: "сумма", cls: "num", get: (r) => money(r.amount) },
    { label: "осталось активаций", cls: "num", get: (r) => r.uses_left + " / " + r.total_uses },
    { label: "использовано", cls: "num", k: "uses" },
    { label: "до", get: (r) => (r.expires ? dt(r.expires) : "бессрочно") },
    { label: "", get: (r) => (r.alive ? badge("жив", "ok") : badge("закрыт", "ban")) },
    { label: "создал", get: (r) => (r.creator || "#" + r.created_by) },
    { label: "", cls: "acts", get: (r) => [
      h("button", { class: "btn sm ghost", onclick: () => {
        const v = window.prompt("Сколько активаций поставить?", r.uses_left);
        if (v != null) api("promo_bump", { code: r.code, uses: v }).then(() => draw(), bad);
      } }, "активации"),
      h("button", { class: "btn sm danger", onclick: () => confirmIt("Удалить " + r.code + "?", () => api("promo_delete", { code: r.code }).then(() => draw(), bad)) }, "удалить"),
    ] },
  ];
  wrap.appendChild(h("div", { class: "mt" }, table({ compact: true, cols: cols, rows: rows, empty: "промокодов нет" })));
  return wrap;
};

/* ═══════════ РЫНОК ═══════════ */
VIEWS.market = async function () {
  const [lots, trades, duels] = await Promise.all([get("market", { limit: 200 }), get("trades"), get("duels")]);
  const wrap = h("div", "sec");
  const lotCols = [
    { label: "когда", get: (r) => dt(r.created) },
    { label: "номер", get: (r) => h("code", null, r.full) },
    { label: "редкость", get: (r) => h("span", "t-" + r.tier, r.tier) },
    { label: "продавец", get: (r) => userLink(r.seller, "#" + r.seller) },
    { label: "цена", cls: "num", get: (r) => money(r.price) },
    { label: "оценка", cls: "num dim", get: (r) => money(r.value) },
    { label: "адресат", get: (r) => (r.buyer ? "#" + r.buyer + " " + (r.buyer_name || "") : "любой") },
    { label: "", cls: "acts", get: (r) => h("button", { class: "btn sm danger", onclick: () => confirmIt("Снять лот с рынка?", () => api("market_remove", { number: r.number_id }).then(() => draw(), bad)) }, "снять") },
  ];
  wrap.appendChild(card("🏷 Лоты рынка", table({ compact: true, cols: lotCols, rows: lots, empty: "лоты не выставлены" })));
  const tradeCols = [
    { label: "id", k: "id" },
    { label: "отдаёт", get: (r) => userLink(r.from_user, r.from_name) },
    { label: "за", get: (r) => userLink(r.to_user, r.to_name) },
    { label: "номер 1", get: (r) => r.give_full || r.give_id },
    { label: "номер 2", get: (r) => r.take_full || r.take_id },
    { label: "доплата", cls: "num", get: (r) => money(r.extra) },
    { label: "статус", get: (r) => badge(r.status, r.status === "open" ? "mute" : "") },
    { label: "", cls: "acts", get: (r) => (r.status === "open" ? h("button", { class: "btn sm danger", onclick: () => api("trade_cancel", { id: r.id }).then(() => draw(), bad) }, "отменить") : null) },
  ];
  const duelCols = [
    { label: "id", k: "id" },
    { label: "инициатор", get: (r) => userLink(r.creator, r.creator_name) },
    { label: "соперник", get: (r) => (r.opponent ? userLink(r.opponent, r.opponent_name) : "—") },
    { label: "банк", cls: "num", get: (r) => money(r.bet) },
    { label: "статус", get: (r) => badge(r.status, r.status === "playing" ? "mute" : "") },
    { label: "победитель", get: (r) => (r.winner ? "#" + r.winner : "—") },
    { label: "когда", get: (r) => dt(r.created) },
    { label: "", cls: "acts", get: (r) => (["open", "invited", "playing"].indexOf(r.status) >= 0
      ? h("button", { class: "btn sm danger", onclick: () => confirmIt("Отменить дуэль и вернуть банки игрокам?", () => api("duel_cancel", { id: r.id }).then(() => draw(), bad)) }, "отменить") : null) },
  ];
  wrap.appendChild(h("div", { class: "grid g2 sec mt" }, [
    card("🔄 Обмены", table({ compact: true, cols: tradeCols, rows: trades, empty: "обменов нет" })),
    card("⚔️ Дуэли", table({ compact: true, cols: duelCols, rows: duels, empty: "дуэлей нет" })),
  ]));
  return wrap;
};

/* ═══════════ КЛАНЫ ═══════════ */
VIEWS.clans = async function () {
  const rows = await get("clans");
  const wrap = h("div", "sec");
  const cols = [
    { label: "id", k: "id", cls: "dim" },
    { label: "тег", get: (r) => badge("[" + r.tag + "]", "clan") },
    { label: "название", k: "name" },
    { label: "глава", get: (r) => userLink(r.owner, r.owner_name || ("#" + r.owner)) },
    { label: "участников", cls: "num", k: "members" },
    { label: "казна", cls: "num", get: (r) => money(r.treasury) },
    { label: "балансы участников", cls: "num dim", get: (r) => money(r.money) },
    { label: "открыт", get: (r) => (r.open ? "да" : "нет") },
    { label: "создан", get: (r) => dstr(r.created) },
    { label: "", cls: "acts", get: (r) => [
      h("button", { class: "btn sm ghost", onclick: () => {
        const v = window.prompt("Пополнить (+) или вывести (−) из казны, ₽", "1000");
        if (v != null) api("clan_treasury", { clan: r.id, delta: v }).then(() => draw(), bad);
      } }, "казна"),
      h("button", { class: "btn sm danger", onclick: () => confirmIt("Удалить клан [" + r.tag + "] и распустить участников?", () => api("clan_delete", { clan: r.id }).then(() => draw(), bad)) }, "удалить"),
    ] },
  ];
  wrap.appendChild(card("🏰 Кланы (" + rows.length + ")", table({ compact: true, cols: cols, rows: rows, empty: "кланов пока нет" })));
  return wrap;
};

/* ═══════════ ЧАТЫ ═══════════ */
VIEWS.chats = async function () {
  const rows = await get("chats");
  const wrap = h("div", "sec");
  const text = ta({ placeholder: "Текст рассылки. Теги будут экранированы — писать можно просто словами.", style: "min-height:90px" });
  let filter = "all";
  const filterSel = sel([["all", "все игроки"], ["week", "заходили неделю"], ["active", "активные 24ч"],
    ["paid", "платившие ⭐"], ["rich", "богатые 500К+"], ["plus", "в плюсе к казино"]], filter, (v) => { filter = v; });
  const send = (dry) => {
    if (!text.value.trim()) return toast("пусто", "bad");
    api("broadcast", { text: text.value, target: "both", filter: filter, dry_run: dry ? 1 : 0 }).then((r) => {
      if (dry) toast("получателей: " + r.result.targets, "good");
      else toast("рассылка запущена → " + r.result.targets + " адресатов", "good");
    }, bad);
  };
  wrap.appendChild(card("📣 Рассылка игрокам и чатам", text,
    inlineEl(h("span", "dim small", "фильтр игроков"), filterSel,
      h("button", { class: "btn ghost", onclick: () => send(1) }, "предпросмотр"),
      h("button", { class: "btn primary", onclick: () => confirmIt("Отправить это всем выбранным игрокам и в чаты?", () => send(0)) }, "разослать"))));
  const cols = [
    { label: "chat id", get: (r) => h("code", null, r.id) },
    { label: "название", get: (r) => (r.title || "—") },
    { label: "добавил", get: (r) => (r.added_by ? userLink(r.added_by, "#" + r.added_by) : "—") },
    { label: "игроков", cls: "num", k: "players" },
    { label: "ставок", cls: "num", k: "games" },
    { label: "дуэлей", cls: "num", k: "duels" },
    { label: "викторин", cls: "num", k: "quizzes" },
    { label: "статус", get: (r) => (r.banned ? badge("⛔ чёрный список", "ban") : r.allowed ? badge("работает", "ok") : badge("вышел")) },
    { label: "с", get: (r) => dstr(r.created) },
    { label: "", cls: "acts", get: (r) => [
      h("button", { class: "btn sm ghost", onclick: () => {
        const t = window.prompt("Сообщение в чат " + r.id + ":");
        if (t) api("dm", { chat_id: r.id, text: t }).then(() => toast("отправлено ✓", "good"), bad);
      } }, "написать"),
      h("button", { class: "btn sm " + (r.banned ? "ok" : "danger"), onclick: () => api("chat_ban", { chat: r.id, banned: r.banned ? 0 : 1 }).then(() => draw(), bad) },
        r.banned ? "разбанить" : "в ЧС"),
    ] },
  ];
  wrap.appendChild(h("div", { class: "mt" }, card("💬 Чаты бота (" + rows.length + ")", table({ compact: true, cols: cols, rows: rows, empty: "бот ещё никуда не добавлен" }))));
  return wrap;
};

/* ═══════════ ПЛАТЕЖИ ═══════════ */
VIEWS.pay = async function () {
  const rows = await get("payments", { limit: 300 });
  const wrap = h("div", "sec");
  const stars = rows.reduce((a, r) => a + (r.refunded ? 0 : Number(r.stars) || 0), 0);
  const rub = rows.reduce((a, r) => a + (r.refunded ? 0 : Number(r.rubles) || 0), 0);
  const refunded = rows.filter((r) => r.refunded).length;
  const top = rows.slice().sort((a, b) => b.stars - a.stars)[0];
  wrap.appendChild(h("div", "grid g4", [
    stat("платежей", nf(rows.length), "записей в базе"),
    stat("звёзд", nf(stars) + " ⭐", "зачислено " + money(rub)),
    stat("возвратов", nf(refunded), "refundStarPayment"),
    stat("крупнейший", top ? nf(top.stars) + " ⭐" : "—", top ? (top.first_name || "#" + top.user_id) : ""),
  ]));
  const cols = [
    { label: "когда", get: (r) => dt(r.created) },
    { label: "игрок", get: (r) => userLink(r.user_id, r.first_name || ("#" + r.user_id)) },
    { label: "⭐", cls: "num", k: "stars" },
    { label: "₽ зачислено", cls: "num", get: (r) => money(r.rubles) },
    { label: "charge_id", get: (r) => h("code", { class: "dim", title: r.charge_id }, String(r.charge_id).slice(0, 22)) },
    { label: "", get: (r) => (r.refunded ? badge("возврат", "ban") : badge("ок", "ok")) },
    { label: "", cls: "acts", get: (r) => (!r.refunded ? h("button", { class: "btn sm danger", onclick: () => refund(r.charge_id) }, "рефанд") : null) },
  ];
  wrap.appendChild(h("div", { class: "mt" }, card("Чеки Telegram Stars", table({ compact: true, cols: cols, rows: rows, empty: "платежей не было" }))));
  return wrap;
};

/* ═══════════ АУДИТ ═══════════ */
VIEWS.audit = async function () {
  const a = S.audit;
  const data = await get("audit", { limit: a.limit, action: a.action });
  const wrap = h("div", "sec");
  const acts = Array.from(new Set(data.rows.map((r) => r.action))).sort();
  const bar = rowEl(h("span", "dim small", "действие"),
    sel([["", "все"]].concat(acts.map((x) => [x, x])), a.action, (v) => { a.action = v; draw(); }),
    h("span", "dim small", "строк"),
    sel([[50, "50"], [100, "100"], [300, "300"], [1000, "1000"]], String(a.limit), (v) => { a.limit = Number(v); draw(); }),
    h("span", "dim tiny", "то же, что «аудит» в чате бота (доступен владельцу)"));
  wrap.appendChild(h("div", "card", bar));
  wrap.appendChild(h("div", { class: "mt" }, auditTable(data.rows)));
  return wrap;
};

/* ═══════════ БАЗА ═══════════ */
VIEWS.db = async function () {
  const f = S.db;
  const [info, data] = await Promise.all([get("tables"), get("browse", { table: f.table, q: f.q, user: f.user, page: f.page, limit: f.limit, order: f.order, desc: f.desc })]);
  const wrap = h("div", "sec");
  const q = input({ placeholder: "поиск по текстовым колонкам", value: f.q });
  const usr = input({ class: "ipt ipt-sm", placeholder: "игрок: @ник / ID", value: f.user });
  const bar = rowEl(
    sel(Object.keys(info.tables).sort().map((t) => [t, t + " (" + nf(info.tables[t]) + ")"]), f.table,
      (v) => { Object.assign(f, { table: v, page: 1, order: "" }); draw(); }, "ipt"),
    q, usr,
    h("button", { class: "btn", onclick: () => { f.q = q.value.trim(); f.user = usr.value.trim(); f.page = 1; draw(); } }, "применить"),
    h("button", { class: "btn ghost", onclick: () => { Object.assign(f, { q: "", user: "", page: 1 }); draw(); } }, "сброс"),
    h("span", "dim tiny", "только чтение · список таблиц задаёт сервер"));
  wrap.appendChild(h("div", "card", bar));
  const cols = data.columns.map((c) => ({ label: c, sort: c, get: (r) => cellRaw(c, r[c]) }));
  wrap.appendChild(h("div", { class: "mt" }, table({
    compact: true, cols: cols, rows: data.rows, sortKey: f.order, desc: !!f.desc,
    onSort: (k) => { if (f.order === k) f.desc = f.desc ? 0 : 1; else { f.order = k; f.desc = 1; } draw(); },
    empty: "в таблице пусто",
  })));
  wrap.appendChild(pager(data, (p) => { f.page = p; draw(); }));
  const del = ["promos", "quizzes", "warns", "trades", "market", "duels"];
  if (del.indexOf(f.table) >= 0) {
    const key = input({ placeholder: "ключ записи для удаления" });
    wrap.appendChild(h("div", { class: "mt" }, card("Удалить строку из " + f.table, inlineEl(key,
      h("button", { class: "btn sm danger", onclick: () => {
        if (!key.value.trim()) return toast("укажи ключ", "bad");
        confirmIt("Удалить " + f.table + " · " + key.value + "?", () => api("browse_delete", { table: f.table, key: key.value.trim() }).then(() => draw(), bad));
      } }, "удалить")))));
  }
  return wrap;
};
function cellRaw(name, v) {
  if (v == null) return h("span", "dim", "∅");
  if (typeof v === "number") {
    if (/^(created|finished|last_seen|expires|updated|muted_until)$/.test(name)) return h("span", { title: new Date(v * 1000).toLocaleString("ru-RU") }, ago(v));
    if (/(balance|bet|payout|value|price|amount|delta|extra|treasury|stars|rubles)/.test(name)) return money(v);
    return nf(v);
  }
  const s = String(v);
  if (/^\d+$/.test(s) && /(_id|^id)$/.test(name) && name !== "number_id") return userLink(s, s);
  if (/^(common|uncommon|rare|epic|legendary|mythic)$/.test(s)) return h("span", "t-" + s, s);
  if (s.length > 70) return h("code", { class: "dim", title: s }, s.slice(0, 70) + "…");
  return s;
}

/* ═══════════ КОНСОЛЬ ═══════════ */
VIEWS.console = async function () {
  const wrap = h("div", "sec");
  const out = h("div", { class: "hist mt" });
  const cmd = h("textarea", { id: "cmd", placeholder: "любая админ-команда бота: статистика · выдать 5000 @nick · оператор шансы testo 55 27 12 4.5 1.2 0.3 · викторина 100 500 слово · бан 1001 флуд",
    onkeydown: (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); run(); } } });
  const chatIn = input({ placeholder: "ID чата, «как если бы команду написали там» (пусто = личка)" });
  async function run() {
    const text = cmd.value.trim();
    if (!text) return;
    const pending = h("div", "li", h("div", "l", h("code", "cmd", text)), h("div", "r dim", "выполняю…"));
    out.prepend(pending);
    try {
      const r = await post("console", { text: text, chat_id: chatIn.value.trim() || undefined });
      const replies = (r.replies || []).map((x) => sanitize(x)).join("<hr>");
      const body = r.ok ? rich(replies || "— бот ничего не ответил —") : rich('<span class="neg">' + esc(r.error || "ошибка") + "</span>");
      pending.replaceWith(h("div", "card", h("code", "cmd", text), h("div", { class: "mt" }, body)));
      S.hist.unshift(text);
      if (r.kpi) renderKpis(r.kpi);
    } catch (e) {
      pending.replaceWith(h("div", "card", h("code", "cmd", text), h("div", { class: "neg mt" }, String(e.message || e))));
    }
    cmd.value = "";
  }
  const quick = ["статистика", "разделы", "настройки", "титулы", "чаты", "аудит", "промо список", "операторы", "админка", "викторина стоп"];
  wrap.appendChild(card("⌨️ Консоль администрации",
    h("div", { class: "tiny dim" }, "Команда исполняется так, как если бы её написал в личке владелец бота (ранг 5): проходят все проверки бота, действия попадают в «аудит», а ответ бота показывается ниже. Это доступ ко всем админ-командам сразу, включая те, что не продублированы кнопками."),
    h("div", { class: "mt" }, chatIn),
    h("div", { class: "console-in mt" }, cmd,
      h("button", { class: "btn primary", onclick: run }, "исполнить ⌘⏎"),
      h("button", { class: "btn ghost", onclick: () => { out.innerHTML = ""; cmd.value = ""; } }, "очистить")),
    h("div", { class: "chips mt" }, quick.map((c) => h("button", { class: "chip", onclick: () => { cmd.value = c; run(); } }, c))),
    out));
  return wrap;
};

/* ─────────── старт ─────────── */
(function init() {
  const form = document.getElementById("login-form");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = document.getElementById("login-btn");
    const msg = document.getElementById("login-msg");
    const box = document.getElementById("pw");
    msg.textContent = "";
    btn.disabled = true;
    try {
      const r = await fetch(API + "login", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: box.value }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d.ok) {
        msg.textContent = d.error || "не вышло (" + r.status + ")";
        return;
      }
      S.csrf = d.csrf || "";
      sessionStorage.setItem("pnl-csrf", S.csrf);
      box.value = "";
      await boot();
    } catch (err) {
      msg.textContent = "сеть недоступна: " + (err.message || err);
    } finally {
      btn.disabled = false;
    }
  });
  document.getElementById("pw-peek").addEventListener("click", () => {
    const i = document.getElementById("pw");
    i.type = i.type === "password" ? "text" : "password";
    i.focus();
  });
  document.getElementById("btn-logout").addEventListener("click", async () => {
    try { await post("logout", {}); } catch (e) { /* не страшно */ }
    S.csrf = "";
    sessionStorage.removeItem("pnl-csrf");
    showGate();
  });
  document.getElementById("btn-refresh").addEventListener("click", () => draw());
  document.getElementById("live").addEventListener("change", (e) => { S.live = e.target.checked; });
  window.addEventListener("hashchange", () => draw());
  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, textarea, select")) return;
    if (e.key === "r") draw();
    if (e.key === "Escape") document.getElementById("modal").classList.add("hidden");
  });
  boot();
}());
