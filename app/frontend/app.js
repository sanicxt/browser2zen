/* Arc2Zen frontend: vanilla JS state machine.
 *
 * All dynamic content is built with createElement / textContent. We never
 * assign user-derived strings (or strings that could embed user data) to
 * innerHTML; the only innerHTML use clears nodes via empty string.
 */

const $  = (id) => document.getElementById(id);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const Bridge = () => (window.pywebview && window.pywebview.api) || null;
const sleep  = (ms) => new Promise((r) => setTimeout(r, ms));

function el(tag, props, children) {
  const e = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v == null) continue;
      if (k === "class") e.className = v;
      else if (k === "dataset") Object.assign(e.dataset, v);
      else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === "text") e.textContent = String(v);
      else if (k === "value") e.value = v;
      else if (k === "selected") e.selected = !!v;
      else e.setAttribute(k, String(v));
    }
  }
  for (const c of (children || [])) {
    if (c == null || c === false) continue;
    e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return e;
}

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
function replace(node, ...kids) { clear(node); for (const k of kids) if (k) node.appendChild(k); }

async function whenBridgeReady(timeoutMs = 5000) {
  const started = Date.now();
  while (!Bridge()) {
    if (Date.now() - started > timeoutMs) throw new Error("Bridge not ready");
    await sleep(50);
  }
  return Bridge();
}

function setScreen(name) {
  document.body.dataset.screen = name;
  for (const node of $$(".screen")) {
    node.classList.toggle("is-active", node.id === `screen-${name}`);
  }
}

// ---- state ----------------------------------------------------------------

const state = {
  env: null,
  selectedZenProfile: null,
  preview: null,
  options: {
    foldersCollapsed: true,
    includeWorkspaces: true,
    includePinnedTabs: true,
    includeBookmarks: true,
    includeFavicons: true,
    includeOpenTabs: true,
    includeHistory: false,
    includeCookies: false,
  },
  steps: [],
  stepLabels: {},
  stepStates: {},
  stepSummaries: {},
  logLines: [],
  pollHandle: null,
  startedAt: 0,
  elapsedHandle: null,
  activeStep: null,
  activeDetail: "",
  activeProgress: null,  // 0..1 or null
};

// ---- button loading state helper ----------------------------------------

function setLoading(btn, on, label = null) {
  if (on) {
    if (label && !btn.dataset.savedLabel) {
      btn.dataset.savedLabel = btn.textContent;
      btn.textContent = label;
    }
    if (!btn.querySelector(".spinner")) {
      btn.insertBefore(el("span", {class: "spinner"}), btn.firstChild);
    }
    btn.classList.add("is-loading");
  } else {
    btn.classList.remove("is-loading");
    const sp = btn.querySelector(".spinner"); if (sp) sp.remove();
    if (btn.dataset.savedLabel) {
      btn.textContent = btn.dataset.savedLabel;
      delete btn.dataset.savedLabel;
    }
  }
}

// ---- brand marks (Arc / Zen) --------------------------------------------
//
// Inline SVG glyphs rendered over a CSS-gradient badge.
// Sizes: default 56px (welcome hero), small 28px (cards).

// Arc Browser logo paths (CC-licensed source: Wikimedia Commons,
// File:Arc_(browser)_logo.svg). Native viewBox "0 0 82 68"; we add a
// small padding so the mark sits with breathing room inside the badge.
const ARC_PATHS = [
  ["#1a007f", "m28.8 51.97 6.35-13.36c-4.85-1.03-9.73-4.03-12.49-7.68l-6.64 13.96c3.69 3.13 8.12 5.59 12.78 7.08"],
  ["#4e000a", "M55.3 30.53c-3.19 3.91-7.62 6.81-12.36 7.94l6.33 13.32c4.62-1.56 8.94-4.08 12.67-7.31L55.3 30.53z"],
  ["#1a007f", "m16.02 44.89-3.32 6.98c-1.69 3.55-.42 7.92 3.06 9.77 3.69 1.96 8.23.43 10.01-3.3l3.03-6.37a37.885 37.885 0 0 1-12.78-7.08"],
  ["#ff9396", "M68.48 15.29a7.29 7.29 0 0 0-8.58 5.72c-.7 3.5-2.34 6.76-4.6 9.53l6.63 13.96c6.12-5.31 10.64-12.54 12.26-20.63.79-3.96-1.77-7.8-5.71-8.58"],
  ["#002dc8", "M42.94 38.47c-1.42.34-2.87.52-4.32.52-1.13 0-2.3-.13-3.47-.38-4.85-1.03-9.73-4.03-12.49-7.68-.69-.91-1.25-1.86-1.64-2.83-1.51-3.73-5.76-5.53-9.49-4.03C7.8 25.58 6 29.83 7.5 33.56c1.71 4.24 4.73 8.13 8.52 11.33a37.84 37.84 0 0 0 12.77 7.08c3.21 1.03 6.54 1.6 9.82 1.6 3.64 0 7.23-.63 10.65-1.78l-6.32-13.32z"],
  ["#ff536a", "m65.43 51.84-3.5-7.36-6.63-13.95-.01.01s0-.01.01-.01l-9.64-20.28a7.292 7.292 0 0 0-6.58-4.16c-2.81 0-5.37 1.62-6.58 4.16l-9.83 20.68c2.76 3.65 7.64 6.65 12.49 7.68l3.18-6.68c.3-.63 1.2-.63 1.5 0l3.11 6.54h.02-.02l6.33 13.32 3.11 6.54a7.28 7.28 0 0 0 6.59 4.16c.65 0 1.3-.09 1.94-.27 4.39-1.21 6.47-6.26 4.51-10.38"],
];

function makeArcMark(size = "default") {
  const wrap = document.createElement("span");
  wrap.className = `brand-mark arc${size === "small" ? " small" : ""}${size === "tiny" ? " tiny" : ""}`;
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "-6 -7 94 82");
  svg.setAttribute("fill", "none");
  for (const [fill, d] of ARC_PATHS) {
    svgChild(svg, "path", {d, fill, "fill-rule": "evenodd", "clip-rule": "evenodd"});
  }
  wrap.appendChild(svg);
  return wrap;
}

function makeZenMark(size = "default") {
  const wrap = document.createElement("span");
  wrap.className = `brand-mark zen${size === "small" ? " small" : ""}${size === "tiny" ? " tiny" : ""}`;
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 32 32");
  svg.setAttribute("fill", "none");
  // Three hollow concentric rings, thin white stroke, empty centre.
  const cx = 16, cy = 16;
  for (const r of [12, 8, 4]) {
    svgChild(svg, "circle", {cx, cy, r,
      stroke: "currentColor", "stroke-width": 1.3, fill: "none"});
  }
  wrap.appendChild(svg);
  return wrap;
}

function makeArrowGlyph() {
  const span = document.createElement("span");
  span.className = "arrow";
  span.textContent = "→";
  return span;
}

// Decorate static placeholders on welcome + detect.
function decorateBranding() {
  const pair = $("brand-pair");
  if (pair && !pair.firstChild) {
    pair.appendChild(makeArcMark());
    pair.appendChild(makeArrowGlyph());
    pair.appendChild(makeZenMark());
  }
  const arcMark = $("arc-mark");
  if (arcMark) {
    const fresh = makeArcMark("small");
    fresh.id = "arc-mark";
    arcMark.replaceWith(fresh);
  }
  const zenMark = $("zen-mark");
  if (zenMark) {
    const fresh = makeZenMark("small");
    fresh.id = "zen-mark";
    zenMark.replaceWith(fresh);
  }
}

// ---- titlebar -------------------------------------------------------------

$("tl-close").addEventListener("click", async () => {
  const api = Bridge(); if (api) api.quit_app();
});

// ---- welcome --------------------------------------------------------------

$("welcome-go").addEventListener("click", () => goToDetect());

async function goToDetect() {
  setScreen("detect");
  await runDetect();
}

// ---- detect ---------------------------------------------------------------

async function runDetect() {
  const api = await whenBridgeReady();
  $("arc-pill").textContent = "Detecting…";
  $("zen-pill").textContent = "Detecting…";
  state.env = await api.check_env();
  renderDetect(state.env);
}

function renderDetect(env) {
  // Arc card
  const arcOk = env.arcInstalled && !env.arcRunning && env.arcProfiles.length > 0;
  const arcPill = $("arc-pill");
  const arcDetail = $("arc-detail");
  const arcCard = $("card-arc");
  $("arc-running-row").style.display = env.arcRunning ? "" : "none";

  arcCard.dataset.ok = arcOk ? "true" : "false";
  if (!env.arcInstalled) {
    arcPill.className = "pill pill-err"; arcPill.textContent = "Not found";
    arcDetail.textContent = "We couldn't find Arc data in ~/Library/Application Support/Arc.";
  } else if (env.arcRunning) {
    arcPill.className = "pill pill-warn"; arcPill.textContent = "Running";
    arcDetail.textContent = "Arc is open. Quit it before we read its data.";
  } else if (env.arcProfiles.length === 0) {
    arcPill.className = "pill pill-warn"; arcPill.textContent = "Empty";
    arcDetail.textContent = "Arc is installed but no profiles were found.";
  } else {
    arcPill.className = "pill pill-ok"; arcPill.textContent = "Ready";
    const n = env.arcProfiles.length;
    arcDetail.textContent = `${n} profile${n === 1 ? "" : "s"}: ${env.arcProfiles.join(", ")}.`;
  }

  // Zen card
  const zenCard = $("card-zen");
  const zenPill = $("zen-pill");
  const zenDetail = $("zen-detail");
  const zenSelect = $("zen-profile-select");
  $("zen-running-row").style.display = env.zenRunning ? "" : "none";
  $("zen-install-row").style.display = env.zenInstalled ? "none" : "";

  if (!env.zenInstalled) {
    zenCard.dataset.ok = "false";
    zenPill.className = "pill pill-err"; zenPill.textContent = "Not installed";
    zenDetail.textContent = "Zen Browser doesn't appear to be installed yet.";
    zenSelect.style.display = "none";
  } else if (env.zenRunning) {
    zenCard.dataset.ok = "false";
    zenPill.className = "pill pill-warn"; zenPill.textContent = "Running";
    zenDetail.textContent = "Zen is open. Quit it before we write to its profile.";
    zenSelect.style.display = "none";
  } else {
    zenCard.dataset.ok = "true";
    zenPill.className = "pill pill-ok"; zenPill.textContent = "Ready";
    if (env.zenProfiles.length > 1) {
      zenDetail.textContent = `${env.zenProfiles.length} profiles found. Pick one:`;
      zenSelect.style.display = "";
      clear(zenSelect);
      env.zenProfiles.forEach((p, i) => {
        zenSelect.appendChild(el("option", {value: p.path, selected: i === 0,
          text: p.name + (p.isRelease ? " (release)" : "")}));
      });
      state.selectedZenProfile = env.zenProfiles[0].path;
      zenSelect.onchange = () => { state.selectedZenProfile = zenSelect.value; updateGate(state.env); };
    } else {
      const p = env.zenProfiles[0];
      zenDetail.textContent = `Profile: ${p.name}.`;
      zenSelect.style.display = "none";
      state.selectedZenProfile = p.path;
    }
  }

  updateGate(env);
}

function updateGate(env) {
  const gate = $("detect-gate");
  const text = $("detect-gate-text");
  const next = $("detect-next");

  const issues = [];
  if (!env.arcInstalled) issues.push("Arc isn't installed.");
  if (env.arcRunning) issues.push("Arc is still running.");
  if (env.arcInstalled && env.arcProfiles.length === 0) issues.push("Arc has no profiles.");
  if (!env.zenInstalled) issues.push("Zen isn't installed (or never launched).");
  if (env.zenRunning) issues.push("Zen is still running.");
  if (!env.hasLz4) issues.push("Python lz4 module missing (build issue).");

  if (issues.length === 0) {
    gate.style.display = "none";
    next.disabled = false;
  } else {
    gate.style.display = "";
    gate.classList.toggle("is-error", issues.some(i => i.includes("isn't installed")));
    text.textContent = issues.join(" ");
    next.disabled = true;
  }
}

$("arc-quit-btn").addEventListener("click", async () => {
  const btn = $("arc-quit-btn");
  setLoading(btn, true, "Quitting Arc");
  await Bridge().quit_browser("arc"); await sleep(400);
  await runDetect();
  setLoading(btn, false);
});
$("zen-quit-btn").addEventListener("click", async () => {
  const btn = $("zen-quit-btn");
  setLoading(btn, true, "Quitting Zen");
  await Bridge().quit_browser("zen"); await sleep(400);
  await runDetect();
  setLoading(btn, false);
});
$("detect-recheck").addEventListener("click", () => runDetect());
$("detect-back").addEventListener("click", () => setScreen("welcome"));
$("detect-next").addEventListener("click", () => goToPreview());
$("zen-install-btn").addEventListener("click", () => {
  const api = Bridge();
  if (api) api.open_url("https://zen-browser.app/");
});

// ---- preview --------------------------------------------------------------

async function goToPreview() {
  setScreen("preview");
  replace($("stat-strip"), el("span", {class: "muted", text: "Reading Arc data…"}));
  clear($("spaces-list"));
  clear($("toggles"));

  const api = Bridge();
  const opts = currentOptionsJson();
  const preview = await api.preview(opts);
  if (preview && preview.error) {
    replace($("stat-strip"), el("span", {class: "muted", text: preview.error}));
    return;
  }
  state.preview = preview;
  renderPreview(preview);
}

function renderPreview(p) {
  const strip = $("stat-strip");
  clear(strip);
  for (const [n, lbl] of [
    [p.spaces.length, "Spaces"],
    [p.pinnedTotal, "Pinned tabs"],
    [p.openTotal, "Open tabs"],
    [p.folderTotal, "Folders"],
    [p.bookmarkTotal, "Bookmarks"],
    [p.faviconMatchEstimate, "Favicons"],
  ]) {
    strip.appendChild(makeStat(n, lbl));
  }

  const list = $("spaces-list"); clear(list);
  for (const s of p.spaces) list.appendChild(makeSpaceRow(s));

  const toggles = $("toggles"); clear(toggles);
  toggles.appendChild(makeToggle("includeOpenTabs", "Open tabs",
                                 `Migrate ${p.openTotal} open tabs`));
  toggles.appendChild(makeToggle("includeHistory", "Browsing history",
                                 `Copy ~${formatRows(p.historyRowsEstimate)} history rows`));
  toggles.appendChild(makeToggle("includeCookies", "Cookies & login state",
                                 `Copy ~${formatRows(p.cookiesEstimate)} cookies (Keychain prompt)`));
  toggles.appendChild(makeToggle("foldersCollapsed", "Collapse folders",
                                 "Imported folders start collapsed"));
}

function makeStat(n, lbl) {
  return el("div", {class: "stat"}, [
    el("span", {class: "num", text: formatRows(n)}),
    el("span", {class: "lbl", text: lbl}),
  ]);
}

function makeSpaceRow(s) {
  const hasColor = Array.isArray(s.color) && s.color.length === 3;
  const row = el("div", {class: hasColor ? "space-row" : "space-row no-color"}, [
    el("div", {class: "icon", text: s.icon || "·"}),
    el("div", {class: "text"}, [
      el("div", {class: "name", text: s.name}),
      el("div", {class: "meta", text:
        `${s.folderCount} folder${s.folderCount === 1 ? "" : "s"}` +
        (s.essentialCount ? ` · ${s.essentialCount} essential` : "")}),
    ]),
    el("div", {class: "count", text: `${s.pinnedCount} tabs`}),
  ]);
  if (hasColor) {
    const [r, g, b] = s.color;
    row.style.setProperty("--space-tint-r", r);
    row.style.setProperty("--space-tint-g", g);
    row.style.setProperty("--space-tint-b", b);
  }
  return row;
}

function makeToggle(key, label, desc) {
  const node = el("div", {
    class: "toggle",
    dataset: {key, on: state.options[key] ? "true" : "false"},
    onclick: () => {
      state.options[key] = !state.options[key];
      node.dataset.on = state.options[key] ? "true" : "false";
    },
  }, [
    el("div", {class: "label-stack"}, [
      el("span", {class: "lbl", text: label}),
      el("span", {class: "desc", text: desc}),
    ]),
    el("div", {class: "switch"}),
  ]);
  return node;
}

$("preview-back").addEventListener("click", () => goToDetect());
$("preview-go").addEventListener("click", () => goToProgress());

// ---- progress -------------------------------------------------------------

// ---- step icons (inline SVG built via DOM, no string parsing) ----------

const SVG_NS = "http://www.w3.org/2000/svg";

function svgChild(parent, tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, String(v));
  parent.appendChild(node);
  return node;
}

const STEP_ICON_BUILDERS = {
  extract: (s) => {
    svgChild(s, "path", {d: "M5 2 L11 2 L14 5 L14 16 L4 16 L4 2 Z"});
    svgChild(s, "path", {d: "M11 2 L11 5 L14 5"});
    svgChild(s, "path", {d: "M6 9 L12 9"});
    svgChild(s, "path", {d: "M6 12 L12 12"});
  },
  containers: (s) => {
    svgChild(s, "path", {d: "M9 2 L15 5 L9 8 L3 5 Z"});
    svgChild(s, "path", {d: "M3 9 L9 12 L15 9"});
    svgChild(s, "path", {d: "M3 12 L9 15 L15 12"});
  },
  sessions: (s) => {
    svgChild(s, "rect", {x: 2.5, y: 3, width: 13, height: 12, rx: 1.5});
    svgChild(s, "line", {x1: 2.5, y1: 6.5, x2: 15.5, y2: 6.5});
  },
  bookmarks: (s) => svgChild(s, "path", {d: "M5 2 L13 2 L13 16 L9 13 L5 16 Z"}),
  favicons: (s) => {
    svgChild(s, "rect", {x: 2.5, y: 3, width: 13, height: 12, rx: 1.5});
    svgChild(s, "circle", {cx: 6.5, cy: 7, r: 1.3, fill: "currentColor", stroke: "none"});
    svgChild(s, "path", {d: "M2.5 13 L7 9 L11 12 L15.5 8"});
  },
  open_tabs: (s) => {
    svgChild(s, "rect", {x: 3, y: 4, width: 11, height: 11, rx: 1.5});
    svgChild(s, "rect", {x: 6, y: 2, width: 9, height: 3, rx: 1});
  },
  history: (s) => {
    svgChild(s, "circle", {cx: 9, cy: 9, r: 6.5});
    svgChild(s, "path", {d: "M9 5 L9 9 L12 11", "stroke-linecap": "round"});
  },
  cookies: (s) => {
    svgChild(s, "circle", {cx: 6, cy: 9, r: 3.5});
    svgChild(s, "path", {d: "M9.5 9 L16 9", "stroke-linecap": "round"});
    svgChild(s, "path", {d: "M14 9 L14 12", "stroke-linecap": "round"});
    svgChild(s, "path", {d: "M16 9 L16 11.5", "stroke-linecap": "round"});
  },
  finalize: (s) => svgChild(s, "path", {d: "M3 9 L7.5 13 L15 5",
                                         "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round"}),
};

function makeStepIcon(step) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 18 18");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.5");
  svg.setAttribute("stroke-linejoin", "round");
  const builder = STEP_ICON_BUILDERS[step] || STEP_ICON_BUILDERS.sessions;
  builder(svg);
  return svg;
}

// ---- step descriptions + streaming patterns -----------------------------

const STEP_SUBTITLES = {
  extract:    "Reading Arc's StorableSidebar.json",
  containers: "Setting up cookie-isolated containers per space",
  sessions:   "Writing spaces, pinned tabs and folders to zen-sessions.jsonlz4",
  bookmarks:  "Mirroring pinned tabs as Firefox bookmarks",
  favicons:   "Decoding Arc's icon cache and inlining into Zen tabs",
  open_tabs:  "Creating Zen sessionstore entries",
  history:    "Copying browsing history into places.sqlite",
  cookies:    "Decrypting Arc cookies via Keychain and writing to cookies.sqlite",
  finalize:   "Marking migration complete",
};

// Each pattern returns {detail, percent?} when matched against a log line.
// We use String.prototype.match (no regex.exec) so it composes cleanly.
const STREAM_PATTERNS = {
  extract: [
    [/Found (\d+) spaces with pinned tabs/, m => ({detail: `Found ${m[1]} spaces`})],
    [/✅ ([^:]+): (\d+) pinned tabs/,        m => ({detail: `Read ${m[1].trim()}: ${m[2]} pinned tabs`})],
  ],
  containers: [
    [/Creating containers? for (\d+)/, m => ({detail: `Creating ${m[1]} containers`})],
    [/Reusing existing container/,     () => ({detail: "Reusing existing container"})],
  ],
  sessions: [
    [/Importing pinned tabs? \(workspace ([^)]+)\)/, m => ({detail: `Workspace: ${m[1]}`})],
    [/Imported (\d+) pinned tabs/,                   m => ({detail: `Imported ${m[1]} pinned tabs`})],
    [/Backed up zen-sessions/,                       () => ({detail: "Backed up zen-sessions.jsonlz4"})],
  ],
  bookmarks: [
    [/Creating bookmark folder/, () => ({detail: "Creating bookmark folders"})],
    [/Imported: (\d+) bookmarks/, m => ({detail: `Imported ${m[1]} bookmarks`})],
  ],
  favicons: [
    [/Reading Arc favicons from (\S+)/,     m => ({detail: `Reading ${m[1]} profile`})],
    [/Matched favicons for (\d+) of (\d+)/, m => ({detail: `Matched ${m[1]} of ${m[2]}`, percent: +m[1] / +m[2]})],
    [/Backed up favicons/,                  () => ({detail: "Backed up favicons.sqlite"})],
    [/Imported (\d+) favicons/,             m => ({detail: `Imported ${m[1]} favicons`})],
    [/Injected favicons into (\d+) tabs/,   m => ({detail: `Inlined favicons into ${m[1]} tabs`})],
  ],
  history: [
    [/Reading Arc history from (\S+)/,                                    m => ({detail: `Reading ${m[1]} profile`})],
    [/Aggregated (\d+) URLs from Arc history \((\d+) visits\)/,           m => ({detail: `Aggregated ${formatRows(+m[1])} URLs / ${formatRows(+m[2])} visits`})],
    [/Backed up places\.sqlite/,                                          () => ({detail: "Backed up places.sqlite"})],
    [/History: \+(\d+) new places, ~(\d+) merged, \+(\d+) visits/,        m => ({detail: `+${formatRows(+m[1])} places, +${formatRows(+m[3])} visits`})],
  ],
  cookies: [
    [/Reading Arc cookies from (\S+)/,                            m => ({detail: `Reading ${m[1]} profile`})],
    [/Decrypted (\d+) encrypted cookie/,                          m => ({detail: `Decrypted ${formatRows(+m[1])} cookies`})],
    [/Decoded (\d+) of (\d+) cookies/,                            m => ({detail: `Decoded ${m[1]} of ${m[2]}`, percent: +m[1] / +m[2]})],
    [/Backed up cookies/,                                         () => ({detail: "Backed up cookies.sqlite"})],
    [/Cookies: imported (\d+), merged (\d+) across (\d+)/,        m => ({detail: `${formatRows(+m[1])} imported across ${m[3]} contexts`})],
  ],
};

function applyStreamingMatch(step, message) {
  const patterns = STREAM_PATTERNS[step]; if (!patterns) return null;
  for (const [re, fn] of patterns) {
    const m = String(message).match(re);
    if (m) return fn(m);
  }
  return null;
}

// ---- progress flow -------------------------------------------------------

async function goToProgress() {
  setScreen("progress");

  const api = Bridge();
  const meta = await api.get_step_metadata();
  state.steps = meta.steps;
  state.stepLabels = meta.labels;
  state.stepStates = Object.fromEntries(state.steps.map(s => [s, "pending"]));
  state.stepSummaries = {};
  state.logLines = [];
  state.activeStep = null;
  state.activeDetail = "";
  state.activeProgress = null;
  state.startedAt = Date.now();

  if (state.elapsedHandle) clearInterval(state.elapsedHandle);
  state.elapsedHandle = setInterval(updateElapsed, 1000);
  updateElapsed();

  renderProgress();

  await api.start_migration(currentOptionsJson());
  state.pollHandle = setInterval(pollProgress, 120);
}

function updateElapsed() {
  const sec = Math.max(0, Math.floor((Date.now() - state.startedAt) / 1000));
  const mm = Math.floor(sec / 60);
  const ss = String(sec % 60).padStart(2, "0");
  $("elapsed").textContent = `${mm}:${ss}`;
}

async function pollProgress() {
  const api = Bridge(); if (!api) return;
  const data = await api.drain_progress();
  for (const ev of (data.events || [])) handleEvent(ev);
  if (data.state && data.state.status === "done") {
    clearInterval(state.pollHandle); state.pollHandle = null;
    if (state.elapsedHandle) { clearInterval(state.elapsedHandle); state.elapsedHandle = null; }
    finishOk(data.state);
  } else if (data.state && data.state.status === "error") {
    clearInterval(state.pollHandle); state.pollHandle = null;
    if (state.elapsedHandle) { clearInterval(state.elapsedHandle); state.elapsedHandle = null; }
    finishError(data.state);
  }
}

function handleEvent(ev) {
  if (ev.kind === "step_start") {
    state.stepStates[ev.step] = "active";
    state.activeStep = ev.step;
    state.activeDetail = "";
    state.activeProgress = null;
  } else if (ev.kind === "step_done") {
    state.stepStates[ev.step] = "done";
    if (ev.summary) state.stepSummaries[ev.step] = ev.summary;
    if (state.activeStep === ev.step) {
      state.activeStep = null;
      state.activeDetail = "";
      state.activeProgress = null;
    }
  } else if (ev.kind === "step_error") {
    state.stepStates[ev.step] = "error";
  } else {
    state.logLines.push({kind: ev.kind, message: ev.message, step: ev.step});
    if (state.logLines.length > 500) state.logLines.shift();
    if (state.activeStep && (ev.step === state.activeStep || !ev.step)) {
      const match = applyStreamingMatch(state.activeStep, ev.message);
      if (match) {
        if (match.detail !== undefined) state.activeDetail = match.detail;
        if (match.percent !== undefined) state.activeProgress = match.percent;
      }
    }
  }
  renderProgress();
  renderLog();
}

function visibleSteps() {
  return state.steps.filter(s => {
    if (s === "open_tabs") return state.options.includeOpenTabs;
    if (s === "history")   return state.options.includeHistory;
    if (s === "cookies")   return state.options.includeCookies;
    if (s === "containers" || s === "sessions") return state.options.includeWorkspaces || state.options.includePinnedTabs;
    if (s === "bookmarks") return state.options.includeBookmarks;
    if (s === "favicons")  return state.options.includeFavicons;
    return true;
  });
}

function renderProgress() {
  const steps = visibleSteps();

  // top meter
  const meter = $("meter"); clear(meter);
  for (const s of steps) {
    meter.appendChild(el("div", {class: "meter-segment",
                                  dataset: {state: state.stepStates[s] || "pending", step: s}}));
  }

  const activeIdx = steps.findIndex(s => state.stepStates[s] === "active");
  const total = steps.length;
  const meta = $("progress-meta");
  if (activeIdx >= 0)                                          meta.textContent = `Step ${activeIdx + 1} of ${total}`;
  else if (steps.every(s => state.stepStates[s] === "done"))   meta.textContent = `${total} of ${total} done`;
  else                                                         meta.textContent = "Working";

  // unified timeline
  const tl = $("timeline"); clear(tl);
  for (const s of steps) {
    const st = state.stepStates[s] || "pending";
    if (st === "active") {
      tl.appendChild(makeActiveRow(s));
    } else {
      tl.appendChild(makeRow(s, st));
    }
  }
}

function makeRow(step, st) {
  const summary = (st === "done") ? stepSummaryText(step, state.stepSummaries[step]) : "";
  return el("li", {class: "tl-step", dataset: {step, state: st}}, [
    el("div", {class: "ico"}, [makeStepIcon(step)]),
    el("span", {class: "lbl", text: state.stepLabels[step] || step}),
    el("span", {class: "summary", text: summary}),
  ]);
}

function makeActiveRow(step) {
  const live = el("div", {class: "live"});
  live.appendChild(el("div", {class: "detail", text: state.activeDetail || ""}));
  if (state.activeProgress != null) {
    const fill = el("span");
    fill.style.width = `${Math.round(state.activeProgress * 100)}%`;
    live.appendChild(el("div", {class: "bar"}, [fill]));
  }
  return el("li", {class: "tl-step", dataset: {step, state: "active"}}, [
    el("div", {class: "ico"}, [makeStepIcon(step)]),
    el("span", {class: "lbl", text: state.stepLabels[step] || step}),
    el("span", {class: "subtitle", text: STEP_SUBTITLES[step] || ""}),
    live,
  ]);
}

function stepSummaryText(step, s) {
  if (!s) return "";
  if (step === "extract")    return `${s.spaces ?? "?"} spaces · ${s.pinned ?? "?"} tabs`;
  if (step === "containers") return `${s.created_or_reused ?? "?"} containers`;
  if (step === "favicons") {
    const db = s.db || {}; const sess = s.session || {};
    return `${formatRows(db.imported ?? 0)} icons · ${formatRows(sess.updated ?? 0)} tabs inlined`;
  }
  if (step === "history") return `${formatRows(s.places_added ?? 0)} places · ${formatRows(s.visits_added ?? 0)} visits`;
  if (step === "cookies") return `${formatRows(s.imported ?? 0)} imported · ${formatRows(s.merged ?? 0)} merged`;
  if (step === "bookmarks" || step === "sessions" || step === "open_tabs") return s.ok ? "ok" : "";
  return "";
}

function renderLog() {
  const wrap = $("log"); clear(wrap);
  const recent = state.logLines.slice(-200);
  for (const l of recent) {
    const cls = l.kind === "warn" ? "warn" : (l.kind === "step_error" ? "err" : "");
    wrap.appendChild(el("div", {class: cls, text: l.message}));
  }
  wrap.scrollTop = wrap.scrollHeight;
}

// ---- done -----------------------------------------------------------------

async function finishOk(finalState) {
  setScreen("done");
  const api = Bridge();
  const ext = state.stepSummaries.extract || {};
  const subtitle = ext.pinned
    ? `${ext.pinned} pinned tabs across ${ext.spaces} spaces. Backups saved next to your Zen profile.`
    : `Backups saved next to your Zen profile.`;
  $("done-summary").textContent = subtitle;

  const list = $("backups-list"); clear(list);
  const backups = (finalState.backups || []).slice(-12).reverse();
  for (const path of backups) {
    list.appendChild(el("li", {
      dataset: {path},
      text: shortenPath(path),
      onclick: () => api.open_path_in_finder(path),
    }));
  }
}

$("done-launch").addEventListener("click", async () => {
  const api = Bridge(); if (!api) return;
  // Don't await quit_app — calling window.destroy() while a JS-Python promise
  // is still pending leaves WKWebView waiting for a reply that never comes,
  // which looks like the window "hanging on loading".
  try { await api.launch_zen(); } catch (e) { /* best-effort */ }
  setTimeout(() => { api.quit_app(); }, 80);
});
$("done-quit").addEventListener("click", async () => {
  const api = Bridge(); if (api) api.quit_app();
});

// ---- error ----------------------------------------------------------------

function finishError(finalState) {
  setScreen("error");
  $("error-summary").textContent = finalState.error || "Something went wrong.";
  const detail = (finalState.trace || "") + "\n\n" + state.logLines.map(l => `[${l.kind}] ${l.message}`).join("\n");
  $("error-body").textContent = detail.trim();
}

$("error-quit").addEventListener("click", async () => {
  const api = Bridge(); if (api) api.quit_app();
});
$("error-copy").addEventListener("click", async () => {
  const api = Bridge();
  if (api) await api.copy_to_clipboard($("error-body").textContent);
});

// ---- backups screen ----------------------------------------------------

const BACKUP_GROUP_LABELS = {
  "zen-sessions.jsonlz4": ["Sessions", "Workspaces, pinned tabs, folders, inline favicons"],
  "favicons.sqlite":      ["Favicons",  "Per-page favicon cache"],
  "places.sqlite":        ["Places",    "Bookmarks and browsing history"],
  "cookies.sqlite":       ["Cookies",   "Login state and per-container cookies"],
};

$("open-backups").addEventListener("click", () => goToBackups());
$("backups-back").addEventListener("click", () => setScreen("welcome"));
$("backups-refresh").addEventListener("click", () => loadBackups());

async function goToBackups() {
  setScreen("backups");
  await loadBackups();
}

async function loadBackups() {
  const api = await whenBridgeReady();
  const list = await api.list_backups();
  renderBackups(list || []);
}

function renderBackups(items) {
  const groups = $("backups-groups");
  clear(groups);

  if (!items.length) { $("backups-empty").style.display = ""; return; }
  $("backups-empty").style.display = "none";

  // group by `original`
  const byOriginal = new Map();
  for (const it of items) {
    if (!byOriginal.has(it.original)) byOriginal.set(it.original, []);
    byOriginal.get(it.original).push(it);
  }
  // sort each group's entries newest first (already from server, but defensive)
  for (const arr of byOriginal.values()) arr.sort((a, b) => b.ts - a.ts);

  // render in known order, then unknown
  const known = Object.keys(BACKUP_GROUP_LABELS);
  const ordered = [
    ...known.filter(k => byOriginal.has(k)),
    ...[...byOriginal.keys()].filter(k => !known.includes(k)),
  ];

  for (const original of ordered) {
    const entries = byOriginal.get(original);
    const [label, desc] = BACKUP_GROUP_LABELS[original] || [original, ""];

    const group = el("div", {class: "backup-group"});
    group.appendChild(el("h3", {text: label + " · " + original}));
    if (desc) group.appendChild(el("p", {class: "desc", text: desc}));
    const ul = el("ul", {class: "backup-list"});
    for (const e of entries) ul.appendChild(makeBackupRow(e));
    group.appendChild(ul);
    groups.appendChild(group);
  }
}

function makeBackupRow(entry) {
  return el("li", {class: "backup-row"}, [
    el("span", {class: "ts", text: entry.iso}),
    el("span", {class: "size", text: humanSize(entry.size)}),
    el("button", {
      class: "btn btn-soft btn-pill",
      text: "Restore",
      onclick: async () => {
        const api = Bridge();
        const r = await api.restore_backup(entry.path);
        if (r && r.ok) {
          await loadBackups();
        } else {
          alert("Restore failed: " + (r && r.error || "unknown error"));
        }
      },
    }),
    el("button", {
      class: "btn btn-soft btn-pill btn-danger",
      text: "Delete",
      onclick: async () => {
        const api = Bridge();
        const r = await api.delete_backup(entry.path);
        if (r && r.ok) await loadBackups();
        else alert("Delete failed: " + (r && r.error || "unknown error"));
      },
    }),
  ]);
}

function humanSize(b) {
  if (b == null) return "—";
  if (b < 1024) return b + " B";
  if (b < 1024 * 1024) return (b / 1024).toFixed(1) + " KB";
  if (b < 1024 * 1024 * 1024) return (b / (1024*1024)).toFixed(1) + " MB";
  return (b / (1024*1024*1024)).toFixed(2) + " GB";
}

// ---- utilities -----------------------------------------------------------

function currentOptionsJson() {
  return JSON.stringify({
    zenProfilePath: state.selectedZenProfile,
    arcSpaceFilter: null,
    foldersCollapsed: state.options.foldersCollapsed,
    includeWorkspaces: state.options.includeWorkspaces,
    includePinnedTabs: state.options.includePinnedTabs,
    includeBookmarks: state.options.includeBookmarks,
    includeFavicons: state.options.includeFavicons,
    includeOpenTabs: state.options.includeOpenTabs,
    includeHistory: state.options.includeHistory,
    includeCookies: state.options.includeCookies,
  });
}

function formatRows(n) {
  if (n == null) return "-";
  if (n < 1000) return String(n);
  if (n < 1e6)  return `${(n / 1000).toFixed(n < 10000 ? 1 : 0)}k`;
  return `${(n / 1e6).toFixed(1)}M`;
}

function shortenPath(p) {
  const home = "/Users/";
  if (p.startsWith(home)) {
    const idx = p.indexOf("/", home.length);
    if (idx > 0) return "~" + p.slice(idx);
  }
  return p;
}

// ---- bootstrap ------------------------------------------------------------

async function setPlatformAttribute() {
  const api = Bridge();
  if (!api) return;
  try {
    const p = await api.platform();
    if (typeof p === "string") document.body.dataset.platform = p;
  } catch (_) { /* best effort */ }
}

async function setAppVersion() {
  const api = Bridge();
  if (!api) return;
  try {
    const v = await api.version();
    if (typeof v === "string" && v) {
      const node = $("ver");
      if (node) node.textContent = `arc2zen · v${v}`;
    }
  } catch (_) { /* best effort */ }
}

window.addEventListener("pywebviewready", () => {
  setPlatformAttribute();
  setAppVersion();
  decorateBranding();
  setScreen("welcome");
});
setTimeout(() => {
  if (!Bridge()) return;
  setPlatformAttribute();
  setAppVersion();
  decorateBranding();
  setScreen("welcome");
}, 200);
