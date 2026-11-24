/* 字游 Ziyou (formerly HSK Reader): a tiny static app. No framework, no server.
 *
 * Data comes pre-processed by tools/build_site.py:
 *   data/library.json         { genres: [...], stories: [...] }
 *   data/stories/<id>.json    chapters -> paragraphs -> tokens:
 *       "，"                                  punctuation / plain text
 *       ["嫦娥", "Cháng é", 0, "Chang'e"]    [word, pinyin, level, meaning]
 *   level: 1-7 = HSK level (7 = 7-9), 10 = beyond HSK, 0 = name, -1 = key word
 *
 * Readers have finished HSK 5, so a "new word" is anything above HSK 5.
 * Settings, the bookshelf and saved words live in this browser only (localStorage).
 *
 * Pages (hash routes):
 *   #/                 Discover: genre tiles
 *   #/genre/<id>       stories in one genre
 *   #/read/<id>/<n>    read chapter n of a story
 *   #/library          bookshelf (saved stories + reading progress)
 *   #/words            saved words; #/words/cards flashcards; #/words/quiz quiz
 */
(() => {
  "use strict";

  const READER_LEVEL = 5;
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
  const settings = Object.assign({ pinyin: "hard", size: "m", theme: "auto" }, store.get("hsk.settings", {}));
  if (!["off", "hard", "all"].includes(settings.pinyin)) settings.pinyin = "hard";
  if (!["auto", "light", "dark"].includes(settings.theme)) settings.theme = "auto";
  const applyTheme = () => {
    if (settings.theme === "auto") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = settings.theme;
  };
  applyTheme();
  let saved = store.get("hsk.saved", {});        // word -> {pinyin, level, meaning, sentence, story, added, box}
  let shelf = store.get("hsk.shelf", {});        // story id -> {added}
  let progress = store.get("hsk.progress", {});  // story id -> {chapter, done: [chapter numbers]}
  const saveSettings = () => store.set("hsk.settings", settings);
  const saveWords = () => { store.set("hsk.saved", saved); updateCount(); };
  const saveShelf = () => store.set("hsk.shelf", shelf);
  const saveProgress = () => store.set("hsk.progress", progress);

  // ---------- helpers ----------
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isNew = (l) => l === 10 || l > READER_LEVEL;
  const fmt = (n) => n.toLocaleString("en-US");
  const CN_NUM = "零一二三四五六七八九十";
  const chapterLabel = (n) => (n <= 10 ? `第${CN_NUM[n]}章` : `第${n}章`);
  const lengthLabel = (s) => (s.chapter_count > 1 ? `${s.chapter_count} chapters` : "One-shot");
  const shuffle = (arr) => {
    const a = [...arr];
    for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; }
    return a;
  };
  const cache = new Map();
  async function getJSON(url) {
    if (!cache.has(url)) {
      // "no-cache" = always ask the server if there's a newer version (new stories show up at once)
      cache.set(url, fetch(url, { cache: "no-cache" }).then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${url}`);
        return r.json();
      }).catch((e) => { cache.delete(url); throw e; }));
    }
    return cache.get(url);
  }
  const getLibrary = () => getJSON("data/library.json");

  function updateCount() {
    const n = Object.keys(saved).length;
    $("saved-count").textContent = n ? String(n) : "";
  }
  function setTab(name) {
    document.querySelectorAll("[data-tab]").forEach((a) => {
      const on = a.dataset.tab === name;
      a.classList.toggle("active", on);
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
  }
  function show(html) {
    app.innerHTML = html;
    app.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }

  const bookmarkIcon = (on) => `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="${on ? "currentColor" : "none"}" stroke="currentColor" stroke-width="2" stroke-linejoin="round" d="M6 3h12v18l-6-4.5L6 21z"/></svg>`;
  const saveStoryBtn = (id) => {
    const on = Boolean(shelf[id]);
    return `<button class="shelf-btn${on ? " on" : ""}" data-shelf="${esc(id)}" aria-pressed="${on}"
      aria-label="${on ? "Remove from library" : "Save to library"}">${bookmarkIcon(on)}<span>${on ? "Saved" : "Save"}</span></button>`;
  };
  function toggleShelf(id) {
    if (shelf[id]) delete shelf[id]; else shelf[id] = { added: Date.now() };
    saveShelf();
  }
  // One delegated handler for every "Save to library" button on any page.
  app.addEventListener("click", (e) => {
    const b = e.target.closest("[data-shelf]");
    if (!b) return;
    e.preventDefault(); e.stopPropagation();
    toggleShelf(b.dataset.shelf);
    b.outerHTML = saveStoryBtn(b.dataset.shelf);
    if (location.hash === "#/library") route();
  });

  function storyCard(s, { showGenre = false, genres = [] } = {}) {
    const p = progress[s.id];
    const genre = genres.find((g) => g.id === s.genre);
    const status = p ? (p.done?.length >= s.chapter_count ? "✓ Finished" : `Reading · ${chapterLabel(p.chapter)}`) : "";
    const start = p && p.chapter ? p.chapter : 1;
    return `
      <article class="card">
        <a class="card-link" href="#/read/${encodeURIComponent(s.id)}/${start}">
          <div class="card-top">
            ${s.date ? `<span class="badge">${esc(newsDate(s.date))}</span>` : `<span class="badge">${lengthLabel(s)}</span>`}
            <span class="meta">${fmt(s.characters)} 字</span>
            ${showGenre && genre ? `<span class="badge cat">${esc(genre.en)}</span>` : ""}
            ${status ? `<span class="meta status">${status}</span>` : ""}
          </div>
          <h2><span class="zh-title" lang="zh-CN">${esc(s.title)}</span><span class="title-en">${esc(s.title_en)}</span></h2>
          <p class="blurb zh" lang="zh-CN">${esc(s.blurb_zh)}</p>
          <p class="blurb">${esc(s.blurb_en)}</p>
        </a>
        ${saveStoryBtn(s.id)}
      </article>`;
  }

  // ---------- Discover: news on top, then genre tiles ----------
  const newsDate = (d, withYear = false) => {
    const t = d ? new Date(d + "T00:00:00") : null;
    return t && !isNaN(t)
      ? t.toLocaleDateString("en-GB", { day: "numeric", month: "short", ...(withYear && { year: "numeric" }) }) : "";
  };
  const byDate = (a, b) => String(b.date || "").localeCompare(String(a.date || ""));

  function genreTile(g, n, extra = "") {
    const soon = g.soon || n === 0;
    const inner = `
      <span class="tile-mark" lang="zh-CN" aria-hidden="true">${esc(g.mark)}</span>
      <span class="tile-zh" lang="zh-CN">${esc(g.zh)}</span>
      <span class="tile-en">${esc(g.en)}</span>
      <span class="tile-tag">${esc(g.tagline)}</span>
      <span class="tile-count">${soon ? "Coming soon" : `${n} ${n === 1 ? "story" : "stories"}`}</span>`;
    return soon
      ? `<div class="tile soon g-${esc(g.id)} ${extra}" aria-disabled="true">${inner}</div>`
      : `<a class="tile g-${esc(g.id)} ${extra}" href="#/genre/${esc(g.id)}">${inner}</a>`;
  }

  // News source: its own field, or the "（IT之家）" an older digest put at the end of its blurb.
  function newsParts(s) {
    const m = String(s.blurb_zh || "").match(/^(.*?)（([^（）]+)）\s*$/);
    return { source: s.source || (m ? m[2] : ""), blurb: m && !s.source ? m[1] : s.blurb_zh || "" };
  }

  function newsItem(s) {
    const { source, blurb } = newsParts(s);
    return `
      <a class="news-item" href="#/read/${encodeURIComponent(s.id)}/1">
        <span class="news-date">${esc(newsDate(s.date, true))}</span>
        ${source ? `<span class="news-source" lang="zh-CN">${esc(source)}</span>` : ""}
        <span class="news-headline" lang="zh-CN">${esc(s.title)}</span>
        <span class="news-blurb" lang="zh-CN">${esc(blurb)}</span>
      </a>`;
  }

  function newsSection(g, items) {
    if (!items.length) return `<div class="tiles news-only">${genreTile(g, 0, "wide")}</div>`;
    return `
      <section class="news g-news" aria-label="${esc(g.en)}">
        <div class="section-row">
          <h2 class="section-title"><span lang="zh-CN">${esc(g.zh)}</span> · ${esc(g.en)}</h2>
          <a class="news-all" href="#/genre/${esc(g.id)}">See all →</a>
        </div>
        <div class="news-list">${items.slice(0, 3).map(newsItem).join("")}</div>
      </section>`;
  }

  async function renderDiscover() {
    setTab("discover");
    const { genres, stories } = await getLibrary();
    const count = (g) => stories.filter((s) => s.genre === g.id).length;
    const newsGenre = genres.find((g) => g.id === "news");
    const news = stories.filter((s) => s.genre === "news").sort(byDate);
    show(`
      <section class="hero">
        <h1>What do you feel like reading?</h1>
      </section>
      ${newsGenre ? newsSection(newsGenre, news) : ""}
      <h2 class="section-title"><span lang="zh-CN">故事</span> · Stories</h2>
      <div class="tiles bento">
        ${genres.filter((g) => g.id !== "news")
          .map((g) => ({ g, n: count(g) }))
          .sort((a, b) => (a.g.soon || !a.n) - (b.g.soon || !b.n) || b.n - a.n)   // most stories first, "soon" last
          .map(({ g, n }, i) => genreTile(g, n, i < 2 ? "big" : "")).join("")}
      </div>`);
  }

  // ---------- one genre: list of stories ----------
  async function renderGenre(id) {
    setTab("discover");
    const { genres, stories } = await getLibrary();
    const g = genres.find((x) => x.id === id);
    if (!g) return show(`<a class="back" href="#/">← All genres</a><p class="empty">Genre not found.</p>`);
    const list = stories.filter((s) => s.genre === id)
      .sort(id === "news" ? byDate : (a, b) => a.chapter_count - b.chapter_count || a.characters - b.characters);
    show(`
      <a class="back" href="#/">← All genres</a>
      <header class="genre-head g-${esc(g.id)}">
        <span class="tile-mark" lang="zh-CN" aria-hidden="true">${esc(g.mark)}</span>
        <div>
          <h1><span lang="zh-CN">${esc(g.zh)}</span> · ${esc(g.en)}</h1>
          <p>${esc(g.tagline)}</p>
        </div>
      </header>
      <div class="cards">${list.map((s) => storyCard(s)).join("") || `<p class="empty">Stories coming soon.</p>`}</div>`);
  }

  // ---------- reader ----------
  function rubyFor(word, pinyin) {
    const syl = pinyin.split(/\s+/).filter(Boolean);
    const chars = [...word];
    if (syl.length === chars.length) return chars.map((c, i) => `${esc(c)}<rt>${esc(syl[i])}</rt>`).join("");
    return `${esc(word)}<rt>${esc(pinyin)}</rt>`;
  }

  function renderToken(t, i) {
    const [word, pinyin, level] = t;
    const cls = ["w"];
    if (level === 0) cls.push("n");
    if (saved[word]) cls.push("saved");
    const showPinyin = pinyin && (settings.pinyin === "all" ||
      (settings.pinyin === "hard" && (level <= 0 || isNew(level))));
    const inner = showPinyin ? `<ruby>${rubyFor(word, pinyin)}</ruby>` : esc(word);
    return `<span class="${cls.join(" ")}" data-i="${i}" role="button" tabindex="0">${inner}</span>`;
  }

  // Tokens on the current page, plus the sentence each one sits in
  // (saved with a word so flashcards can show it in context).
  let currentTokens = [];
  let tokenSentence = [];
  let currentStory = null;

  function renderParagraph(tokens) {
    const sentenceEnd = /[。！？!?]/;
    let sentence = "";
    let pending = [];
    const flush = () => {
      const text = sentence.replace(/^[”’」』）)\s]+/, "").trim(); // a closing quote belongs to the previous sentence
      pending.forEach((i) => { tokenSentence[i] = text; });
      sentence = ""; pending = [];
    };
    const html = tokens.map((t) => {
      if (typeof t === "string") {
        sentence += t;
        if (sentenceEnd.test(t)) flush();
        return esc(t);
      }
      currentTokens.push(t);
      const i = currentTokens.length - 1;
      sentence += t[0];
      pending.push(i);
      return renderToken(t, i);
    }).join("");
    flush();
    return `<p>${html}</p>`;
  }

  async function renderRead(id, n) {
    setTab(shelf[id] ? "library" : "discover");
    let s, lib;
    try { [s, lib] = await Promise.all([getJSON(`data/stories/${encodeURIComponent(id)}.json`), getLibrary()]); }
    catch { return show(`<a class="back" href="#/">← Discover</a><p class="empty">Story not found.</p>`); }
    const total = s.chapters.length;
    n = Math.min(Math.max(1, n || 1), total);
    const ch = s.chapters[n - 1];
    const genre = lib.genres.find((g) => g.id === s.genre);
    currentStory = s;

    // remember where the reader is
    const p = progress[id] || { chapter: 1, done: [] };
    p.chapter = n;
    progress[id] = p;
    saveProgress();

    currentTokens = []; tokenSentence = [];
    // titles are tappable too: render them like a one-line paragraph
    const inline = (tokens) => renderParagraph(tokens || []).replace(/^<p>|<\/p>$/g, "");
    const titleHtml = s.title_tokens ? inline(s.title_tokens) : esc(s.title);
    const chapterTitleHtml = ch.title_tokens ? inline(ch.title_tokens) : esc(ch.title);
    const paras = ch.paragraphs.map(renderParagraph).join("");
    const pinyinTitles = settings.pinyin !== "off" ? "pinyin" : "";

    const chapterNav = total > 1 ? `
      <nav class="chapter-nav" aria-label="Chapters">
        ${n > 1 ? `<a class="btn ghost" href="#/read/${encodeURIComponent(id)}/${n - 1}">← ${chapterLabel(n - 1)}</a>` : "<span></span>"}
        <select id="chapter-select" aria-label="Go to chapter">
          ${s.chapters.map((c, i) => `<option value="${i + 1}" ${i + 1 === n ? "selected" : ""}>${esc(c.title)}</option>`).join("")}
        </select>
        ${n < total ? `<a class="btn" id="next-ch" href="#/read/${encodeURIComponent(id)}/${n + 1}">${chapterLabel(n + 1)} →</a>` : "<span></span>"}
      </nav>` : "";

    const finished = n === total;
    const sources = (s.sources || []).length ? `
      <section class="sources">
        <strong>Sources</strong> (retold for learners):
        ${s.sources.map((u) => `<a href="${esc(u)}" rel="noopener" target="_blank">${esc(decodeURIComponent(u))}</a>`).join(" · ")}
      </section>` : `<section class="sources">An original story written for 字游 Ziyou.</section>`;

    show(`
      <div class="read-bar">
        <a class="back" href="${genre ? `#/genre/${esc(genre.id)}` : "#/"}">← ${esc(genre ? genre.en : "Discover")}</a>
        <div class="title-actions">
          <button class="aa-btn" id="aa-btn" aria-expanded="false" aria-controls="read-settings" aria-label="阅读设置 Reading settings">Aa</button>
          ${saveStoryBtn(id)}
        </div>
      </div>
      <header class="story-head">
        <h1 class="${pinyinTitles}" lang="zh-CN">${titleHtml}</h1>
      </header>
      ${readSettingsPanel()}
      ${ch.title ? `<h2 class="chapter-title ${pinyinTitles}" lang="zh-CN">${chapterTitleHtml}</h2>` : ""}
      <article class="story size-${esc(settings.size)} ${settings.pinyin !== "off" ? "pinyin" : ""}" lang="zh-CN">${paras}</article>
      ${chapterNav}
      ${finished ? `<div class="the-end" lang="zh-CN"><span>全文完</span><span aria-hidden="true">·</span>
        <a class="btn" href="#/words">复习生词 →</a></div>` : ""}
      ${sources}`);

    // chapter counts as read when the reader moves on (or reaches the end)
    const markDone = (num) => {
      const pr = progress[id];
      if (!pr.done.includes(num)) { pr.done.push(num); saveProgress(); }
    };
    const next = $("next-ch");
    if (next) next.addEventListener("click", () => markDone(n));
    if (finished) markDone(n);
    const sel = $("chapter-select");
    if (sel) sel.addEventListener("change", () => { location.hash = `#/read/${encodeURIComponent(id)}/${sel.value}`; });
    wireReadSettings();
  }

  // ---------- reading settings (only on the story page, in Chinese) ----------
  const SETTING_GROUPS = [
    { key: "pinyin", label: "拼音", options: [["off", "关闭"], ["all", "全部"], ["hard", "仅生词"]] },
    { key: "size", label: "字号", options: [["s", "小"], ["m", "中"], ["l", "大"]] },
    { key: "theme", label: "主题", options: [["light", "浅色"], ["dark", "深色"], ["auto", "跟随系统"]] },
  ];
  let settingsOpen = false;
  function readSettingsPanel() {
    return `
      <section class="read-settings" id="read-settings" ${settingsOpen ? "" : "hidden"} lang="zh-CN">
        ${SETTING_GROUPS.map((g) => `
          <div class="rs-row">
            <span class="rs-label">${g.label}</span>
            <div class="seg" role="group" aria-label="${g.label}">
              ${g.options.map(([v, text]) => `<button data-set="${g.key}" data-val="${v}" aria-pressed="${settings[g.key] === v}">${text}</button>`).join("")}
            </div>
          </div>`).join("")}
      </section>`;
  }
  function wireReadSettings() {
    const btn = $("aa-btn"), panel = $("read-settings");
    if (!btn || !panel) return;
    btn.setAttribute("aria-expanded", String(settingsOpen));
    btn.addEventListener("click", () => {
      settingsOpen = panel.hidden;
      panel.hidden = !settingsOpen;
      btn.setAttribute("aria-expanded", String(settingsOpen));
    });
    panel.addEventListener("click", (e) => {
      const b = e.target.closest("[data-set]");
      if (!b) return;
      settings[b.dataset.set] = b.dataset.val;
      saveSettings();
      if (b.dataset.set === "theme") {
        applyTheme();
        panel.querySelectorAll('[data-set="theme"]').forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
        return;
      }
      const y = window.scrollY;
      Promise.resolve(route()).then(() => window.scrollTo(0, y));
    });
  }

  // ---------- word popup ----------
  let popupToken = null;
  let popupIndex = -1;
  let selectedEl = null;

  function openPopup(i, el) {
    popupIndex = i;
    popupToken = currentTokens[i];
    const [word, pinyin, level, meaning] = popupToken;
    $("popup-word").textContent = word;
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
    selectedEl = null; popupToken = null; popupIndex = -1;
  }
  function updateSaveButton() {
    const isSaved = popupToken && saved[popupToken[0]];
    const btn = $("popup-save");
    btn.textContent = isSaved ? "✓ 已加入生词本 · 点击移除" : "＋ 加入生词本";
    btn.classList.toggle("ghost", Boolean(isSaved));
  }

  app.addEventListener("click", (e) => {
    const el = e.target.closest(".w");
    if (!el) return;
    openPopup(Number(el.dataset.i), el);
  });
  app.addEventListener("keydown", (e) => {
    const el = e.target.closest(".w");
    if (el && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); el.click(); }
  });
  $("popup-close").addEventListener("click", closePopup);
  // Tap anywhere outside the popup (and not on another word) to close it.
  document.addEventListener("click", (e) => {
    if ($("popup").hidden) return;
    if (e.target.closest("#popup") || e.target.closest(".w")) return;
    closePopup();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePopup(); });
  $("popup-save").addEventListener("click", () => {
    if (!popupToken) return;
    const [word, pinyin, level, meaning] = popupToken;
    if (saved[word]) delete saved[word];
    else saved[word] = {
      pinyin, level, meaning, added: Date.now(), box: 0,
      sentence: tokenSentence[popupIndex] || "",
      story: currentStory ? currentStory.title : "",
    };
    saveWords();
    updateSaveButton();
    document.querySelectorAll(".w").forEach((el) => {
      const t = currentTokens[Number(el.dataset.i)];
      if (t && t[0] === word) el.classList.toggle("saved", Boolean(saved[word]));
    });
  });

  // ---------- Library (bookshelf) ----------
  async function renderLibrary() {
    setTab("library");
    const { genres, stories } = await getLibrary();
    const mine = stories.filter((s) => shelf[s.id]).sort((a, b) => shelf[b.id].added - shelf[a.id].added);
    show(`
      <div class="page-head">
        <h1><span lang="zh-CN">书架</span> Library</h1>
      </div>
      <p class="meta">Stories you saved, with where you left off. Saved in this browser only.</p>
      <div class="cards">
        ${mine.map((s) => storyCard(s, { showGenre: true, genres })).join("") ||
          `<p class="empty">Nothing here yet. Open a story and tap <strong>Save</strong> to keep it on your shelf.<br><br>
           <a class="btn" href="#/">Discover stories</a></p>`}
      </div>`);
  }

  // ---------- My Words ----------
  const wordEntries = () => Object.entries(saved).sort((a, b) => b[1].added - a[1].added);

  function renderWords() {
    setTab("words");
    const entries = wordEntries();
    const n = entries.length;
    show(`
      <div class="page-head">
        <h1><span lang="zh-CN">生词本</span> My Words</h1>
        ${n ? `<button class="small-btn" id="export">Export CSV (Anki)</button>` : ""}
      </div>
      <p class="meta">${n} word${n === 1 ? "" : "s"} · saved in this browser only.</p>
      ${n ? `
      <div class="practice">
        <a class="practice-card" href="#/words/cards">
          <span class="pc-zh" lang="zh-CN">闪卡</span><span class="pc-en">Flashcards</span>
          <span class="pc-sub">Flip each card: do you remember it?</span>
        </a>
        <a class="practice-card ${n < 4 ? "disabled" : ""}" href="${n < 4 ? "#/words" : "#/words/quiz"}" ${n < 4 ? 'aria-disabled="true"' : ""}>
          <span class="pc-zh" lang="zh-CN">小测验</span><span class="pc-en">Quiz</span>
          <span class="pc-sub">${n < 4 ? "Save at least 4 words to unlock" : "Multiple choice, 10 questions"}</span>
        </a>
      </div>
      <ul class="word-list">${entries.map(([w, e]) => `
        <li>
          <span class="zh" lang="zh-CN">${esc(w)}</span>
          <span class="py">${esc(e.pinyin)}${e.box >= 3 ? ` <span class="known">✓ known</span>` : ""}</span>
          <button class="small-btn rm" data-word="${esc(w)}" aria-label="Remove ${esc(w)}">Remove</button>
          <span class="en">${esc(e.meaning)}</span>
          ${e.sentence ? `<span class="ex" lang="zh-CN">${esc(e.sentence)}</span>` : ""}
        </li>`).join("")}</ul>`
      : `<p class="empty">No saved words yet. While reading, tap a word, then <strong>+ Save to My Words</strong>.</p>`}`);

    app.querySelectorAll(".rm").forEach((b) => b.addEventListener("click", () => {
      delete saved[b.dataset.word]; saveWords(); renderWords();
    }));
    const exp = $("export");
    if (exp) exp.addEventListener("click", () => {
      const q = (s) => `"${String(s ?? "").replace(/"/g, '""')}"`;
      const csv = entries.map(([w, e]) => [w, e.pinyin, e.meaning, e.sentence].map(q).join(",")).join("\n");
      const url = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }));
      const a = Object.assign(document.createElement("a"), { href: url, download: "ziyou-words.csv" });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }

  // Highlight the word inside its saved sentence.
  const sentenceHtml = (sentence, word) => esc(sentence).split(esc(word)).join(`<mark>${esc(word)}</mark>`);

  // ---------- Flashcards ----------
  // "box" is a tiny spaced-repetition score: Got it +1, Again -> 0.
  // Words with the lowest box come first, so the hard ones come back sooner.
  function renderCards() {
    setTab("words");
    const deck = shuffle(wordEntries()).sort((a, b) => (a[1].box || 0) - (b[1].box || 0)).slice(0, 20);
    if (!deck.length) { location.hash = "#/words"; return; }
    let i = 0, known = 0, flipped = false;

    const draw = () => {
      if (i >= deck.length) {
        show(`
          <a class="back" href="#/words">← My Words</a>
          <div class="done-box">
            <div class="done-big" lang="zh-CN">完成！</div>
            <p>You knew <strong>${known}</strong> of ${deck.length} cards.</p>
            <div class="done-actions">
              <a class="btn" href="#/words/cards" id="again-deck">Go again</a>
              ${Object.keys(saved).length >= 4 ? `<a class="btn ghost" href="#/words/quiz">Take the quiz</a>` : ""}
            </div>
          </div>`);
        $("again-deck").addEventListener("click", (e) => { e.preventDefault(); renderCards(); });
        return;
      }
      const [w, e] = deck[i];
      flipped = false;
      show(`
        <a class="back" href="#/words">← My Words</a>
        <div class="progress-row"><span>Card ${i + 1} / ${deck.length}</span><span>Known ${known}</span></div>
        <div class="progress-bar"><span style="width:${(i / deck.length) * 100}%"></span></div>
        <button class="flashcard" id="card" aria-live="polite">
          <span class="fc-word" lang="zh-CN">${esc(w)}</span>
          <span class="fc-back" hidden>
            <span class="fc-py">${esc(e.pinyin)}</span>
            <span class="fc-en">${esc(e.meaning)}</span>
            ${e.sentence ? `<span class="fc-ex" lang="zh-CN">${sentenceHtml(e.sentence, w)}</span>` : ""}
            ${e.story ? `<span class="fc-src">from ${esc(e.story)}</span>` : ""}
          </span>
          <span class="fc-hint">Tap to flip</span>
        </button>
        <div class="fc-actions" id="fc-actions" hidden>
          <button class="btn ghost" id="fc-again"><span lang="zh-CN">再看看</span> Again</button>
          <button class="btn" id="fc-got"><span lang="zh-CN">记住了</span> Got it</button>
        </div>`);
      const flip = () => {
        if (flipped) return;
        flipped = true;
        app.querySelector(".fc-back").hidden = false;
        app.querySelector(".fc-hint").hidden = true;
        $("fc-actions").hidden = false;
        $("card").classList.add("flipped");
      };
      $("card").addEventListener("click", flip);
      const answer = (good) => {
        const entry = saved[w];
        if (entry) { entry.box = good ? (entry.box || 0) + 1 : 0; saveWords(); }
        if (good) known++;
        i++; draw();
      };
      $("fc-again").addEventListener("click", () => answer(false));
      $("fc-got").addEventListener("click", () => answer(true));
    };
    draw();
  }

  // ---------- Quiz ----------
  // Two kinds of question, mixed: "what does 词 mean?" and "which word means …?".
  function renderQuiz() {
    setTab("words");
    const all = wordEntries();
    if (all.length < 4) { location.hash = "#/words"; return; }
    const qs = shuffle(all).slice(0, 10).map(([w, e], k) => {
      const others = shuffle(all.filter(([x]) => x !== w)).slice(0, 3);
      const options = shuffle([[w, e], ...others]);
      return { w, e, options, reverse: k % 2 === 1 };
    });
    let i = 0, score = 0;
    const missed = [];

    const draw = () => {
      if (i >= qs.length) {
        show(`
          <a class="back" href="#/words">← My Words</a>
          <div class="done-box">
            <div class="done-big">${score} / ${qs.length}</div>
            <p>${score === qs.length ? "Perfect! <span lang=\"zh-CN\">太棒了！</span>" : score >= qs.length * 0.7 ? "Nice work. <span lang=\"zh-CN\">不错！</span>" : "Keep reading, they'll stick. <span lang=\"zh-CN\">加油！</span>"}</p>
            ${missed.length ? `<ul class="missed">${missed.map(([w, e]) => `<li><span lang="zh-CN">${esc(w)}</span> ${esc(e.pinyin)}: ${esc(e.meaning)}</li>`).join("")}</ul>` : ""}
            <div class="done-actions">
              <a class="btn" href="#/words/quiz" id="again-quiz">New quiz</a>
              <a class="btn ghost" href="#/words/cards">Flashcards</a>
            </div>
          </div>`);
        $("again-quiz").addEventListener("click", (ev) => { ev.preventDefault(); renderQuiz(); });
        return;
      }
      const q = qs[i];
      const prompt = q.reverse
        ? `<div class="q-prompt-en">${esc(q.e.meaning)}</div><div class="q-sub">Which word means this?</div>`
        : `<div class="q-prompt" lang="zh-CN">${esc(q.w)}</div><div class="q-sub">What does it mean?</div>`;
      show(`
        <a class="back" href="#/words">← My Words</a>
        <div class="progress-row"><span>Question ${i + 1} / ${qs.length}</span><span>Score ${score}</span></div>
        <div class="progress-bar"><span style="width:${(i / qs.length) * 100}%"></span></div>
        <div class="quiz-card">${prompt}</div>
        <div class="options">
          ${q.options.map(([w, e], k) => `<button class="option" data-k="${k}" ${q.reverse ? 'lang="zh-CN"' : ""}>
            ${q.reverse ? esc(w) : esc(e.meaning)}</button>`).join("")}
        </div>
        <div class="quiz-feedback" id="feedback" aria-live="polite"></div>`);
      const buttons = [...app.querySelectorAll(".option")];
      buttons.forEach((b) => b.addEventListener("click", () => {
        const pick = q.options[Number(b.dataset.k)][0];
        const right = pick === q.w;
        buttons.forEach((x) => {
          x.disabled = true;
          if (q.options[Number(x.dataset.k)][0] === q.w) x.classList.add("right");
        });
        if (right) score++; else { b.classList.add("wrong"); missed.push([q.w, q.e]); }
        $("feedback").innerHTML = `
          <p><strong lang="zh-CN">${esc(q.w)}</strong> ${esc(q.e.pinyin)}: ${esc(q.e.meaning)}</p>
          ${q.e.sentence ? `<p class="fc-ex" lang="zh-CN">${sentenceHtml(q.e.sentence, q.w)}</p>` : ""}
          <button class="btn" id="next-q">${i + 1 < qs.length ? "Next →" : "See results"}</button>`;
        $("next-q").addEventListener("click", () => { i++; draw(); });
        $("next-q").focus();
      }));
    };
    draw();
  }

  // ---------- router ----------
  function route() {
    closePopup();
    const hash = location.hash || "#/";
    let m;
    if ((m = hash.match(/^#\/read\/([^/]+)(?:\/(\d+))?$/))) return renderRead(decodeURIComponent(m[1]), Number(m[2] || 1));
    if ((m = hash.match(/^#\/story\/([^/]+)$/))) { location.replace(`#/read/${m[1]}/1`); return; } // old links
    if ((m = hash.match(/^#\/genre\/([^/]+)$/))) return renderGenre(decodeURIComponent(m[1]));
    if (hash === "#/library") return renderLibrary();
    if (hash === "#/words/cards") return renderCards();
    if (hash === "#/words/quiz") return renderQuiz();
    if (hash === "#/words") return renderWords();
    return renderDiscover();
  }
  window.addEventListener("hashchange", () => {
    Promise.resolve(route()).catch((err) => show(`<p class="empty">Could not load (${esc(err.message)}).</p>`));
  });
  updateCount();
  Promise.resolve(route()).catch((err) => show(`<p class="empty">Could not load the library (${esc(err.message)}).</p>`));

  // ---------- installable app (PWA) ----------
  if ("serviceWorker" in navigator && location.protocol !== "file:") {
    window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
  }
})();
