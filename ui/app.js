"use strict";
/* KytyPS5 Studio - PS5-style shell. Vanilla JS, no dependencies. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
function h(tag, attrs, ...kids) {
  const [t, ...cls] = tag.split(".");
  const el = document.createElement(t.split("#")[0] || "div");
  const last = cls.length ? cls.length - 1 : -1, m = (last >= 0 ? cls[last] : t).split("#");
  if (m[1]) { el.id = m[1]; if (last >= 0) cls[last] = m[0]; }
  if (cls.length) el.className = cls.join(" ");
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "on") for (const [ev, fn] of Object.entries(v)) el.addEventListener(ev, fn);
    else if (k === "style") el.style.cssText = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("data-")) el.setAttribute(k, v === true ? "" : v);
    else el[k] = v;
  }
  for (const c of kids.flat()) if (c != null && c !== false) el.append(c.nodeType ? c : document.createTextNode(c));
  return el;
}

/* ------------------------------------------------------------ api ---- */
const TOKEN = window.STUDIO_TOKEN;
async function api(path, body) {
  const r = await fetch(path, body === undefined ? { headers: { "X-Studio-Token": TOKEN } } :
    { method: "POST", headers: { "X-Studio-Token": TOKEN, "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
const art = (id, kind) => `/api/art/${id}/${kind}?t=${TOKEN}`;

/* --------------------------------------------------------- app state -- */
const S = { forks: [], settings: {}, libraries: [], defaultFork: "original", jobs: [], running: [], hasToken: false };
let games = [];
let tab = "games";
let selTile = 0;
const toldUpdate = new Set();
const seenJobs = new Map();

/* ------------------------------------------------------------ sound --- */
let actx;
function tone(freq, dur, gain = .05, type = "sine", delay = 0, slide = 0) {
  if (!S.settings.sounds) return;
  try {
    actx = actx || new AudioContext();
    if (actx.state === "suspended") actx.resume();
    const o = actx.createOscillator(), g = actx.createGain(), t = actx.currentTime + delay;
    o.type = type; o.frequency.setValueAtTime(freq, t);
    if (slide) o.frequency.exponentialRampToValueAtTime(freq * slide, t + dur);
    g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(gain, t + .008);
    g.gain.exponentialRampToValueAtTime(.0001, t + dur);
    o.connect(g).connect(actx.destination); o.start(t); o.stop(t + dur + .02);
  } catch (e) { /* audio unavailable */ }
}
const sfx = {
  move: () => tone(740, .06, .03, "sine", 0, .8),
  tab: () => { tone(660, .09, .04, "triangle"); tone(990, .12, .03, "sine", .04); },
  ok: () => { tone(740, .08, .05, "triangle"); tone(1110, .16, .05, "sine", .06); },
  back: () => tone(520, .12, .04, "triangle", 0, .6),
  open: () => tone(440, .22, .04, "sine", 0, 1.6),
  err: () => { tone(200, .18, .06, "sawtooth"); tone(150, .24, .06, "sawtooth", .12); },
  done: () => { [660, 880, 1320].forEach((f, i) => tone(f, .25, .05, "sine", i * .09)); },
};

/* ------------------------------------------------ on-screen keyboard --- */
const OSK_ROWS = {
  abc: [["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "\u232b"], ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p"], ["a", "s", "d", "f", "g", "h", "j", "k", "l"], ["\u21e7", "z", "x", "c", "v", "b", "n", "m", ",", "."]],
  sym: [["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "\u232b"], ["!", "@", "#", "$", "%", "^", "&", "*", "(", ")"], ["-", "_", "=", "+", "/", "\\", ":", ";", "'", "\""], ["~", "[", "]", "{", "}", "<", ">", "?", "|", "."]],
  num: [["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"], ["\u232b", "0", "\u2713"]],
};
let OSK = null;
function openKeyboard(o) {
  return new Promise(res => {
    OSK = { text: o.value || "", layer: o.numeric ? "num" : "abc", shift: !o.value && !o.numeric && !o.password, max: o.max || 64, o, res, opener: F.cur };
    drawKeyboard();
    $("#osk").classList.add("on"); sfx.open();
    setTimeout(() => setFocus($('#osk [data-key="k-q"]') || $("#osk [data-f]"), true), 30);
  });
}
function drawKeyboard(focusKey) {
  const o = OSK.o, box = $("#osk");
  const field = h("div.osk-field", {}, h("span.t"), h("i.caret"), h("span.cnt"));
  const label = c => c === "\u21e7" ? "\u21e7" : OSK.layer === "abc" && OSK.shift && /[a-z]/.test(c) ? c.toUpperCase() : c;
  const key = (id, text, fn, cls) => h("button.key" + (cls ? "." + cls : ""), { "data-f": true, "data-key": "k-" + id, on: { click: fn } }, text);
  const rows = OSK_ROWS[OSK.layer].map(r => h("div.krow", {}, ...r.map(c => {
    if (c === "\u232b") return key("bs", "\u232b", oskBackspace, "wide");
    if (c === "\u21e7") return key("shift", "\u21e7", () => { OSK.shift = !OSK.shift; drawKeyboard("k-shift"); }, "wide" + (OSK.shift ? " on" : "").replace(" ", "."));
    if (c === "\u2713") return key("ok", "\u2713", oskDone, "done");
    return key(c, label(c), () => oskType(c));
  })));
  const kb = h("div.osk-keys" + (OSK.layer === "num" ? ".num" : ""), {}, ...rows);
  if (OSK.layer !== "num") kb.append(h("div.krow", {},
    key("layer", OSK.layer === "abc" ? "123 #+=" : "ABC", () => { OSK.layer = OSK.layer === "abc" ? "sym" : "abc"; drawKeyboard("k-layer"); }, "act"),
    key("space", "Space", () => oskType(" "), "xl"),
    key("cancel", "Cancel", oskCancel, "act"),
    key("done", o.okLabel || "Done", oskDone, "act.done")));
  box.replaceChildren(
    h("div.osk-top", {}, h("div.osk-title", { text: o.title || "Enter text" }), o.hint ? h("div.osk-hint", { text: o.hint }) : null, field),
    h("div.osk-kb", {}, kb, h("div.osk-legend", {},
      h("span", {}, h("i.glyph", { text: "\u2715" }), "Enter"), h("span", {}, h("i.glyph", { text: "\u25a1" }), "Delete"), h("span", {}, h("i.glyph", { text: "\u25b3" }), "Space"),
      h("span", {}, h("i.glyph", { text: "R1" }), "Shift"), h("span", {}, h("i.glyph.opt", { text: "\u2261" }), "Done"), h("span", {}, h("i.glyph", { text: "\u25cb" }), "Cancel"))));
  oskRefresh();
  if (focusKey) setFocus($(`#osk [data-key="${focusKey}"]`), true);
}
function oskRefresh() {
  const o = OSK.o, t = $("#osk .osk-field .t"); if (!t) return;
  const shown = o.password ? "\u2022".repeat(OSK.text.length) : OSK.text;
  t.textContent = shown || o.placeholder || ""; t.classList.toggle("ph", !shown);
  $("#osk .cnt").textContent = `${OSK.text.length}/${OSK.max}`;
}
function oskType(c) {
  if (!OSK || OSK.text.length >= OSK.max) return;
  if (OSK.o.numeric && !/\d/.test(c)) return;
  if (OSK.layer === "abc" && OSK.shift && /[a-z]/.test(c)) { c = c.toUpperCase(); OSK.shift = false; oskRefresh(); drawKeyboard(F.cur?.dataset.key); return oskAdd(c); }
  oskAdd(c);
}
function oskAdd(c) { OSK.text += c; oskRefresh(); }
function oskBackspace() { if (OSK) { OSK.text = OSK.text.slice(0, -1); oskRefresh(); } }
function oskShift() { if (OSK && OSK.layer === "abc") { OSK.shift = !OSK.shift; drawKeyboard(F.cur?.dataset.key); } }
function closeKeyboard(value) {
  const { res, opener } = OSK; OSK = null;
  $("#osk").classList.remove("on"); $("#osk").replaceChildren();
  F.cur = null;
  setTimeout(() => { const k = opener?.dataset?.key; const t = opener?.isConnected ? opener : k && $$("[data-f]").find(e => e.dataset.key === k); t ? setFocus(t, true) : focusDefault(); }, 0);
  res(value);
}
function oskDone() { if (OSK) { sfx.ok(); closeKeyboard(OSK.text); } }
function oskCancel() { if (OSK) { sfx.back(); closeKeyboard(null); } }
function oskPhysicalKey(e) {  // a real keyboard works too
  if (e.key === "Escape") { e.preventDefault(); return oskCancel(); }
  if (e.key === "Enter") { e.preventDefault(); return oskDone(); }
  if (e.key === "Backspace") { e.preventDefault(); return oskBackspace(); }
  const map = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" };
  if (map[e.key]) { e.preventDefault(); return move(map[e.key]); }
  if (e.key === " " && !e.target.matches?.("button")) { e.preventDefault(); return oskType(" "); }
  if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); if (OSK.layer === "abc" && /[A-Z]/.test(e.key)) { OSK.shift = false; oskAdd(e.key); } else oskType(e.key.toLowerCase() === e.key || OSK.layer !== "abc" ? e.key : e.key); }
}
/* open the keyboard for a settings text row */
function editInput(row) {
  const inp = $("input", row); if (!inp) return false;
  const title = row.querySelector("div")?.firstChild?.textContent || "Enter text";
  openKeyboard({ title, value: inp.type === "password" ? "" : inp.value, password: inp.type === "password", numeric: inp.type === "number", max: inp.type === "number" ? 6 : 120, placeholder: inp.placeholder })
    .then(v => { if (v !== null) { inp.value = v; inp.dispatchEvent(new Event("change")); } });
  return true;
}

/* ---------------------------------------------------------------- users --- */
const AVATARS = [["blue", "#4aa8ff", "#1b4fd6", "\u2605"], ["violet", "#b07bff", "#4b2bd6", "\u263e"], ["pink", "#ff80bd", "#d63384", "\u2665"],
  ["orange", "#ffbb55", "#e0561b", "\u2600"], ["green", "#55e090", "#0f8a55", "\u2663"], ["teal", "#4fe0e0", "#0f7c8a", "\u266a"],
  ["red", "#ff7070", "#b3202f", "\u26a1"], ["gold", "#ffe680", "#d9930a", "\u2655"], ["slate", "#a3b0c6", "#3d4a63", "\u263a"],
  ["lime", "#cdf56f", "#5a9a14", "\u273f"], ["coral", "#ffa595", "#c94a5a", "\u2726"], ["ice", "#c6ebff", "#4a86d9", "\u2744"]];
const avatarOf = k => AVATARS.find(a => a[0] === k) || AVATARS[0];
const me = () => S.users.find(u => u.id === S.activeUser) || S.users[0] || { id: "", name: "Player", avatar: "blue" };
function avatarEl(u) {
  const [, a, b, g] = avatarOf(u.avatar);
  return h("div.av", { style: `background:radial-gradient(circle at 35% 30%,${a},${b})` }, h("span", { text: g }));
}
const needUser = () => S.settings.askUser !== false && !new URLSearchParams(location.search).has("nouser");

function pickAvatar(current) {
  return new Promise(res => {
    const done = v => { hideOverlay(); res(v); };
    const grid = h("div.avgrid", {}, ...AVATARS.map(a => h("button.avpick" + (a[0] === current ? ".sel" : ""), { "data-f": true, "data-default": a[0] === current || (!current && a[0] === "blue"), on: { click: () => done(a[0]) } },
      avatarEl({ avatar: a[0] }))));
    showOverlay(h("div.ov", { style: "max-width:140rem" }, h("h2", { text: "Choose an Avatar" }), grid,
      h("div", { style: "margin-top:3rem" }, h("button.pill.ghost", { "data-f": true, on: { click: () => done(null) } }, "Cancel"))), () => done(null));
  });
}
async function createUser() {
  const avatar = await pickAvatar(null); if (!avatar) return null;
  const name = await openKeyboard({ title: "Create User", hint: "Enter a name for the new user. Saves and trophies are kept separately for each user.", placeholder: "Name", okLabel: "Done", max: 16 }).then(v => (v && v.trim()) || null); if (!name) return null;
  try { const r = await api("/api/user/add", { name, avatar }); await refreshState(); return r.user; } catch (e) { fail(e); return null; }
}
function editUser(u, changed) {
  openPanel(u.name, "User", body => {
    body.append(opt("Change name", "", async () => { closePanel(); const n = await openKeyboard({ title: "Change Name", hint: "Enter a new name.", value: u.name, okLabel: "Done", max: 16 }).then(v => (v && v.trim()) || null); if (n) try { await api("/api/user/update", { id: u.id, name: n }); await refreshState(); renderTop(); changed?.(); } catch (e) { fail(e); } }),
      opt("Change avatar", "", async () => { closePanel(); const a = await pickAvatar(u.avatar); if (a) try { await api("/api/user/update", { id: u.id, avatar: a }); await refreshState(); renderTop(); changed?.(); } catch (e) { fail(e); } }),
      opt("Delete user", S.users.length < 2 ? "Last user" : "", async () => {
        closePanel();
        if (await confirmBox(`Delete ${u.name}?`, "Their saves and trophies stay on disk, but the user disappears from the list.", "Delete")) try { await api("/api/user/delete", { id: u.id }); await refreshState(); renderTop(); changed?.(); } catch (e) { fail(e); }
      }, { disabled: S.users.length < 2 }));
  });
}
function userRow(pick, o = {}) {
  const row = h("div.u-row" + (o.compact ? ".compact" : ""));
  const users = [...S.users].sort((a, b) => a.created - b.created);
  row.append(h("button.uitem.add", { "data-f": true, "data-key": "u-add", on: { click: async () => { const id = await createUser(); if (id) o.onChange?.(id); } } },
    h("div.uslot", {}, h("div.uav", { text: "+" })), h("div.uname", { text: "Add User" })));
  users.forEach(u => {
    const it = h("button.uitem", { "data-f": true, "data-key": "u-" + u.id, "data-default": u.id === S.activeUser, on: { click: () => pick(u) } },
      h("div.uslot", {}, h("div.uav", {}, avatarEl(u))), h("div.uname", { text: u.name }),
      o.compact && u.id === S.activeUser ? h("div.ulog", { text: "Logged In" }) : h("div.uopt", { text: o.compact ? "" : "\u2261  Options" }));
    it._options = () => editUser(u, () => o.onChange?.(u.id));
    row.append(it);
  });
  return row;
}
async function chooseUser(id) {
  try { await api("/api/user/select", { id }); } catch (e) { fail(e); return false; }
  await refreshAll(); TR.overview = null; TR.byGame = {}; await loadTrophies();
  renderTop(); renderView(); return true;
}
function showUsers() {
  document.body.classList.add("pre");
  const scr = h("div#users");
  const draw = focusId => {
    scr.replaceChildren(h("div.glow.g1"), h("div.glow.g2"), h("div.glow.g3"),
      h("div.u-title", { text: "Welcome Back to Studio" }), h("div.u-sub", { text: "Who's using this controller?" }), h("div.u-clock", { id: "uclock", text: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) }),
      userRow(async u => { if (await chooseUser(u.id)) { sfx.ok(); scr.classList.add("leave"); document.body.classList.remove("pre"); setTimeout(() => { scr.remove(); focusDefault(); }, 1100); } }, { onChange: id => draw(id) }),
      h("button.u-power", { "data-f": true, "data-key": "u-power", on: { click: async () => { if (await confirmBox("Exit KytyPS5 Studio?", "Games that are running will keep running.", "Exit")) api("/api/quit", {}).then(() => window.close()).catch(() => { }); } } }, "\u23fb"),
      h("div.u-hint", {}, h("i.glyph", { text: "\u2715" }), "Select"));
    setTimeout(() => { const t = $$("[data-f]", scr).find(e => e.dataset.key === "u-" + (focusId || S.activeUser)); setFocus(t || $$("[data-f]", scr)[1], true); }, 30);
  };
  document.body.append(scr); draw();
  requestAnimationFrame(() => scr.classList.add("on"));
}
function openSwitchUser() {
  if (gameRunning()) { toast("A game is running", "Close it before switching user.", true); return; }
  const dlg = h("div.switchbox");
  const draw = () => dlg.replaceChildren(h("h2", { text: "Switch User" }),
    h("ul", {}, h("li", { text: "You'll take over the previous user's accessories." }), h("li", { text: "They'll be logged out." })),
    userRow(async u => { hideOverlay(); if (u.id !== S.activeUser && await chooseUser(u.id)) toast("Switched user", `Now playing as ${u.name}`); }, { compact: true, onChange: () => { draw(); setTimeout(focusDefault, 30); } }));
  draw(); showOverlay(dlg, hideOverlay);
}
function openProfile() {
  const u = me();
  openPanel(u.name, "Profile", body => {
    body.append(opt("Trophies", "", () => { closePanel(); TR.from = "games"; switchTab("trophies"); }),
      opt("Switch User", "", () => { closePanel(); openSwitchUser(); }),
      opt("Edit user", "", () => { closePanel(); editUser(u, () => { renderTop(); }); }));
  });
}

/* ---------------------------------------------------------- trophies --- */
const GRADE = { P: "Platinum", G: "Gold", S: "Silver", B: "Bronze" };
const TR = { overview: null, byGame: {}, game: null, gameId: null, from: "games", filter: "all", sort: "default" };
const trLang = () => navigator.language || "en-US";
const trArt = (gid, name) => `/api/trophy-art/${gid}/${name}?t=${TOKEN}`;
function cup(g, size) {
  const svg = `<svg viewBox="0 0 24 24"><g fill="url(#cg-${g})"><path d="M6.5 3h11v6a5.5 5.5 0 0 1-11 0z"/><path d="M10.5 14.2h3V17h3.4v3.6H7.1V17h3.4z"/></g><g fill="none" stroke="url(#cg-${g})" stroke-width="1.8" stroke-linecap="round"><path d="M6.5 5.2H3.8v2a3.6 3.6 0 0 0 3.6 3.6"/><path d="M17.5 5.2h2.7v2a3.6 3.6 0 0 1-3.6 3.6"/></g></svg>`;
  return h("span.cup." + g + (size ? "." + size : ""), { innerHTML: svg });
}
async function loadTrophies() {
  const prev = TR.overview ? TR.overview.earnedCount : null;
  try {
    const o = await api("/api/trophies?lang=" + encodeURIComponent(trLang()));
    TR.overview = o; TR.byGame = Object.fromEntries(o.games.map(g => [g.game, g]));
  } catch (e) { /* trophies are optional */ }
  return prev;
}
async function openTrophyGame(gid, from) {
  TR.gameId = gid; TR.from = from; TR.game = null; TR.filter = "all"; TR.sort = "default";
  switchTab("trophylist");
  try {
    TR.game = await api(`/api/trophies/game?id=${gid}&lang=${encodeURIComponent(trLang())}`);
    if (tab === "trophylist") renderView();
  } catch (e) { fail(e); switchTab(from === "trophies" ? "trophies" : "games"); }
}
const fmtWhen = ts => ts ? new Date(ts * 1000).toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
function countsRow(earned, total) {
  return h("div.counts" + (total ? ".ng" : ""), {}, ...["P", "G", "S", "B"].map(g =>
    h("div", {}, cup(g, "lg"), total ? `${earned[g]}/${total[g]}` : String(earned[g]), total ? null : h("small", { text: GRADE[g] }))));
}
function viewTrophies() {
  const o = TR.overview, v = h("div.view.settings");
  v.append(backBtn(), h("div.stitle", { text: "Trophies" }));
  if (!o) { v.append(h("div.empty", {}, h("h1", { text: "Loading trophies\u2026" }))); return v; }
  v.append(h("div.tsum", {},
    h("div.who", {}, h("div.bigav", {}, avatarEl(me())), h("div", {}, h("b", { text: me().name }), h("span", { text: `${o.earnedCount} trophies earned` }))),
    h("div.lvl", {}, h("small", { text: "Level" }), h("b", { text: String(o.level) }), h("span", { text: o.levelProgress + "%" })),
    h("div.bar", {}, h("i", { style: `width:${o.levelProgress}%` })),
    countsRow(o.earned),
    h("div.totals", {}, `${o.points} points`, h("br"), `${o.games.length} game${o.games.length === 1 ? "" : "s"} with trophies`)));
  const list = h("div.items#items");
  if (!o.games.length) list.append(h("div.empty.inline", {}, h("h1", { text: "No trophies yet" }), h("p", { text: "Games that ship a trophy set (sce_sys/trophy2) appear here once they are in your library." })));
  o.games.forEach((g, i) => {
    const row = h("button.grow", { "data-f": true, "data-key": "tg" + g.game, "data-default": i === 0, "data-go-left": "#tabs .tab.active, #btn-trophy", on: { click: () => openTrophyGame(g.game, "trophies") } },
      g.icon ? h("img", { src: art(g.game, "icon"), draggable: false }) : h("div"),
      h("div", {}, h("div.gt", { text: g.title }), h("div.gp", {}, h("div.bar", {}, h("i", { style: `width:${g.progress}%` })), h("span", { text: g.progress + "%" }))),
      h("div.gc", {}, ...["P", "G", "S", "B"].map(k => h("span", {}, cup(k, "sm"), String(g.earned[k])))));
    list.append(row);
  });
  v.append(list);
  return v;
}
function viewTrophyGame() {
  const d = TR.game, v = h("div.view.settings"), meta = TR.byGame[TR.gameId];
  v.append(backBtn(), h("div.stitle", { text: "Trophies" }));
  if (!d) { v.append(h("div.empty", {}, h("h1", { text: "Loading trophies\u2026" }))); return v; }
  v.append(h("div.tsum", {},
    h("div.gicon", {}, meta && meta.icon ? h("img", { src: art(TR.gameId, "icon") }) : null),
    h("h3", { text: d.title || (meta && meta.title) || "" }),
    h("div.pct", {}, h("span", { text: "Progress" }), h("b", { text: d.progress + "%" })),
    h("div.bar", {}, h("i", { style: `width:${d.progress}%` })),
    countsRow(d.earned, d.total),
    h("div.totals", {}, `${d.earnedCount} of ${d.count} earned`, h("br"), `${d.points} points`)));
  let rows = d.trophies.slice();
  if (TR.filter === "earned") rows = rows.filter(t => t.earned);
  if (TR.filter === "locked") rows = rows.filter(t => !t.earned);
  if (TR.sort === "recent") rows.sort((a, b) => (b.at || 0) - (a.at || 0) || a.id - b.id);
  if (TR.sort === "grade") rows.sort((a, b) => "PGSB".indexOf(a.grade) - "PGSB".indexOf(b.grade) || a.id - b.id);
  const list = h("div.items#items"), multi = Object.keys(d.groups).length > 1 && TR.sort === "default";
  let last = null, first = true;
  if (!rows.length) list.append(h("div.empty.inline", {}, h("h1", { text: "Nothing to show" }), h("p", { text: "No trophies match this filter." })));
  rows.forEach(t => {
    if (multi && t.group !== last) { last = t.group; if (d.groups[t.group]) list.append(h("div.tgroup", { text: d.groups[t.group] })); }
    const when = t.earned ? (fmtWhen(t.at) || "Earned") : "Not earned";
    const row = h("button.trow" + (t.earned ? "" : ".locked") + (t.grade === "P" ? ".plat" : ""), { "data-f": true, "data-key": "t" + t.id, "data-default": first },
      h("div.ti", {}, t.icon ? h("img", { src: trArt(TR.gameId, t.icon), loading: "lazy", draggable: false }) : "?"),
      h("div", {}, h("div.tn", { text: t.name }), h("div.td", { text: t.detail })),
      h("div.ts", {}, cup(t.grade, "lg"), h("span", { text: when })));
    row._options = trophyOptions;
    first = false; list.append(row);
  });
  v.append(list);
  return v;
}
function trophyOptions() {
  openPanel("Trophies", "Filter and sort this list", body => {
    const set = (k, val) => { TR[k] = val; closePanel(); renderView(); };
    body.append(plabel("Show"));
    [["all", "All trophies"], ["earned", "Earned"], ["locked", "Not earned"]].forEach(([k, l]) => body.append(opt(l, "", () => set("filter", k), { sel: TR.filter === k })));
    body.append(plabel("Sort by"));
    [["default", "Default order"], ["recent", "Recently earned"], ["grade", "Grade"]].forEach(([k, l]) => body.append(opt(l, "", () => set("sort", k), { sel: TR.sort === k })));
  });
}
function trophyWidget(g, t) {
  const best = t.earned.P ? "P" : t.earned.G ? "G" : t.earned.S ? "S" : "B";
  const w = h("button.widget", { "data-f": true, "data-key": "trw", "data-go-left": "[data-primary]", "data-go-up": "#btn-trophy", on: { click: () => openTrophyGame(g.id, "games") } },
    h("div.wc", {}, cup(t.earnedCount ? best : "B")),
    h("div.wn", {}, h("small", { text: "Trophies" }), "Collection"),
    h("div.wt", {}, h("div", {}, h("small", { text: "Progress" }), t.progress + "%"), h("div", {}, h("small", { text: "Earned" }), `${t.earnedCount}/${t.count}`)));
  w._options = () => gameOptions(g);
  return w;
}

/* ---------------------------------------------------- background art --- */
let artKey = null;
function setArt(bg, fg) {
  const key = (bg || "") + "|" + (fg || "");
  if (key === artKey) return;
  artKey = key;
  const box = $("#artbox");
  document.body.classList.toggle("plain", !bg);
  const old = $$(".artlayer", box);
  if (bg) {
    const layer = h("div.artlayer", {}, h("img", { src: bg, draggable: false }), fg ? h("img", { src: fg, draggable: false }) : null);
    box.append(layer);
    Promise.all($$("img", layer).map(i => i.decode ? i.decode().catch(() => { }) : 0)).then(() => requestAnimationFrame(() => layer.classList.add("on")));
  }
  old.forEach(o => { o.classList.remove("on"); setTimeout(() => o.remove(), 1000); });
}

/* ------------------------------------------------------------ utils --- */
const hue = s => { let x = 0; for (const c of s) x = (x * 31 + c.charCodeAt(0)) % 360; return x; };
const fmtDate = ts => ts ? new Date(ts).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "-";
const shortTag = t => (t || "").replace(/^KytyPS5-/, "");
const fmtMB = b => (b / 1048576).toFixed(1) + " MB";
const fmtBytes = b => b >= 1073741824 ? (b / 1073741824).toFixed(2) + " GB" : fmtMB(b || 0);
function fmtPlay(sec) { if (!sec) return "Never played"; const m = Math.round(sec / 60); return m < 60 ? `${m} min played` : `${Math.floor(m / 60)} h ${m % 60} min played`; }
function ago(ts) {
  if (!ts) return "Never played";
  const d = (Date.now() / 1000 - ts) / 3600;
  return d < 1 ? "Played just now" : d < 24 ? `Played ${Math.floor(d)} h ago` : `Played ${Math.floor(d / 24)} d ago`;
}
const forkById = id => S.forks.find(f => f.id === id);
const installed = () => S.forks.filter(f => f.current);
function effectiveFork(g) {
  const f = forkById(g.fork) || forkById(S.defaultFork);
  if (f && f.current) return f;
  return installed()[0] || f;
}
const jobFor = fid => S.jobs.find(j => j.fork === fid && ["queued", "downloading", "extracting"].includes(j.state));

function toast(title, msg, err) {
  const t = h("div.toast" + (err ? ".err" : ""), {}, h("b", { text: title }), h("span", { text: msg || "" }));
  $("#toasts").append(t);
  err ? sfx.err() : sfx.done();
  setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 450); }, 5200);
}
const fail = e => toast("Something went wrong", e.message, true);

/* ------------------------------------------------------------ focus --- */
const F = { cur: null };
const gameRunning = () => S.running.some(r => r.alive);
let menuOpen = false, lastMenuReq = null;
const inputBlocked = () => gameRunning() && !menuOpen;  // keys and pad belong to the game unless its menu is open
function scope() {
  if ($("#osk.on")) return $("#osk");
  const ov = $("#overlay.on"); if (ov) return ov;
  if ($("#cc.on")) return $("#cc");
  if ($("#panel.on")) return $("#panel");
  const us = $("#users.on:not(.leave)"); if (us) return us;
  return document;
}
function focusables() {
  const sc = scope();
  return $$("[data-f]", sc).filter(e => e.offsetParent !== null && !e.closest("[hidden]"));
}
function setFocus(el, quiet) {
  if (!el) return;
  if (F.cur && F.cur !== el) F.cur.classList.remove("focus");
  const changed = F.cur !== el;
  F.cur = el; el.classList.add("focus");
  if (!el.classList.contains("tile")) el.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
  if (changed && !quiet) sfx.move();
  if (el.classList.contains("tab") && el.dataset.tab !== tab) { sfx.tab(); switchTab(el.dataset.tab, true); }
  if (el._onfocus) el._onfocus();
}
function focusDefault() {
  const l = focusables();
  setFocus(l.find(e => e.hasAttribute("data-default")) || l.find(e => !e.closest("#top")) || l[0], true);
}
function move(dir) {
  const list = focusables();
  if (!F.cur || !list.includes(F.cur)) return focusDefault();
  const cur = F.cur;
  const key = "go" + dir[0].toUpperCase() + dir.slice(1);
  if (cur.dataset[key]) { const t = $(cur.dataset[key], scope()); if (t) return setFocus(t); }
  if (cur.closest("#top") && dir === "down") return focusDefault();
  const r = cur.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  let best = null, bs = 1e9;
  const inTop = !!cur.closest("#top") && (dir === "left" || dir === "right");
  for (const el of list) {
    if (inTop && !el.closest("#top")) continue;  // like the PS5: left/right on the top bar only walks the bar
    if (el === cur) continue;
    const b = el.getBoundingClientRect(), dx = b.left + b.width / 2 - cx, dy = b.top + b.height / 2 - cy;
    const horiz = dir === "left" || dir === "right";
    const p = horiz ? (dir === "right" ? dx : -dx) : (dir === "down" ? dy : -dy);
    if (p < 4) continue;
    // distance between the facing edges, plus a penalty when the two elements do not line up
    // (wide keys such as the space bar would otherwise lose to a small key one row above)
    const gap = Math.max(0, horiz ? (dir === "right" ? b.left - r.right : r.left - b.right) : (dir === "down" ? b.top - r.bottom : r.top - b.bottom));
    const overlap = horiz ? Math.min(r.bottom, b.bottom) - Math.max(r.top, b.top) : Math.min(r.right, b.right) - Math.max(r.left, b.left);
    const off = Math.abs(horiz ? dy : dx);
    const s = gap + (overlap > 0 ? off * 0.3 : off * 3 + 200);
    if (s < bs) { bs = s; best = el; }
  }
  if (best) setFocus(best);
  else if (dir === "up" && !cur.closest("#top")) setFocus(["#tabs .tab.active", ".backbtn", "#btn-gear"].map(q => $(q)).find(e => e && e.offsetParent !== null));
}
function activate() {
  const el = F.cur; if (!el || el.classList.contains("disabled")) return;
  if (editInput(el)) return;
  sfx.ok(); el.click();
}
function leaveScreen() {
  if (tab === "trophylist") return switchTab(TR.from === "trophies" ? "trophies" : "games");
  switchTab("games");
}
const backBtn = () => h("button.backbtn", { "data-f": true, "data-key": "backbtn", title: "Back", on: { click: () => { sfx.back(); leaveScreen(); } } }, "\u2039");
function openSettings(cat) { if (cat === "forks") setCat = 0; switchTab("settings"); }
function back() {
  if ($("#osk.on")) return oskCancel();
  if ($("#overlay.on")) { if (overlayBack) overlayBack(); return; }
  if ($("#panel.on")) return closePanel();
  if ($("#cc.on")) return closeCC();
  if ($("#users.on:not(.leave)")) return;  // the login screen cannot be dismissed
  sfx.back();
  if (tab === "trophylist" || tab === "settings" || tab === "trophies") return leaveScreen();
  if (F.cur && !F.cur.closest("#top")) setFocus($(`.tab[data-tab="${tab}"]`), true);
}
function options() {
  if ($("#overlay.on") || $("#panel.on") || $("#cc.on")) return;
  const el = F.cur;
  if (el && el._options) { sfx.open(); el._options(); }
}
function nextTab(d) {
  if ($("#overlay.on") || $("#panel.on") || $("#cc.on") || $("#users.on") || $("#osk.on")) return;
  const tabs = $$("#tabs .tab").filter(x => !x.hidden).map(x => x.dataset.tab), i = tabs.indexOf(tab) + d;
  if (i < 0 || i >= tabs.length) return;
  sfx.tab(); switchTab(tabs[i]);
  if (F.cur?.closest("#top")) setFocus($(`.tab[data-tab="${tab}"]`), true);
}

/* keyboard */
addEventListener("keydown", e => {
  if (booting()) { skipBoot(); return; }
  if (inputBlocked()) return;  // keys belong to the game
  if ($("#osk.on")) { oskPhysicalKey(e); return; }
  const typing = e.target.tagName === "INPUT";
  if (typing) {
    if (e.key === "Enter") { e.target.blur(); e.target.dispatchEvent(new Event("change")); e.preventDefault(); if (promptOk && $("#overlay.on")) promptOk(); }
    else if (e.key === "Escape") { e.target.blur(); e.preventDefault(); if (overlayBack && $("#overlay.on")) overlayBack(); }
    return;
  }
  if (e.key === "F11" && window.pywebview?.api) { e.preventDefault(); window.pywebview.api.toggle_fullscreen(); return; }
  const map = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" };
  if (map[e.key]) { e.preventDefault(); move(map[e.key]); }
  else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(); }
  else if (e.key === "Escape" || e.key === "Backspace") { e.preventDefault(); back(); }
  else if (e.key === "Tab" || e.key === "ContextMenu" || e.key === "F2" || e.key.toLowerCase() === "o") { e.preventDefault(); options(); }
  else if (e.key.toLowerCase() === "q") nextTab(-1);
  else if (e.key.toLowerCase() === "e") nextTab(1);
  else if (e.key === "F1" || e.key.toLowerCase() === "c") { e.preventDefault(); toggleCC(); }
});
/* mouse */
document.addEventListener("mouseover", e => {
  const el = e.target.closest?.("[data-f]");
  if (el && scope().contains(el) && el !== F.cur) setFocus(el);
});
document.addEventListener("contextmenu", e => { e.preventDefault(); const el = e.target.closest("[data-f]"); if (el) setFocus(el, true); options(); });
let lastMouse = 0;
addEventListener("mousemove", () => { lastMouse = Date.now(); document.body.classList.remove("hide-cursor"); });
setInterval(() => { if (Date.now() - lastMouse > 4000 && F.cur) document.body.classList.add("hide-cursor"); }, 1000);

/* gamepad: handles the standard mapping plus the raw DualShock/DualSense layout */
const pad = { prev: {}, next: {}, shown: null };
function padMap(gp) {
  if (gp.mapping === "standard") return { ok: 0, back: 1, sq: 2, opt: 3, start: 9, l1: 4, r1: 5 };
  if (/054c/i.test(gp.id)) return { ok: 1, back: 2, sq: 0, opt: 3, start: 9, l1: 4, r1: 5 };  // Sony, non-standard
  return { ok: 0, back: 1, sq: 2, opt: 3, start: 7, l1: 4, r1: 5 };
}
function hatDir(v) {  // hat switch exposed as axis 9: -1 up, then clockwise in 2/7 steps, 3.29 = neutral
  if (typeof v !== "number" || v > 1.1) return {};
  const i = Math.round((v + 1) * 3.5);
  return { up: i === 0 || i === 1 || i === 7, right: i >= 1 && i <= 3, down: i >= 3 && i <= 5, left: i >= 5 && i <= 7 };
}
const padSeen = new Set();
const padName = gp => gp.id.replace(/\(.*\)/, "").trim().slice(0, 28);
function showPad(gp) {
  const id = gp ? padName(gp) : null;
  if (id === pad.shown) return;
  pad.shown = id;
  const el = $("#pad"); el.textContent = gp ? "🎮" : ""; el.title = id || "";
  if (gp && !padSeen.has(id)) { padSeen.add(id); toast("Controller connected", id); }  // once per device per session
}
// Real controllers only: stray HID devices (mice, keyboards, virtual pads) also show up as gamepads.
const isController = p => p.mapping === "standard" || /054c|045e|057e/i.test(p.id);
function padLoop() {
  const all = [...(navigator.getGamepads?.() || [])].filter(p => p && p.connected);
  const pads = all.filter(isController);
  const use = pads.length ? pads : all;
  showPad(use[0] || null);
  if (!use.length) return;
  if (inputBlocked()) { pad.prev = {}; return; }  // the controller belongs to the game
  const now = performance.now(), want = { left: 0, right: 0, up: 0, down: 0, ok: 0, back: 0, opt: 0, l1: 0, r1: 0, cc: 0, sq: 0, tri: 0, start: 0 };
  for (const gp of use) {
    const b = i => !!gp.buttons[i]?.pressed, m = padMap(gp), hat = hatDir(gp.axes[9]);
    want.left |= b(14) || gp.axes[0] < -.55 || !!hat.left; want.right |= b(15) || gp.axes[0] > .55 || !!hat.right;
    want.up |= b(12) || gp.axes[1] < -.55 || !!hat.up; want.down |= b(13) || gp.axes[1] > .55 || !!hat.down;
    want.ok |= b(m.ok); want.back |= b(m.back); want.opt |= b(m.opt) || b(m.start); want.l1 |= b(m.l1); want.r1 |= b(m.r1); want.cc |= b(16) || b(17) || b(8); want.sq |= b(m.sq); want.tri |= b(3); want.start |= b(m.start);
  }
  for (const d of ["left", "right", "up", "down"]) {
    if (!want[d]) { pad.next[d] = 0; continue; }
    if (now >= (pad.next[d] || 0)) { if (booting()) skipBoot(); else move(d); pad.next[d] = now + (pad.next[d] ? 120 : 380); }
  }
  const edge = (k, fn) => { if (want[k] && !pad.prev[k]) fn(); pad.prev[k] = want[k]; };
  edge("ok", () => { if (booting()) skipBoot(); else if (document.activeElement?.tagName !== "INPUT") activate(); });
  const osk = () => !!$("#osk.on");
  edge("sq", () => { if (osk()) oskBackspace(); });
  edge("tri", () => { if (osk()) oskType(" "); else options(); });
  edge("start", () => { if (osk()) oskDone(); else options(); });
  edge("back", back); edge("cc", () => { if (!osk()) toggleCC(); });
  edge("l1", () => { if (!osk()) nextTab(-1); }); edge("r1", () => { if (osk()) oskShift(); else nextTab(1); });
}
addEventListener("gamepadconnected", () => { /* polling picks it up */ });

/* ------------------------------------------------------- control center -- */
let ccOpener = null;
function openCC() {
  if ($("#overlay.on") || $("#panel.on") || $("#cc.on") || $("#users.on") || $("#osk.on") || booting() || inputBlocked()) return;
  ccOpener = F.cur;
  const go = fn => () => { closeCC(true); fn(); };
  const item = (ic, label, fn, key) => h("button.ccb", { "data-f": true, "data-key": key, "data-default": key === "cc-games", on: { click: go(fn) } }, h("div.ic", {}, ic), h("em", { text: label }));
  const live = S.running.find(r => r.alive);
  if (live) {
    $("#cc").replaceChildren(h("div.ccbar", {},
      item("\u25b6", "Back to game", resumeGame, "cc-games"),
      item("\u2715", "Close game", () => api("/api/stop", { run: live.id }).catch(fail), "cc-stop"),
      item("\u2699", "Settings", () => { menuOpen = true; switchTab("settings"); }, "cc-settings"),
      item(cup("G"), "Trophies", () => { TR.from = "games"; menuOpen = true; switchTab("trophies"); }, "cc-trophies")));
    $("#cc").classList.add("on"); $("#scrim").classList.add("on"); sfx.open();
    setTimeout(() => setFocus($('#cc [data-key="cc-games"]'), true), 30);
    return;
  }
  $("#cc").replaceChildren(h("div.ccbar", {},
    item("\u2302", "Home", () => switchTab("games"), "cc-games"),
    item(cup("G"), "Trophies", () => { TR.from = "games"; switchTab("trophies"); }, "cc-trophies"),
    item(h("div.av", { style: "width:100%;height:100%" }, avatarEl(me())), "Switch User", openSwitchUser, "cc-user"),
    item("\u2193", "Forks", () => openSettings("forks"), "cc-forks"),
    item("\u2699", "Settings", () => switchTab("settings"), "cc-settings"),
    item("\u23fb", "Power", async () => { if (await confirmBox("Exit KytyPS5 Studio?", "Games that are running will keep running.", "Exit")) api("/api/quit", {}).then(() => window.close()).catch(() => { }); }, "cc-power")));
  $("#cc").classList.add("on"); $("#scrim").classList.add("on"); sfx.open();
  setTimeout(() => setFocus($('#cc [data-key="cc-games"]'), true), 30);
}
function closeCC(silent) {
  if (!$("#cc.on")) return;
  $("#cc").classList.remove("on"); $("#scrim").classList.remove("on"); if (!silent) sfx.back();
  F.cur = null;
  setTimeout(() => { if (silent) return focusDefault(); ccOpener?.isConnected ? setFocus(ccOpener, true) : focusDefault(); }, 0);
}
function toggleCC() { $("#cc.on") ? closeCC() : openCC(); }

/* ------------------------------------------------------ panel / overlay */
let overlayBack = null, promptOk = null;
let panelOpener = null;
function openPanel(title, sub, builder) {
  panelOpener = F.cur;
  const body = $("#panel-body"); body.replaceChildren(h("h3", { text: title }), h("div.psub", { text: sub || "" }));
  builder(body);
  $("#panel").classList.add("on"); $("#scrim").classList.add("on");
  setTimeout(() => focusDefault(), 30);
}
function closePanel() {
  if (!$("#panel.on")) return;
  $("#panel").classList.remove("on"); $("#scrim").classList.remove("on"); sfx.back();
  F.cur = null;
  setTimeout(() => {
    const k = panelOpener?.dataset?.key;
    const t = panelOpener?.isConnected ? panelOpener : k && $$("[data-f]").find(e => e.dataset.key === k);
    t ? setFocus(t, true) : focusDefault();
  }, 0);
}
$("#scrim").addEventListener("click", () => { closePanel(); closeCC(); });
function opt(label, right, fn, { sel, disabled, def } = {}) {
  const el = h("button.opt" + (sel ? ".sel" : "") + (disabled ? ".disabled" : ""), { "data-f": true, "data-default": def || sel || null, on: { click: () => !disabled && fn() } },
    h("span", { text: label }), right ? h("span.r", { text: right }) : null);
  return el;
}
const plabel = t => h("div.plabel", { text: t });

function showOverlay(node, onBack) {
  const ov = $("#overlay"); ov.replaceChildren(node); ov.classList.add("on");
  overlayBack = onBack || null; setTimeout(focusDefault, 30);
}
function hideOverlay() { $("#overlay").classList.remove("on"); overlayBack = null; promptOk = null; F.cur = null; setTimeout(() => focusDefault(), 0); }
function ask(title, text, placeholder, okLabel, initial) {
  return openKeyboard({ title, hint: text, placeholder, okLabel, value: initial || "", max: 16 * 8 }).then(v => (v && v.trim()) || null);
}

/* --------------------------------------------------------- top bar ---- */
function renderTop() {
  const av = $("#avatar"); if (av) av.replaceChildren(avatarEl(me()));
  document.body.classList.toggle("insettings", ["settings", "trophies", "trophylist"].includes(tab));
  $$(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === tab));
  for (const [id, extension] of pluginTabs) {
    const button = $(`.tab[data-tab="${id}"]`);
    if (button) {
      try { button.hidden = extension.visible ? !extension.visible() : false; }
      catch (_) { button.hidden = true; }
    }
  }
  const f = forkById(S.defaultFork), chip = $("#fork-chip");
  if (!f) return;
  const st = !f.current ? "bad" : f.updateAvailable ? "warn" : "good";
  chip.replaceChildren(h("i.dot." + st), h("span", {}, "Fork  ", h("b", { text: f.name })),
    h("span", { text: f.current ? shortTag(f.current.tag).slice(0, 26) : "not installed" }));
}
$("#btn-trophy .cup").replaceWith(cup("G"));
function tickClock() { const t = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); $("#clock").textContent = t; const u = $("#uclock"); if (u) u.textContent = t; }
setInterval(tickClock, 5000); tickClock();

function switchTab(t, fromFocus) {
  tab = t; renderTop(); renderView();
  if (t === "trophies") loadTrophies().then(() => { if (tab === "trophies") renderView(); });
  if (!fromFocus) setFocus($(`.tab[data-tab="${t}"]`), true);
}
$$(".tab").forEach(t => t.addEventListener("click", () => { sfx.tab(); switchTab(t.dataset.tab); }));
$("#fork-chip").addEventListener("click", () => openSettings("forks"));
$("#avatar").addEventListener("click", openProfile);
$("#btn-gear").addEventListener("click", () => switchTab("settings"));
$("#btn-trophy").addEventListener("click", () => { TR.from = "games"; switchTab("trophies"); });
function confirmBox(title, text, okLabel) {
  return new Promise(res => {
    const done = v => { hideOverlay(); res(v); };
    showOverlay(h("div.ov", {}, h("h2", { text: title }), h("p", { text }),
      h("div", { style: "display:flex;gap:14px;justify-content:center" },
        h("button.pill.ghost", { "data-f": true, "data-default": true, on: { click: () => done(false) } }, "Cancel"),
        h("button.pill", { "data-f": true, on: { click: () => done(true) } }, okLabel))), () => done(false));
  });
}
$("#btn-power").addEventListener("click", async () => {
  if (await confirmBox("Exit KytyPS5 Studio?", "Games that are running will keep running.", "Exit")) api("/api/quit", {}).then(() => window.close()).catch(() => { });
});

/* ------------------------------------------------------------ views --- */
const view = () => $("#screen");
function chooseZarConversion(gameTitle) {
  return new Promise(resolve => {
    const done = value => { hideOverlay(); resolve(value); };
    showOverlay(h("div.ov.zar-choice", {},
      h("h2", { text: "Convert game to ZAR" }),
      h("p", { text: `Choose what to do with ${gameTitle} after conversion.` }),
      h("div.zar-choice-options", {},
        h("button.pill", { "data-f": true, "data-default": true, on: { click: () => done(false) } }, "Convert only"),
        h("button.pill.ghost", { "data-f": true, on: { click: () => done(true) } }, "Convert and remove folder"),
        h("button.pill.ghost", { "data-f": true, on: { click: () => done(null) } }, "Cancel"))),
      () => done(null));
  });
}
function renderView() {
  const keep = F.cur?.dataset?.key;
  window._lastKey = keep;
  const render = { games: viewGames, settings: viewSettings, trophies: viewTrophies, trophylist: viewTrophyGame }[tab] || pluginTabs.get(tab)?.render;
  if (!render) { tab = "games"; renderTop(); return renderView(); }
  const v = render();
  view().replaceChildren(v);
  if (tab !== "games") setArt(null);
  if (tab === "games") updateHero();
  const again = keep && $$("[data-f]", view()).find(e => e.dataset.key === keep);
  if (again && !F.cur?.classList.contains("tab")) setFocus(again, true);
  else if (!F.cur?.classList.contains("tab")) focusDefault();
}

/* ---- games ---- */
function viewGames() {
  const v = h("div.view");
  const track = h("div.track#track");
  if (selTile >= games.length) selTile = Math.max(0, games.length - 1);
  games.forEach((g, i) => {
    const run = S.running.find(r => r.game === g.id && r.alive);
    const cell = h("button.cell" + (i === selTile ? ".sel" : ""), {
      "data-f": true, "data-default": i === selTile, "data-key": "g" + g.id,
      "data-go-up": "#tabs .tab.active", "data-go-down": ".hero [data-primary]", on: { click: () => playGame(g) },
    }, h("div.tile", {}, g.icon ? h("img", { src: art(g.id, "icon"), draggable: false })
      : h("div.ph", { style: `--h:${hue(g.title)}`, text: g.title.trim().slice(0, 2).toUpperCase() }),
      run ? h("span.live", { text: "RUNNING" }) : null),
      h("div.label", {}, g.title, h("i", { text: "  |  PS5" })));
    cell._onfocus = () => { selTile = i; $$(".cell.sel").forEach(t => t.classList.remove("sel")); cell.classList.add("sel"); updateHero(); };
    cell._options = () => gameOptions(g);
    track.append(cell);
  });
  const add = h("button.cell.add", { "data-f": true, "data-key": "add", "data-default": !games.length, "data-go-up": "#tabs .tab.active", on: { click: addLibrary } },
    h("div.tile", { text: "+" }), h("div.label", { text: "Add game folder" }));
  add._onfocus = () => { $$(".cell.sel").forEach(t => t.classList.remove("sel")); add.classList.add("sel"); selTile = games.length; updateHero(); };
  track.append(add);
  v.append(h("div.hero#hero"), h("div#wslot"), h("div.rowwrap", {}, track));
  return v;
}
function shiftTrack() {
  const t = $("#track"); if (t) t.style.transform = `translateX(${16.8 * 10 / 10}rem) translateX(${-selTile * 12}rem)`;
}
let heroFor = null;
function updateHero() {
  shiftTrack();
  const hero = $("#hero"), slot = $("#wslot"); if (!hero) return;
  const g = games[selTile];
  if (!g) {
    setArt(null); heroFor = null; slot.replaceChildren();
    hero.className = "empty"; hero.replaceChildren(
      h("h1", { text: games.length ? "Add more games" : "Your library is empty" }),
      h("p", { text: "Point Studio at the folder that holds your game dumps (each game folder contains an sce_sys directory). Games appear here with their cover art." }));
    return;
  }
  setArt(g.bg ? art(g.id, "bg") : null, g.fg ? art(g.id, "fg") : null);
  const pk = F.cur && (hero.contains(F.cur) || slot.contains(F.cur)) ? F.cur.dataset.key : null;
  const f = effectiveFork(g);
  const run = S.running.find(r => r.game === g.id && r.alive);
  const label = run ? "Resume" : f && f.current ? "Play" : "Install a fork";
  const fresh = heroFor !== g.id; heroFor = g.id;
  const play = h("button.pill", { "data-f": true, "data-primary": true, "data-key": "play", "data-go-up": ".cell.sel", "data-go-down": ".cell.sel", on: { click: () => playGame(g) } }, label);
  const optBtn = h("button.pill.ghost.round-btn", { "data-f": true, "data-key": "opts", "data-go-up": ".cell.sel", title: "Options", on: { click: () => gameOptions(g) } }, "\u00b7\u00b7\u00b7");
  hero.className = "hero" + (fresh ? " enter" : "");
  hero.replaceChildren(
    g.fg ? null : h("h1", { text: g.title }),
    h("div.meta", {}, f ? h("span.chip", {}, h("i.dot." + (f.current ? "good" : "bad")), f.name + (f.current ? "  \u00b7  " + shortTag(f.current.tag).slice(0, 22) : "  \u00b7  not installed")) : null,
      h("span", { text: ago(g.lastPlayed) })),
    h("div.actions", {}, play, optBtn));
  play._options = optBtn._options = () => gameOptions(g);
  const tg = TR.byGame[g.id];
  slot.replaceChildren(...(tg ? [trophyWidget(g, tg)] : []));
  if (pk) { const el = $$("[data-f]").find(e => e.dataset.key === pk); if (el && el !== F.cur) setFocus(el, true); }
}

async function playGame(g) {
  const f = effectiveFork(g);
  if (!f || !f.current) { toast("No fork installed", "Install a KytyPS5 build first.", true); openSettings("forks"); return; }
  const live = S.running.find(r => r.game === g.id && r.alive);
  try {
    const run = live || await api("/api/launch", { game: g.id, fork: f.id });
    showRunning(run, g);
  } catch (e) { fail(e); }
}
function resumeGame() {
  menuOpen = false;
  api("/api/resume", {}).catch(fail);
  const run = S.running.find(r => r.alive), g = run && games.find(x => x.id === run.game);
  if (g) showRunning(run, g);
}
function openGameMenu() {
  const run = S.running.find(r => r.alive); if (!run) return;
  const g = games.find(x => x.id === run.game) || { id: run.game, title: "Game", icon: false };
  menuOpen = true; closePanel(); closeCC(true);
  if (tab !== "games") switchTab("games");
  showRunning(run, g, true);
}
let shownRun = null;
function showRunning(run, g, menu) {
  shownRun = run.id;
  const f = forkById(run.fork);
  const stop = () => api("/api/stop", { run: run.id }).catch(fail);
  const node = h("div.ov", {}, h("div.icon", {}, g.icon ? h("img", { src: art(g.id, "icon") }) : null),
    h("h2", { text: g.title }),
    h("p", {}, h("span.spinner"), `Running on ${f ? f.name : run.fork} · ${run.tag}`, h("br"), h("small", { text: "Studio steps aside while the game runs and returns when it closes." })),
    menu
      ? h("div", { style: "display:flex;gap:1.6rem;justify-content:center" },
        h("button.pill", { "data-f": true, "data-default": true, on: { click: resumeGame } }, "Resume"),
        h("button.pill.ghost", { "data-f": true, on: { click: () => { hideOverlay(); switchTab("settings"); } } }, "Settings"),
        h("button.pill.ghost", { "data-f": true, on: { click: stop } }, "Close game"))
      : h("button.pill.ghost", { "data-f": true, "data-default": true, on: { click: stop } }, "Close game"));
  showOverlay(node, menu ? resumeGame : () => { });
}
async function showCrash(run, g) {
  let text = "";
  try { text = (await api("/api/log?name=" + encodeURIComponent(run.log))).text; } catch (e) { /* ignore */ }
  sfx.err();
  showOverlay(h("div.ov", {}, h("h2", { text: "The game closed unexpectedly" }),
    h("p", { text: `${g.title} exited with code ${run.exit} shortly after starting. Try another fork from the options menu.` }),
    h("div.logbox", { text }),
    h("button.pill", { "data-f": true, "data-default": true, on: { click: hideOverlay } }, "OK")), hideOverlay);
}

function gameOptions(g) {
  const ov = () => games.find(x => x.id === g.id)?.overrides || {};
  openPanel(g.title, g.titleId || "", body => {
    if (TR.byGame[g.id]) body.append(plabel("Trophies"), opt("View trophies", `${TR.byGame[g.id].progress}%`, () => { closePanel(); openTrophyGame(g.id, "games"); }));
    body.append(plabel("Play with"));
    const eff = effectiveFork(g);
    S.forks.forEach(f => {
      body.append(opt(f.name + (f.badge === "Original" ? "  (original)" : ""), f.current ? f.current.tag.slice(0, 24) : "Not installed", async () => {
        try { await api("/api/game/set", { game: g.id, fork: f.id }); g.fork = f.id; closePanel(); refreshGames(); toast("Fork selected", `${g.title} will start on ${f.name}`); } catch (e) { fail(e); }
      }, { sel: eff && eff.id === f.id, disabled: !f.current }));
    });
    const ef = effectiveFork(g);
    body.append(plabel("Launch settings for this game"));
    const cycle = (label, key, values) => {
      const cur = () => ov()[key];
      const text = v => v === undefined || v === "" ? "Default" : v === true ? "On" : v === false ? "Off" : v;
      const el = opt(label, text(cur()), async () => {
        const i = values.findIndex(v => v === (cur() ?? undefined));
        const nv = values[(i + 1) % values.length];
        const o = { ...ov() }; if (nv === undefined) delete o[key]; else o[key] = nv;
        try { await api("/api/game/set", { game: g.id, overrides: o }); g.overrides = o; const x = games.find(q => q.id === g.id); if (x) x.overrides = o; $(".r", el).textContent = text(nv); } catch (e) { fail(e); }
      });
      return el;
    };
    body.append(cycle("Fullscreen", "fullscreen", [undefined, true, false]),
      cycle("Present mode", "presentMode", [undefined, "Mailbox", "Fifo", "Immediate"]),
      cycle("Shader cache", "shaderCache", [undefined, true, false]),
      cycle("HDR", "hdr", [undefined, true, false]));
    if (ef && ef.current) body.append(opt(`${ef.name} settings\u2026`, "DLSS, audio, controller\u2026", () => { closePanel(); forkSettingsPanel(ef, g); }));
    const patchBox = h("div");
    body.append(patchBox);
    api("/api/patches?game=" + g.id).then(pl => {
      if (!pl.patches.length) return;
      patchBox.append(plabel("Game patch"), opt("None", "", async () => { try { await api("/api/game/patch", { game: g.id, patch: null }); closePanel(); toast("Patch off", g.title); } catch (e) { fail(e); } }, { sel: !pl.selected }));
      pl.patches.forEach(p => patchBox.append(opt(p.name, p.compatible ? p.version : `for game ${p.gameVersion}`, async () => { try { await api("/api/game/patch", { game: g.id, patch: p.id }); closePanel(); toast("Patch on", `${p.name} for ${g.title}`); } catch (e) { fail(e); } }, { sel: pl.selected === p.id, disabled: !p.compatible })));
      const cur = pl.patches.find(p => p.id === pl.selected);
      if (cur) patchBox.append(opt("Patch options\u2026", "Resolution, frame cap, fixes", () => { closePanel(); patchOptionsPanel(g, cur.id, cur.name); }));
    }).catch(() => { });
    body.append(plabel("Shaders"));
    const shaderBox = h("div");
    body.append(shaderBox);
    api("/api/shaders?game=" + g.id).then(si => {
      shaderBox.append(opt("Shader cache", `${si.megabytes} MB \u00b7 ${si.fork}`, () => { }, { disabled: false }));
      shaderBox.append(opt("Preload while playing", si.auto ? "Automatic" : "Not supported by this fork", () => { }, { disabled: !si.auto }));
      if (si.precompile) {
        shaderBox.append(opt("Precompile shaders (quick)", "", async () => { closePanel(); try { await api("/api/precompile", { game: g.id, mode: "quick" }); toast("Shader precompile started", "A console window shows the progress."); } catch (e) { fail(e); } }));
        shaderBox.append(opt("Precompile shaders (full)", "hours", async () => { closePanel(); try { await api("/api/precompile", { game: g.id, mode: "full" }); toast("Full precompile started", "Leave it running, it can take hours."); } catch (e) { fail(e); } }));
      }
      if (si.files) shaderBox.append(opt("Clear shader cache", "", async () => { closePanel(); if (await confirmBox("Clear the shader cache?", "Shaders are rebuilt the next time you play, which causes stutter at first.", "Clear")) try { const r = await api("/api/shaders/clear", { game: g.id }); toast("Shader cache cleared", `${r.removed} file(s) removed`); } catch (e) { fail(e); } }));
    }).catch(() => { });
    body.append(plabel("Game"));
    body.append(opt("Open game folder", "", () => api("/api/open", { game: g.id }).catch(fail)),
      opt("Open logs folder", "", () => api("/api/open", { what: "logs" }).catch(fail)));
  });
}

async function addLibrary() {
  try {
    toast("Choose a folder", "A folder picker opened on your desktop.");
    await api("/api/library/add", {}); await refreshAll();
  } catch (e) {
    const p = await ask("Add game folder", "Paste the full path to your games folder.", "D:\\Games\\PS5");
    if (p) try { await api("/api/library/add", { path: p }); await refreshAll(); } catch (e2) { fail(e2); }
  }
}

/* ---- forks ---- */
let forkSel = null;
const forkStat = f => !f.current ? ["bad", "Not installed"] : f.updateAvailable ? ["warn", "Update available"] : f.checkError ? ["", "Could not check for updates"] : ["good", "Up to date"];
function forkPanel(f) {
  openPanel(f.name, forkStat(f)[1], body => {
    if (f.tagline && f.community) body.append(h("p.pdesc", { text: f.tagline }));
    if (f.current) body.append(h("p.pdesc", { text: "Installed: " + shortTag(f.current.tag) }));
    if (f.latest) body.append(h("p.pdesc", { text: `Latest: ${shortTag(f.latest.tag)} \u00b7 ${fmtDate(f.latest.published)}` }));
    body.append(opt(!f.current ? "Install" : f.updateAvailable ? "Update" : "Reinstall", f.latest ? shortTag(f.latest.tag).slice(0, 22) : "", () => { closePanel(); install(f, null); }, { def: true, disabled: !f.latest }),
      opt("Fork settings\u2026", "Options of this build", () => { closePanel(); forkSettingsPanel(f); }, { disabled: !f.current }),
      opt("Use as default fork", S.defaultFork === f.id ? "Default \u2713" : "", async () => { closePanel(); try { await api("/api/fork/default", { fork: f.id }); await refreshAll(); } catch (e) { fail(e); } }));
  });
}
/* ---- settings of the fork itself (read from its own --help, so any fork works) ---- */
async function forkSettingsPanel(f, g) {
  let d;
  try { d = await api("/api/forkopts", { fork: f.id, game: g ? g.id : undefined }); } catch (e) { return fail(e); }
  const vals = { ...(g ? d.game : d.fork) };
  const sel = { ...(g ? d.gameSel : d.forkSel) };
  let env = g ? d.gameEnv : d.forkEnv;
  const base = g ? d.fork : {};
  const baseSel = g ? d.forkSel : {};
  const fmt = v => v === true ? "On" : v === false ? "Off" : String(v);
  const shown = o => vals[o.flag] !== undefined ? fmt(vals[o.flag])
    : base[o.flag] !== undefined ? `Default (${fmt(base[o.flag])})` : o.default ? `Default (${o.default})` : "Default";
  const save = async () => {
    try { await api("/api/forkopts/set", { fork: f.id, game: g ? g.id : undefined, args: vals, sel, env }); return true; } catch (e) { fail(e); return false; }
  };
  openPanel(`${f.name} settings`,g ? `Only for ${g.title}. Default = the fork-wide value.` : (d.tag ? shortTag(d.tag) : ""), body => {
    if (!d.installed) { body.append(h("p.pdesc", { text: "Install this fork first. Its settings are read from the build itself." })); return; }
    if (!d.options.length) body.append(h("p.pdesc", { text: "This build lists no extra options in --help. You can still set environment variables below." }));
    const fts = [...d.features.filter(x => !x.auto), ...d.features.filter(x => x.auto)];
    fts.forEach((ft, i) => {
      if (i === 0 && !ft.auto) body.append(plabel("Fork features"));
      if (ft.auto && (i === 0 || !fts[i - 1].auto)) body.append(plabel("Documented by this build"));
      const fshown = () => sel[ft.id] !== undefined ? fmt(sel[ft.id]) : baseSel[ft.id] !== undefined ? `Default (${fmt(baseSel[ft.id])})` : "Default";
      const el = opt(ft.label, fshown(), async () => {
        const old = sel[ft.id];
        if (ft.kind === "num" || ft.kind === "text") {
          const v = await openKeyboard({ title: ft.label, hint: ft.kind === "num" ? `${ft.min}-${ft.max}. Empty = default.` : `${ft.key}. Empty = default.`, okLabel: "Set", value: old !== undefined ? String(old) : "", max: ft.kind === "num" ? 8 : 100, numeric: ft.kind === "num" });
          if (v === null) return;
          if (v.trim() === "") delete sel[ft.id]; else sel[ft.id] = v.trim();
        } else {
          const cycle = ft.kind === "choice" ? [undefined, ...ft.choices.map(c => c.label)] : ft.off ? [undefined, true, false] : [undefined, true];
          const nv = cycle[(cycle.indexOf(sel[ft.id]) + 1) % cycle.length];
          if (nv === undefined) delete sel[ft.id]; else sel[ft.id] = nv;
        }
        if (!(await save())) { if (old === undefined) delete sel[ft.id]; else sel[ft.id] = old; }
        $(".r", el).textContent = fshown();
      });
      body.append(el, h("div.odesc", { text: ft.desc }));
    });
    let lastGroup = null;
    d.options.forEach(o => {
      if (o.group !== lastGroup) { body.append(plabel(o.group)); lastGroup = o.group; }
      const el = opt(o.label, shown(o), async () => {
        if (o.kind === "num" || o.kind === "text") {
          const v = await openKeyboard({ title: o.label, hint: `${o.flag}${o.min !== undefined ? ` (${o.min}-${o.max})` : ""}. Empty = default.`, placeholder: o.default || "", okLabel: "Set", value: vals[o.flag] !== undefined ? String(vals[o.flag]) : "", max: 200, numeric: o.kind === "num" });
          if (v === null) return;
          const old = vals[o.flag]; if (v.trim() === "") delete vals[o.flag]; else vals[o.flag] = v.trim();
          if (!(await save())) { if (old === undefined) delete vals[o.flag]; else vals[o.flag] = old; }
        } else {
          const cycle = o.kind === "switch" ? [undefined, true, false] : [undefined, ...o.choices];
          const nv = cycle[(cycle.indexOf(vals[o.flag]) + 1) % cycle.length];
          const old = vals[o.flag]; if (nv === undefined) delete vals[o.flag]; else vals[o.flag] = nv;
          if (!(await save())) { if (old === undefined) delete vals[o.flag]; else vals[o.flag] = old; }
        }
        $(".r", el).textContent = shown(o);
      });
      body.append(el, h("div.odesc", { text: o.desc }));
    });
    body.append(plabel("Environment variables"));
    const envText = () => env ? env.split(";").join("  ") : "None";
    const envEl = opt("Variables", envText(), async () => {
      const v = await openKeyboard({ title: "Environment variables", hint: "NAME=value, separated by ;  e.g. KYTY_DLSS=1;KYTY_FOO=2. Empty = none.", placeholder: "NAME=value;NAME2=value", okLabel: "Set", value: env, max: 400 });
      if (v === null) return;
      const old = env; env = v.trim(); if (!(await save())) env = old;
      $(".r", envEl).textContent = envText();
    });
    body.append(envEl, h("div.odesc", { text: "For options a fork only offers as an environment variable. Applied on top of the fork's own preset." }));
    if (d.preset.length) body.append(h("div.odesc", { text: `This build's preset sets: ${d.preset.slice(0, 40).join(", ")}${d.preset.length > 40 ? "\u2026" : ""}` }));
    if (Object.keys(vals).length || Object.keys(sel).length || env) body.append(plabel("Reset"), opt("Reset to default", "", async () => {
      for (const k of Object.keys(vals)) delete vals[k]; for (const k of Object.keys(sel)) delete sel[k]; env = "";
      if (await save()) { closePanel(); toast("Settings reset", g ? g.title : f.name); }
    }));
  });
}

async function addFork() {
  const repo = await ask("Add a fork", "Enter the GitHub repository (owner/repo or full URL). It must publish Windows zip builds in its releases.", "owner/KytyPS5", "Add");
  if (repo) try { await api("/api/fork/add", { repo }); await refreshAll(); toast("Fork added", repo); } catch (e) { fail(e); }
}
async function install(f, tag) {
  try { await api("/api/install", { fork: f.id, tag }); await refreshState(); } catch (e) { fail(e); }
}
function versionsPanel(f) {
  openPanel(f.name, "Choose a version to install or switch to", body => {
    const inst = new Map(f.versions.map(v => [v.tag, v]));
    const tags = [...new Set([...f.releases.map(r => r.tag), ...f.versions.map(v => v.tag)])];
    tags.forEach(tag => {
      const i = inst.get(tag), isCur = f.current && f.current.tag === tag;
      const rel = f.releases.find(r => r.tag === tag);
      body.append(opt(tag.length > 30 ? tag.slice(0, 30) + "…" : tag,
        isCur ? "Active" : i ? "Switch" : "Install" + (rel && rel.prerelease ? " (pre)" : ""), async () => {
          closePanel();
          try {
            if (i) { await api("/api/use", { fork: f.id, tag }); await refreshAll(); toast("Version switched", `${f.name} now uses ${tag}`); }
            else await install(f, tag);
          } catch (e) { fail(e); }
        }, { sel: isCur }));
    });
    if (!tags.length) body.append(h("div.psub", { text: f.checkError || "No releases found." }));
  });
}

/* ----------------------------------------------- community forks & patches --- */
let CM = null, cmLoading = false;
async function loadCommunity(refresh) {
  CM = refresh ? await api("/api/community/refresh", {}) : await api("/api/community");
  if (CM.error) toast("Catalog not updated", CM.error, true);
  return CM;
}
const shortDesc = d => (d.length > 112 ? d.slice(0, 109) + "\u2026" : d);
const nameOf = n => n.split(" \u00b7 ")[0];
function communityForkPanel(e) {
  openPanel(nameOf(e.name), e.author ? `by ${e.author}` : "Community fork", body => {
    body.append(h("p.pdesc", { text: e.description }));
    if (e.games.length) body.append(h("p.pdesc", { text: "Games: " + e.games.join(", ") }));
    if (e.notes) body.append(h("p.pdesc", { text: "Note: " + e.notes }));
    body.append(h("p.pdesc", { text: e.latest ? `Latest: ${shortTag(e.latest.tag)} \u00b7 ${fmtDate(e.latest.published)} \u00b7 ${fmtMB(e.latest.size)}${e.latest.prerelease ? " \u00b7 pre-release" : ""}` : (e.checkError || "Release information is not loaded yet. Use \"Refresh catalog and releases\".") }));
    if (e.installed) body.append(h("p.pdesc", { text: "Installed: " + shortTag(e.installed) }));
    if (!e.installed) body.append(h("p.pwarn", { text: `Third-party build from github.com/${e.repo}. Studio downloads its Windows release; install only builds you trust.` }));
    const go = async () => {
      closePanel();
      if (!e.installed && !(await confirmBox(`Install ${nameOf(e.name)}?`, `This is a third-party build from github.com/${e.repo}, not made by Studio or the KytyPS5 team. Install only builds you trust.`, "Install"))) return;
      try { const r = await api("/api/community/fork", { id: e.id }); await refreshState(); await install({ id: r.fork }, null); toast("Installing " + nameOf(e.name), "Progress is shown in Settings, Forks."); await loadCommunity(); } catch (x) { fail(x); }
    };
    body.append(opt(!e.installed ? "Install" : e.updateAvailable ? "Update" : "Reinstall", "", go, { disabled: !e.latest, def: true }));
    if (e.forkId) body.append(opt("Remove from Studio", "Saves are kept", async () => {
      closePanel();
      try { await api("/api/fork/remove", { fork: e.forkId }); await refreshAll(); await loadCommunity(); toast("Removed", nameOf(e.name)); } catch (x) { fail(x); }
    }));
  });
}
function patchOptionsPanel(g, pid, pname) {
  openPanel("Patch options", pname || "", async body => {
    let data;
    try { data = await api("/api/patch/mods?id=" + encodeURIComponent(pid)); } catch (e) { fail(e); closePanel(); return; }
    body.append(h("p.pdesc", { text: `${data.mods.length} options. Resolution and frame-cap options work as a choice of one. Changes apply the next time you start the game.` }));
    const groupOf = n => (n.match(/^(Precise frame cap|Fixed render resolution)/) || [])[1];
    const rows = [];
    const label = m => m.name.replace(/^\[(\w[\w ]*)\]\s*/, "$1 \u00b7 ");
    const draw = () => rows.forEach(({ el, m }) => { el.classList.toggle("sel", m.enabled); $(".r", el).textContent = m.enabled ? "On" : "Off"; });
    data.mods.forEach(m => {
      const el = opt(label(m), m.enabled ? "On" : "Off", async () => {
        const changes = { [m.name]: !m.enabled }, grp = groupOf(m.name);
        if (!m.enabled && grp) data.mods.forEach(o => { if (o !== m && groupOf(o.name) === grp && o.enabled) changes[o.name] = false; });
        try { await api("/api/patch/mods", { id: pid, changes }); } catch (e) { fail(e); return; }
        data.mods.forEach(o => { if (o.name in changes) o.enabled = changes[o.name]; });
        draw();
      }, { sel: m.enabled });
      rows.push({ el, m }); body.append(el);
    });
    setTimeout(focusDefault, 30);
  });
}
function communityPatchPanel(p) {
  openPanel(p.name, p.game + (p.author ? ` \u00b7 by ${p.author}` : ""), body => {
    body.append(h("p.pdesc", { text: p.description || "No description." }));
    if (p.gameVersion) body.append(h("p.pdesc", { text: `For game version ${p.gameVersion}${p.options ? ` \u00b7 ${p.options} option${p.options === 1 ? "" : "s"}` : ""}. A patch only fits that exact version of the game.` }));
    if (p.version) body.append(h("p.pdesc", { text: "Patch version " + p.version + (p.installed && p.installedVersion ? ` (installed: ${p.installedVersion})` : "") }));
    if (!p.installed) body.append(h("p.pwarn", { text: "Game patches change the game's memory while it runs. Install only patches from people you trust." }));
    if (!p.custom) body.append(opt(!p.installed ? "Install" : p.updateAvailable ? "Update" : "Reinstall", "", async () => {
      closePanel();
      try { const r = await api("/api/patch/install", { id: p.id }); await loadCommunity(); toast("Patch installed", `${p.name} is on for ${p.game} (${r.titleId}). Change it from the game's options.`); } catch (x) { fail(x); }
    }, { def: true }));
    if (p.installed) body.append(opt("Remove", "", async () => { closePanel(); try { await api("/api/patch/remove", { id: p.id }); await loadCommunity(); toast("Patch removed", p.name); } catch (x) { fail(x); } }));
  });
}
function installedForkPanel(f) {
  openPanel(f.name, f.current ? shortTag(f.current.tag) : "Not installed", body => {
    body.append(opt("Choose another version\u2026", "Older builds or switch between installed ones", () => { closePanel(); versionsPanel(f); }),
      opt("Open fork folder", "", () => api("/api/open", { fork: f.id }).catch(fail), { disabled: !f.current }),
      opt("Open saves folder", "Shared saves and trophies; shader caches remain per fork", () => api("/api/open", { dataFork: f.id }).catch(fail)),
      opt("Import saves\u2026", "From another KytyPS5 folder", async () => { try { const r = await api("/api/import", { fork: f.id }); if (r.copied.length) toast("Saves imported", r.copied.join(", ")); } catch (e) { fail(e); } }));
    if (!f.builtin) body.append(opt("Remove fork", "Shared saves and trophies remain available to other forks", async () => { closePanel(); try { await api("/api/fork/remove", { fork: f.id }); forkSel = null; await refreshAll(); await loadCommunity(); } catch (e) { fail(e); } }));
  });
}
async function importPatch() {
  try {
    const r = await api("/api/patch/import", {});
    if (r.titleId) { await loadCommunity(); toast("Patch imported", `For ${r.titleId}. Choose it in that game's options.`); }
  } catch (e) { fail(e); }
}

/* ---- settings ---- */
let setCat = 0;
function viewSettings() {
  const st = S.settings;
  const save = async (k, v) => { st[k] = v; try { const r = await api("/api/settings", { [k]: v }); Object.assign(S, { settings: r.settings }); } catch (e) { fail(e); } };
  const label = (l, desc) => h("div", {}, l, desc ? h("small", { text: desc }) : null);
  const toggle = (l, key, desc) => {
    const sw = h("i.switch" + (st[key] ? ".on" : ""));
    return h("button.row", { "data-f": true, "data-key": "s" + key, on: { click: () => { save(key, !st[key]); sw.classList.toggle("on", !st[key]); } } }, label(l, desc), sw);
  };
  const cycle = (l, key, values, desc) => {
    const val = h("span.val", { text: String(st[key]) });
    return h("button.row", { "data-f": true, "data-key": "s" + key, on: { click: () => { const nv = values[(values.indexOf(st[key]) + 1) % values.length]; save(key, nv); val.textContent = nv; } } }, label(l, desc), val);
  };
  const text = (l, key, desc, kind = "text") => {
    const inp = h("input", { type: kind === "number" ? "number" : kind === "password" ? "password" : "text", value: kind === "password" ? "" : st[key], placeholder: kind === "password" && S.hasToken ? "saved" : "" });
    inp.addEventListener("change", () => { const v = kind === "number" ? parseInt(inp.value, 10) || 0 : inp.value; if (kind === "password" && !v) return; save(key, v).then(() => { if (kind === "password") { S.hasToken = true; inp.value = ""; inp.placeholder = "saved"; } }); });
    return h("button.row", { "data-f": true, "data-key": "s" + key, on: { click: e => { if (e.target !== inp) editInput(e.currentTarget); } } }, label(l, desc), inp);
  };
  const action = (l, desc, fn, right) => h("button.row", { "data-f": true, "data-key": "a" + l, on: { click: fn } }, label(l, desc), h("span.val", { text: right || "" }));
  const pluginRows = () => [
    pluginRow({ label: "Add-ons", desc: "Optional extras you install yourself; Studio ships none. An add-on runs with the same access to your PC as Studio, so install only what you trust. Changes apply after restarting Studio." }),
    ...pluginList.map(p => {
      const sw = h("i.switch" + (p.enabled ? ".on" : ""));
      return h("button.row", { "data-f": true, "data-key": "pl" + p.id, on: { click: async () => {
        try { await api("/api/plugins/enable", { id: p.id, enabled: !p.enabled }); p.enabled = !p.enabled; sw.classList.toggle("on", p.enabled); toast(p.name, "Restart Studio to apply"); } catch (e) { fail(e); }
      } } }, label(p.name + (p.version ? "  " + p.version : ""), [p.description, p.author && "by " + p.author, p.error && "Error: " + p.error].filter(Boolean).join(" · ") || p.id), sw);
    }),
    ...(pluginList.length ? [] : [pluginRow({ label: "No add-ons installed", desc: "Install an add-on .zip below, or put an add-on folder or .zip into the add-ons folder and restart Studio." })]),
    action("Install add-on from .zip", "Installs or updates an add-on. Restart Studio to load it.", async () => {
      try {
        const r = await api("/api/plugins/install", {});
        if (r.cancelled) return;
        toast("Add-on installed", `${r.id} · restart Studio to load it`);
        const loaded = new Map(pluginList.map(p => [p.id, p]));
        pluginList = (await api("/api/plugins")).plugins.map(p => ({ ...p, error: loaded.get(p.id)?.error || p.error }));
        fill(true);
      } catch (e) { fail(e); }
    }),
    action("Open add-ons folder", pluginDir, () => api("/api/open", { what: "plugins" }).catch(fail)),
    action("Open logs folder", "Each add-on writes plugin-<id>.log here", () => api("/api/open", { what: "logs" }).catch(fail))];
  const commRows = () => {
    const rows = [h("div.row.info", {}, h("div", {}, "Third-party builds and patches", h("small", { text: "Made by community members, not by Studio. Install only what you trust." })))];
    if (!CM) {
      if (!cmLoading) {
        cmLoading = true;
        loadCommunity().then(() => { cmLoading = false; if (setCat === COMM) fill(true); return (CM.catalogUrl && CM.empty) || CM.forks.some(f => !f.latest && !f.checkError) ? loadCommunity(true) : null; })
          .then(() => { if (setCat === COMM) fill(true); }).catch(() => { cmLoading = false; });
      }
      rows.push(h("div.row.info", {}, h("div", { text: "Loading the catalog…" })));
    } else if (CM.empty) {
      rows.push(h("div.row.info", {}, h("div", {}, "No community catalog loaded", h("small", { text: CM.catalogUrl ? `The catalog at ${CM.catalogUrl} could not be loaded (it may not be published yet, or you are offline). You can also import a catalog folder below.` : "Load a catalog you trust below: a folder with catalog.json, or a catalog URL on GitHub." }))));
    } else {
      rows.push(h("div.tgroup", { text: "Community forks" }));
      CM.forks.forEach(e => rows.push(action(nameOf(e.name), shortDesc(e.description), () => communityForkPanel(e),
        e.installed ? (e.updateAvailable ? "Update available" : "Installed") : "Install")));
      rows.push(h("div.tgroup", { text: "Game patches" }));
      if (!CM.patches.length) rows.push(h("div.row.info", {}, h("div", {}, "No patches in the catalog yet", h("small", { text: "Import one from a file below, or point the catalog URL at a list that has them." }))));
      CM.patches.forEach(p => rows.push(action(p.name, `${p.game}${p.description ? " · " + shortDesc(p.description) : ""}`, () => communityPatchPanel(p),
        p.installed ? (p.updateAvailable ? "Update available" : "Installed") : "Install")));
    }
    rows.push(h("div.tgroup", { text: "Tools" }),
      action("Import catalog…", "Choose a folder that contains catalog.json (and a patches folder)", async () => {
        try { const r = await api("/api/community/import", {}); if (r.cancelled) return; await loadCommunity(true); fill(true); toast("Catalog imported", `${r.forks} fork(s), ${r.patches} patch(es), ${r.patchFiles} patch file(s)`); } catch (e) { fail(e); }
      }),
      action("Open community folder", "catalog.json and patches live here; edit them freely", () => api("/api/open", { what: "community" }).catch(fail)),
      action("Import patch from file…", "A game patch .json (for example PPSA21564.json)", importPatch),
      action("Add fork by GitHub URL…", "owner/repo, for builds that are not in the catalog", addFork),
      text("Catalog URL", "catalogUrl", "Optional: owner/repo of a catalog repository on GitHub, or a link to its catalog.json"),
      action("Refresh catalog and releases", "", async () => { try { await loadCommunity(true); fill(true); toast("Catalog refreshed", CM.updated ? "Catalog " + CM.updated : ""); } catch (e) { fail(e); } }, CM && CM.updated ? "Catalog " + CM.updated : ""));
    rows.push(h("div.tgroup", { text: "Installed forks (advanced)" }));
    S.forks.forEach(f => rows.push(action(f.name, f.current ? shortTag(f.current.tag) : "Not installed", () => installedForkPanel(f), "Manage")));
    return rows;
  };
  const forkRows = () => {
    const rows = [];
    S.forks.forEach(f => {
      const job = jobFor(f.id), [, txt] = forkStat(f);
      rows.push(job
        ? h("div.row.dl", { "data-key": "fj" + f.id }, h("div", {}, f.name, h("small", { "data-pm": f.id, text: job.message })), h("div.bar", {}, h("i", { "data-pf": f.id })))
        : action(f.name, `${f.current ? shortTag(f.current.tag) + " \u00b7 " : ""}${txt}`, () => forkPanel(f), f.updateAvailable ? "Update" : !f.current ? "Install" : S.defaultFork === f.id ? "Default" : ""));
    });
    const upd = S.forks.filter(f => (f.updateAvailable || !f.current) && f.latest).length;
    rows.push(h("div.sep"),
      action("Check for updates", "", async () => { try { await api("/api/check", {}); await refreshAll(); toast("Checked for updates", "Release information is up to date."); } catch (e) { fail(e); } }),
      action(upd ? `Update all (${upd})` : "Everything is up to date", "", () => S.forks.filter(f => (f.updateAvailable || !f.current) && f.latest).forEach(f => install(f, null))));
    return rows;
  };
  const cats = [
    ["Forks", forkRows],
    ["Library", [
      h("div.row.info.library-heading", {}, h("div", {}, "Games in library", h("small", { text: "Format and conversion status" }))),
      ...games.map(g => {
        const task = (S.zarJobs || []).find(j => j.game === g.id && ["queued", "scanning", "compressing"].includes(j.state));
        const finished = [...(S.zarJobs || [])].reverse().find(j => j.game === g.id && ["done", "error"].includes(j.state));
        const convertGame = async () => {
          const deleteSource = await chooseZarConversion(g.title);
          if (deleteSource === null) return;
          if (deleteSource && !(await confirmBox("Remove the original game folder?",
            "After ZArchive finishes successfully, Studio will remove this folder and keep the ZAR archive as the game copy. This cannot be undone from Studio.", "Convert and remove"))) return;
          try { await api("/api/game/convert-zar", { game: g.id, deleteSource }); await refreshState(); }
          catch (e) { fail(e); }
        };
        const convert = g.format === "Folder" && !task && S.zarAvailable
          ? h("button.pill", { "data-f": true, "data-key": `zar-${g.id}`, on: { click: convertGame } }, "Convert")
          : null;
        return h("div.row.info.library-game", {}, h("div", {}, g.title,
          h("small", { text: `${g.titleId || g.path} · ${g.format || "Folder"}` }),
          finished?.state === "error" ? h("small", { text: `Conversion failed: ${finished.message}` }) : null,
          finished?.state === "done" ? h("small", { text: finished.message }) : null,
          task ? h("div.zar-task", {}, h("small", { "data-zar-pm": task.id, text: task.state === "scanning" ? "Scanning game files…" :
            `${task.state === "queued" ? "Starting compression" : "Compressing to ZAR"} · ${fmtBytes(task.writtenBytes || 0)} written${task.sourceBytes ? ` · ${fmtBytes(task.sourceBytes)} source` : ""}` }),
            h("span.zar-progress", {}, h("i"))) : null),
          h("div.library-game-actions", {}, h("span.badge", { text: g.format || "Folder" }),
            convert,
            g.format === "ZAR" ? h("span.val", { text: "Ready" }) : !S.zarAvailable && g.format === "Folder" ? h("span.val", { text: "ZArchive unavailable" }) : null));
      }),
      ...S.libraries.map(p => action(p, "Select to remove this folder from the library", async () => { try { await api("/api/library/remove", { path: p }); await refreshAll(); } catch (e) { fail(e); } }, "Remove")),
      action("Add game folder\u2026", "Games are detected by their sce_sys folder", addLibrary),
      action("Rescan library", "", async () => { await refreshGames(); toast("Library scanned", `${games.length} game(s) found`); }, `${games.length} games`)]],
    ["Display", [
      toggle("Fullscreen", "fullscreen", "Borderless desktop fullscreen"),
      text("Window width", "width", "Used when fullscreen is off", "number"), text("Window height", "height", "", "number"),
      cycle("Present mode", "presentMode", ["Mailbox", "Fifo", "Immediate"], "Fifo = V-Sync, Immediate = lowest latency"),
      toggle("HDR output", "hdr"), toggle("Hide cursor in game", "hideCursor")]],
    ["Emulator", [
      toggle("Shader cache", "shaderCache", "Keep compiled shaders between boots"),
      toggle("Skip startup splash", "noSplash"), toggle("AMD CPU patches", "amdCpu"),
      toggle("Trophy notifications", "trophyNotifications", "Show unlock toasts and sound in the game"),
      text("Trophy toast duration", "trophyDuration", "5 to 10 seconds", "number"),
      text("GPU index", "gpu", "Vulkan device number, empty = automatic"),
      text("Extra arguments", "extraArgs", "Appended to every launch, e.g. --tessellation")]],
    ["Profile & Interface", [
      action("Manage users\u2026", "Add, rename or remove users. Saves and trophies are kept per user", openSwitchUser, `${S.users.length} user${S.users.length === 1 ? "" : "s"}`),
      toggle("Ask for user at startup", "askUser", "Show the user selection after the start screen"), toggle("Interface sounds", "sounds"),
      toggle("Show start screen", "startScreen", "The animated welcome screen when the app opens")]],
    ["Updates", [
      text("Versions to keep per fork", "keepVersions", "Older builds are deleted after an update", "number"),
      text("GitHub token", "githubToken", "Optional. Raises the API rate limit from 60 to 5000 requests/hour", "password"),
      action("Open logs folder", "Game logs are kept here", () => api("/api/open", { what: "logs" }).catch(fail))]],
    ["Community (advanced)", commRows],
    ...pluginSections.map(x => [x.label, () => x.rows()]),
    ["Plugins", pluginRows],
    ["About & legal", () => [
      h("div.row.info", {}, h("div", {}, "KytyPS5 Studio", h("small", { text: "A launcher and manager for KytyPS5 builds. Free of charge. No telemetry." }))),
      h("div.row.info", {}, h("div", {}, "Not affiliated", h("small", { text: "Not affiliated with, endorsed by or connected to Sony Interactive Entertainment, PlayStation or the KytyPS5 project. PlayStation and PS5 are trademarks of Sony Interactive Entertainment Inc.; they are used only to say what the software works with." }))),
      h("div.row.info", {}, h("div", {}, "Look and feel", h("small", { text: "The interface is an original recreation inspired by the PlayStation 5 home screen. No Sony artwork, fonts, logos or sounds are included; avatars and sounds are generated by Studio. The cat-gamepad logo is the KytyPS5 project's own artwork, used to identify the emulator; it is not made by Studio." }))),
      h("div.row.info", {}, h("div", {}, "No games, no firmware", h("small", { text: "Studio contains no games, firmware, keys or copyrighted game data and does not download any. Use only games you own and dumped yourself." }))),
      h("div.row.info", {}, h("div", {}, "Emulators and patches", h("small", { text: "Emulator builds are downloaded by you from their authors' GitHub releases and keep their own licenses (GPL-2.0 for the forks listed in the sample catalog). Studio ships none of them, nor any patch. The only default is the address of a metadata-only catalog, read when you open Settings, Community; nothing is installed until you press Install. Patches belong to their authors." }))),
      h("div.row.info", {}, h("div", {}, "Third-party software", h("small", { text: "ZArchive-Next, zstandard, Python, pywebview, pythonnet and other libraries are bundled under their own licenses. See THIRD_PARTY_LICENSES.txt in the installation folder." })))]],
    ["Exit", [action("Exit KytyPS5 Studio", "Running games are not closed", () => api("/api/quit", {}).then(() => window.close()).catch(() => { }))]],
  ];
  const v = h("div.view.settings"), left = h("div.cats"), right = h("div.items#items");
  const COMM = cats.findIndex(c => c[0].startsWith("Community"));
  const fill = keep => {  // keep = a refresh of the same list: do not lose the row under the pointer/focus
    const key = keep && F.cur && right.contains(F.cur) ? F.cur.dataset.key : null, top = right.scrollTop;
    const rows = typeof cats[setCat][1] === "function" ? cats[setCat][1]() : cats[setCat][1];
    right.replaceChildren(...rows);
    rows.forEach(r => { if (r.dataset) r.dataset.goLeft = ".cat.sel"; });
    if (keep) { right.scrollTop = top; const el = key && $$("[data-f]", right).find(e => e.dataset.key === key); if (el) setFocus(el, true); }
    else { right.classList.remove("fade"); void right.offsetWidth; right.classList.add("fade"); }
  };
  cats.forEach(([name], i) => {
    const b = h("button.cat" + (i === setCat ? ".sel" : ""), { "data-f": true, "data-key": "cat" + i, "data-default": i === setCat, "data-go-right": "#items [data-f]", ...(i === 0 ? { "data-go-up": ".backbtn" } : {}) }, name);
    b._onfocus = () => {
      if (b.classList.contains("sel")) return;
      setCat = i; $$(".cat").forEach(c => c.classList.toggle("sel", c === b)); fill();
    };
    left.append(b);
  });
  fill();
  v.append(backBtn(), h("div.stitle", { text: "Settings" }), left, right);
  return v;
}

/* ------------------------------------------------------------ plugins --- */
// Optional add-ons the user installed. Studio does not know what they do: it loads the enabled ones and
// gives them window.KytyStudio (see PLUGINS.md).
const pluginTabs = new Map(), pluginSections = [];
let pluginList = [], pluginDir = "";
function pluginRow({ label, desc, value, onClick }) {
  const body = h("div", {}, label, desc ? h("small", { text: desc }) : null);
  return onClick ? h("button.row", { "data-f": true, "data-key": "p" + label, on: { click: onClick } }, body, h("span.val", { text: value || "" }))
    : h("div.row.info", {}, body);
}
function registerPluginTab(x) {
  if (!x || !/^[a-z][a-z0-9-]{0,47}$/.test(x.id || "") || typeof x.render !== "function") return;
  pluginTabs.set(x.id, x);
  let b = $(`.tab[data-tab="${x.id}"]`);
  if (!b) {
    b = h("button.tab", { "data-tab": x.id, "data-f": true, text: x.label || x.id });
    b.addEventListener("click", () => { sfx.tab(); switchTab(x.id); });
    $("#tabs").append(b);
  } else b.textContent = x.label || x.id;
  renderTop();
}
function registerPluginSection(x) {
  if (x && x.label && typeof x.rows === "function") pluginSections.push(x);
}
function reportPluginError(text) {  // script errors from a plugin's files go to its log file
  const m = /\/api\/plugins\/ui\/([a-z][a-z0-9-]+)\//.exec(text || "");
  if (m) api("/api/plugins/log", { id: m[1], text }).catch(() => { });
}
window.addEventListener("error", e => reportPluginError((e.filename || "") + " " + (e.message || "") + " " + (e.error?.stack || "")));
window.addEventListener("unhandledrejection", e => reportPluginError(String(e.reason?.stack || e.reason || "")));
async function loadPlugins() {
  try {
    const r = await api("/api/plugins");
    pluginList = r.plugins; pluginDir = r.dir;
    for (const p of pluginList.filter(p => p.enabled)) {
      try {
        if (p.styles) document.head.append(h("link", { rel: "stylesheet", href: `/api/plugins/ui/${p.id}/${p.styles}?t=${encodeURIComponent(TOKEN)}` }));
        if (p.script) await new Promise((ok, no) => {
          const s = h("script", { src: `/api/plugins/ui/${p.id}/${p.script}?t=${encodeURIComponent(TOKEN)}` });
          s.onload = ok; s.onerror = () => no(new Error("could not load " + p.script)); document.head.append(s);
        });
      } catch (e) { p.error = String(e.message || e); console.error("Plugin " + p.id, e); }
    }
  } catch (e) { console.error("Could not load plugins", e); }
}
window.KytyStudio = {
  version: 1, api, h, $, $$, toast, fail, openKeyboard, switchTab, renderView, refreshState, refreshGames,
  registerTab: registerPluginTab, registerSection: registerPluginSection, row: pluginRow,
  plugin: id => ({ call: (route, body) => api(`/api/plugins/${id}/${route}`, body || {}) }),
  confirm: (title, text, ok) => confirmBox(title, text, ok || "OK"),
  get state() { return S; }, get activeTab() { return tab; },
};

/* ------------------------------------------------------ data refresh --- */
async function refreshGames() {
  try { games = await api("/api/games"); } catch (e) { fail(e); }
  if (tab === "games" || (tab === "settings" && setCat === 1)) renderView();
}
let lastSig = "";
const sig = () => JSON.stringify([S.forks, S.settings, S.libraries, S.defaultFork, S.jobs.map(j => [j.id, j.state]), (S.zarJobs || []).map(j => [j.id, j.state]), S.zarAvailable]);
async function refreshState() {
  const oldZarStates = new Map((S.zarJobs || []).map(j => [j.id, j.state]));
  const st = await api("/api/state");
  Object.assign(S, st);
  if ((S.zarJobs || []).some(j => ["done", "error"].includes(j.state) && oldZarStates.get(j.id) !== j.state)) {
    try { games = await api("/api/games"); } catch (_) {}
  }
  handleEvents();
  const s = sig();
  if (s !== lastSig) {
    lastSig = s; renderTop();
    if (tab !== "games" && !$("#panel.on") && !$("#overlay.on") && !document.activeElement?.matches("input")) renderView();
    if (tab === "games") updateHero();
  }
  for (const j of S.jobs) {
    const bar = $(`[data-pf="${j.fork}"]`), msg = $(`[data-pm="${j.fork}"]`);
    if (bar) bar.style.width = (j.progress * 100).toFixed(1) + "%";
    if (msg) msg.textContent = j.message + (j.speed && j.state === "downloading" ? `  ·  ${(j.speed / 1048576).toFixed(1)} MB/s` : "");
  }
  for (const j of S.zarJobs || []) {
    const msg = $$('[data-zar-pm]').find(el => el.dataset.zarPm === String(j.id));
    if (msg && ["queued", "scanning", "compressing"].includes(j.state)) {
      msg.textContent = j.state === "scanning" ? "Scanning game files…" :
        `${j.state === "queued" ? "Starting compression" : "Compressing to ZAR"} · ${fmtBytes(j.writtenBytes || 0)} written${j.sourceBytes ? ` · ${fmtBytes(j.sourceBytes)} source` : ""}`;
    }
  }
}
async function refreshAll() { await refreshState(); await refreshGames(); }

function handleEvents() {
  for (const j of S.jobs) {
    const prev = seenJobs.get(j.id);
    if (prev && prev !== j.state) {
      const f = forkById(j.fork);
      if (j.state === "done") toast(`${f?.name || j.fork} installed`, j.tag);
      if (j.state === "error") toast(`Update failed: ${f?.name || j.fork}`, j.message, true);
    }
    seenJobs.set(j.id, j.state);
  }
  for (const f of S.forks) if (f.updateAvailable && !toldUpdate.has(f.id + f.latest.tag)) {
    toldUpdate.add(f.id + f.latest.tag); toast(`Update available: ${f.name}`, f.latest.tag.slice(0, 40));
  }
  if (S.menuReq !== undefined) {
    if (lastMenuReq !== null && S.menuReq !== lastMenuReq && gameRunning()) openGameMenu();
    lastMenuReq = S.menuReq;
  }
  if (!gameRunning()) menuOpen = false;
  // running-game overlay lifecycle
  const live = S.running.find(r => r.alive);
  if (live && shownRun !== live.id && !$("#overlay.on")) { const g = games.find(x => x.id === live.game); if (g) showRunning(live, g); }
  if (shownRun) {
    const r = S.running.find(x => x.id === shownRun);
    if (r && !r.alive) {
      const g = games.find(x => x.id === r.game); shownRun = null; hideOverlay(); refreshGames();
      loadTrophies().then(prev => { const n = TR.overview ? TR.overview.earnedCount : 0; if (prev != null && n > prev) toast("Trophies earned", `${n - prev} new trophy${n - prev === 1 ? "" : "ies"} unlocked`); if (tab === "games") updateHero(); });
      if (g && r.exit !== 0 && Date.now() / 1000 - r.started < 90) showCrash(r, g);
    }
  }
}
(function loop() { refreshState().catch(() => { }).finally(() => setTimeout(loop, S.jobs.some(j => ["downloading", "extracting", "queued"].includes(j.state)) || (S.zarJobs || []).some(j => ["queued", "scanning", "compressing"].includes(j.state)) || S.running.some(r => r.alive) ? 800 : 3000)); })();

/* ------------------------------------------------------ start screen -- */
let bootDone = false, bootReady = false;
const booting = () => !bootDone && !!$("#boot");
function skipBoot() {
  if (bootDone || !bootReady) return;
  bootDone = true; sfx.ok(); tone(220, 1.2, .03, "sine", 0, 2.2);
  $("#boot").classList.add("leave");
  if (needUser()) showUsers(); else document.body.classList.remove("pre");
  setTimeout(() => { $("#boot")?.remove(); if (!$("#users")) focusDefault(); }, 1500);
}
function enterNow() { bootDone = true; $("#boot")?.remove(); if (needUser()) showUsers(); else { document.body.classList.remove("pre"); focusDefault(); } }
function bootAnim() {
  const c = $("#boot-canvas"), x = c.getContext("2d");
  const fit = () => { c.width = innerWidth; c.height = innerHeight; }; fit(); addEventListener("resize", fit);
  const P = Array.from({ length: 130 }, () => {
    const z = Math.random() ** 1.6, warm = Math.random();
    return { x: Math.random(), y: Math.random(), z, r: .6 + z * 3, vx: (.004 + z * .016) * (.5 + Math.random()), vy: -(.002 + z * .006) * Math.random(),
      tw: .5 + Math.random() * 1.8, ph: Math.random() * 6.28, col: warm < .55 ? "255,205,150" : warm < .9 ? "255,255,255" : "170,200,255" };
  });
  const t0 = performance.now(); let last = t0;
  [110, 165, 220].forEach((f, i) => tone(f, 4.5, .02, "sine", .4 + i * .5));
  setTimeout(() => { bootReady = true; tone(880, .6, .03, "sine"); tone(1318, 1.2, .025, "sine", .12); }, 3300);
  (function frame(now) {
    if (!$("#boot")) return;
    const dt = Math.min((now - last) / 1000, .1); last = now;
    const t = (now - t0) / 1000, fade = Math.min(t / 3, 1);
    x.clearRect(0, 0, c.width, c.height);
    for (const p of P) {
      p.x += p.vx * dt; p.y += p.vy * dt;
      if (p.x > 1.05) p.x = -.05;
      if (p.y < -.05) p.y = 1.05;
      const big = p.z > .93;
      const a = fade * (.18 + .55 * p.z) * (.55 + .45 * Math.sin(t * p.tw + p.ph));
      const px = p.x * c.width, py = p.y * c.height, rad = p.r * (c.height / 900) * (big ? 2.2 : 1);
      const g = x.createRadialGradient(px, py, 0, px, py, rad * 2.2);
      g.addColorStop(0, `rgba(${p.col},${a})`);
      g.addColorStop(big ? .55 : .25, `rgba(${p.col},${a * (big ? .35 : .5)})`);
      g.addColorStop(1, `rgba(${p.col},0)`);
      x.fillStyle = g; x.beginPath(); x.arc(px, py, rad * 2.2, 0, 6.283); x.fill();
    }
    requestAnimationFrame(frame);
  })(t0);
  addEventListener("pointerdown", skipBoot);
}

/* ------------------------------------------------------------- init ---- */
(async function init() {
  document.addEventListener("pointerdown", () => { actx = actx || new AudioContext(); actx.resume?.(); }, { once: true });
  try { await refreshState(); await refreshGames(); } catch (e) { fail(e); }
  await loadPlugins();
  loadTrophies().then(() => { if (tab === "games") updateHero(); });
  lastSig = sig();
  renderTop(); renderView();
  setInterval(padLoop, 16);
  if (new URLSearchParams(location.search).has("noboot") || S.settings.startScreen === false) enterNow();
  else bootAnim();
})();
