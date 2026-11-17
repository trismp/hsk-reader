/* HSK Reader: a tiny static app. No framework, no server.
 *
 * Data comes pre-processed by tools/build_site.py:
 *   data/library.json         list of stories
 *   data/stories/<id>.json    paragraphs as tokens:
 *       "，"                                  punctuation / plain text
 *       ["嫦娥", "Cháng é", 0, "Chang'e"]    [word, pinyin, level, meaning]
 *   level: 1-7 = HSK level (7 = 7-9), 10 = beyond HSK, 0 = name, -1 = key word
 *
 * Settings and saved words live in this browser only (localStorage).
 */
(() => {
  "use strict";

  const CATEGORIES = {
    folklore: "Myths & folklore", history: "History", east_asia: "East Asian tales",
    culture: "Culture & festivals", classics: "Classical texts",
  };
  const app = document.getElementById("app");
  const $ = (id) => document.getElementById(id);

  // ---------- storage (wrapped: private windows may block it) ----------
  const store = {
    get(key, fallback) {
      try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; }
      catch { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ignore */ }
    },
  };
  const settings = Object.assign({ level: 4, pinyin: "hard", markHard: true }, store.get("hsk.settings", {}));
  let saved = store.get("hsk.saved", {});
  const saveSettings = () => store.set("hsk.settings", settings);
  const saveWords = () => { store.set("hsk.saved", saved); updateCount(); };

  // ---------- helpers ----------
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const levelLabel = (l) =>
    l === -1 ? "Key word" : l === 0 ? "Name" : l === 10 ? "Beyond HSK" : l === 7 ? "HSK 7–9" : `HSK ${l}`;
  const isHard = (l) => l === 10 || (l > settings.level);
  const cache = new Map();
  async function getJSON(url) {
    if (!cache.has(url)) {
      cache.set(url, fetch(url).then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${url}`);
        return r.json();
      }));
    }
    return cache.get(url);
  }

  function updateCount() {
    const n = Object.keys(saved).length;
    $("saved-count").textContent = n ? String(n) : "";
  }

  function setActiveNav(name) {
    document.querySelectorAll("[data-nav]").forEach((a) =>
      a.classList.toggle("active", a.dataset.nav === name));
  }

  // ---------- library page ----------
  let filter = store.get("hsk.filter", { level: "all", category: "all" });

  async function renderLibrary() {
    setActiveNav("library");
    const stories = await getJSON("data/library.json");
    const levels = [...new Set(stories.map((s) => s.level))].sort((a, b) => a - b);
    const cats = [...new Set(stories.map((s) => s.category))];

    const chips = (kind, values, label) => [["all", "All"], ...values.map((v) => [String(v), label(v)])]
      .map(([v, text]) => `<button class="chip" data-${kind}="${esc(v)}" aria-pressed="${String(filter[kind]) === v}">${esc(text)}</button>`)
      .join("");

    const shown = stories.filter((s) =>
      (filter.level === "all" || String(s.level) === filter.level) &&
      (filter.category === "all" || s.category === filter.category));

    app.innerHTML = `
      <section class="hero">
        <h1>Read Chinese at your level</h1>
        <p>Myths, history and culture, retold in HSK 3.0 vocabulary. Tap any word for its meaning.</p>
      </section>
      <div class="filters" role="group" aria-label="Filter by level">${chips("level", levels, levelLabel)}</div>
      <div class="filters" role="group" aria-label="Filter by category">${chips("category", cats, (c) => CATEGORIES[c] || c)}</div>
      <div class="cards">
        ${shown.map((s) => `
          <a class="card" href="#/story/${encodeURIComponent(s.id)}">
            <div class="card-top">
              <span class="badge">${levelLabel(s.level)}</span>
              <span class="badge cat">${esc(CATEGORIES[s.category] || s.category)}</span>
              <span class="meta">${s.characters} characters</span>
            </div>
            <h2>${esc(s.title)}</h2>
            <p>${esc(s.summary)}</p>
          </a>`).join("") || `<p class="empty">No stories match these filters.</p>`}
      </div>`;

    app.querySelectorAll(".chip").forEach((btn) => btn.addEventListener("click", () => {
      if (btn.dataset.level) filter.level = btn.dataset.level;
      if (btn.dataset.category) filter.category = btn.dataset.category;
      store.set("hsk.filter", filter);
      renderLibrary();
    }));
  }

  // ---------- reader page ----------
  function rubyFor(word, pinyin) {
    const syl = pinyin.split(/\s+/).filter(Boolean);
    const chars = [...word];
    if (syl.length === chars.length) {
      return chars.map((c, i) => `${esc(c)}<rt>${esc(syl[i])}</rt>`).join("");
    }
    return `${esc(word)}<rt>${esc(pinyin)}</rt>`;
  }

  function renderToken(t, i) {
    if (typeof t === "string") return esc(t);
    const [word, pinyin, level] = t;
    const cls = ["w"];
    if (level === -1) cls.push("k");
    if (level === 0) cls.push("n");
    if (level > 0 && isHard(level)) cls.push("h");
    if (saved[word]) cls.push("saved");
    const showPinyin = pinyin && (settings.pinyin === "all" ||
      (settings.pinyin === "hard" && (level <= 0 || isHard(level))));
    const inner = showPinyin ? `<ruby>${rubyFor(word, pinyin)}</ruby>` : esc(word);
    return `<span class="${cls.join(" ")}" data-i="${i}" role="button" tabindex="0">${inner}</span>`;
  }

  let currentTokens = [];

  async function renderStory(id) {
    setActiveNav("library");
    let s;
    try { s = await getJSON(`data/stories/${encodeURIComponent(id)}.json`); }
    catch { app.innerHTML = `<a class="back" href="#/">← Library</a><p class="empty">Story not found.</p>`; return; }

    currentTokens = [];
    const paras = s.paragraphs.map((p) => `<p>${p.map((t) => {
      if (typeof t === "string") return esc(t);
      currentTokens.push(t);
      return renderToken(t, currentTokens.length - 1);
    }).join("")}</p>`).join("");

    const kws = (s.keywords || []).map((k) => `
      <li><span class="kw">${esc(k.word)}</span><span class="py">${esc(k.pinyin)}</span>
      <span class="en">${esc((k.meanings || []).slice(0, 2).join("; "))}</span></li>`).join("");

    app.innerHTML = `
      <a class="back" href="#/">← Library</a>
      <header class="story-head">
        <div class="card-top">
          <span class="badge">${levelLabel(s.level)}</span>
          <span class="badge cat">${esc(CATEGORIES[s.category] || s.category)}</span>
          <span class="meta">${s.characters} characters · ${Math.round(s.within_target * 100)}% at or below level</span>
        </div>
        <h1>${esc(s.title)}</h1>
        <p>${esc(s.summary)}</p>
      </header>
      ${kws ? `<section class="keywords"><h3>Key words</h3><ul>${kws}</ul></section>` : ""}
      <div class="legend">
        <span class="lk">key word</span><span class="ln">name</span>
        ${settings.markHard ? `<span class="lh">above HSK ${settings.level === 7 ? "7–9" : settings.level}</span>` : ""}
      </div>
      <article class="story ${settings.pinyin !== "off" ? "pinyin" : ""} ${settings.markHard ? "mark-hard" : ""}" lang="zh-CN">${paras}</article>
      <section class="sources">
        <strong>Sources</strong> (retold and simplified):<br>
        ${s.sources.map((u) => `<a href="${esc(u)}" rel="noopener" target="_blank">${esc(decodeURIComponent(u))}</a>`).join("<br>")}
      </section>`;
    app.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }

  // ---------- word popup ----------
  let popupToken = null;
  let selectedEl = null;

  function openPopup(t, el) {
    popupToken = t;
    const [word, pinyin, level, meaning] = t;
    $("popup-word").textContent = word;
    $("popup-level").textContent = levelLabel(level);
    $("popup-pinyin").textContent = pinyin || "";
    $("popup-meaning").textContent = meaning || "No dictionary entry.";
    updateSaveButton();
    $("popup").hidden = false;
    if (selectedEl) selectedEl.classList.remove("sel");
    selectedEl = el; el.classList.add("sel");
  }
  function closePopup() {
    $("popup").hidden = true;
    if (selectedEl) selectedEl.classList.remove("sel");
    selectedEl = null; popupToken = null;
  }
  function updateSaveButton() {
    const isSaved = popupToken && saved[popupToken[0]];
    const btn = $("popup-save");
    btn.textContent = isSaved ? "✓ Saved, tap to remove" : "Save word";
    btn.classList.toggle("ghost", Boolean(isSaved));
  }

  app.addEventListener("click", (e) => {
    const el = e.target.closest(".w");
    if (!el) return;
    openPopup(currentTokens[Number(el.dataset.i)], el);
  });
  app.addEventListener("keydown", (e) => {
    const el = e.target.closest(".w");
    if (el && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); el.click(); }
  });
  $("popup-close").addEventListener("click", closePopup);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePopup(); });
  $("popup-save").addEventListener("click", () => {
    if (!popupToken) return;
    const [word, pinyin, level, meaning] = popupToken;
    if (saved[word]) delete saved[word];
    else saved[word] = { pinyin, level, meaning, added: Date.now() };
    saveWords();
    updateSaveButton();
    document.querySelectorAll(".w").forEach((el) => {
      const t = currentTokens[Number(el.dataset.i)];
      if (t && t[0] === word) el.classList.toggle("saved", Boolean(saved[word]));
    });
  });

  // ---------- saved words page ----------
  function renderWords() {
    setActiveNav("words");
    const entries = Object.entries(saved).sort((a, b) => b[1].added - a[1].added);
    app.innerHTML = `
      <div class="words-head">
        <h1>My words</h1>
        ${entries.length ? `<button class="btn ghost" id="export">Export CSV (for Anki)</button>` : ""}
      </div>
      <p class="meta">Saved in this browser only. ${entries.length} word${entries.length === 1 ? "" : "s"}.</p>
      ${entries.length ? `<ul class="word-list">${entries.map(([w, e]) => `
        <li><span class="zh" lang="zh-CN">${esc(w)}</span><span class="py">${esc(e.pinyin)} · ${levelLabel(e.level)}</span>
        <button class="small-btn rm" data-word="${esc(w)}">Remove</button>
        <span class="en">${esc(e.meaning)}</span></li>`).join("")}</ul>`
        : `<p class="empty">No saved words yet. Tap a word in a story, then “Save word”.</p>`}`;

    app.querySelectorAll(".rm").forEach((b) => b.addEventListener("click", () => {
      delete saved[b.dataset.word]; saveWords(); renderWords();
    }));
    const exp = $("export");
    if (exp) exp.addEventListener("click", () => {
      const q = (s) => `"${String(s ?? "").replace(/"/g, '""')}"`;
      const csv = entries.map(([w, e]) => [w, e.pinyin, e.meaning, levelLabel(e.level)].map(q).join(",")).join("\n");
      const url = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }));
      const a = Object.assign(document.createElement("a"), { href: url, download: "hsk-reader-words.csv" });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }

  // ---------- settings ----------
  $("my-level").value = String(settings.level);
  $("pinyin-mode").value = settings.pinyin;
  $("mark-hard").checked = settings.markHard;
  $("settings-btn").addEventListener("click", () => {
    const panel = $("settings");
    panel.hidden = !panel.hidden;
    $("settings-btn").setAttribute("aria-expanded", String(!panel.hidden));
  });
  const onSetting = () => {
    settings.level = Number($("my-level").value);
    settings.pinyin = $("pinyin-mode").value;
    settings.markHard = $("mark-hard").checked;
    saveSettings();
    route();
  };
  ["my-level", "pinyin-mode", "mark-hard"].forEach((id) => $(id).addEventListener("change", onSetting));

  // ---------- router ----------
  function route() {
    closePopup();
    const hash = location.hash || "#/";
    const m = hash.match(/^#\/story\/(.+)$/);
    if (m) return renderStory(decodeURIComponent(m[1]));
    if (hash === "#/words") return renderWords();
    return renderLibrary();
  }
  window.addEventListener("hashchange", route);
  updateCount();
  route().catch?.((err) => { app.innerHTML = `<p class="empty">Could not load the library (${esc(err.message)}).</p>`; });
})();
