/* 字游 Ziyou (formerly HSK Reader): a tiny static app. No framework, no server.
 *
 * Data comes pre-processed by tools/build_site.py:
 *   data/library.json         { genres: [...], stories: [...] }
 *   data/stories/<id>.json    chapters -> paragraphs -> tokens:
 *       "，"                                  punctuation / plain text
 *       ["嫦娥", "Cháng é", 0, "Chang'e"]    [word, pinyin, level, meaning]
 *   level: 1-7 = HSK level (7 = 7-9), 10 = beyond HSK, 0 = name, -1 = key word
 *
 * In 高级 a "new word" is anything above HSK 5, in 中级 anything above HSK 4.
 * Settings, the bookshelf and saved words live in this browser only (localStorage).
 *
 * Pages (hash routes):
 *   #/                 Discover: genre tiles
 *   #/genre/<id>       stories in one genre
 *   #/read/<id>/<n>    read chapter n of a story
 *   #/me               我的: recently read; #/me/saved saved stories; #/me/highlights highlights
 *   #/words            saved words; #/words/cards flashcards; #/words/quiz quiz
 */
(() => {
  "use strict";

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
  const settings = Object.assign({ pinyin: "hard", size: "m", theme: "auto", level: "high" }, store.get("hsk.settings", {}));
  if (!["off", "hard", "all"].includes(settings.pinyin)) settings.pinyin = "hard";
  if (!["auto", "light", "dark"].includes(settings.theme)) settings.theme = "auto";
  if (!["mid", "high"].includes(settings.level)) settings.level = "high";
  const isMid = () => settings.level === "mid";          // 中级 (HSK 4) or 高级 (HSK 6)
  const readerLevel = () => (isMid() ? 4 : 5);           // a word above this is a "new word"
  const applyLevel = () => {                             // 中级 turns the app green (style.css)
    document.documentElement.dataset.level = settings.level;
    const bar = document.querySelector('meta[name="theme-color"][media*="light"]');
    if (bar) bar.content = isMid() ? "#eef7f1" : "#f4f0fd";
  };
  applyLevel();
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
  // Saved words are keyed "word|pinyin", so 还 hái and 还 huán are two words.
  // Older saves were keyed by the word alone: move them over once.
  const wordKey = (word, pinyin) => `${word}|${(pinyin || "").toLowerCase()}`;
  const wordOf = (key, e) => e.word || key.split("|")[0];
  if (Object.keys(saved).some((k) => !k.includes("|"))) {
    saved = Object.fromEntries(Object.entries(saved).map(([k, e]) =>
      k.includes("|") ? [k, e] : [wordKey(k, e.pinyin), { ...e, word: k }]));
    store.set("hsk.saved", saved);
  }
  const saveShelf = () => store.set("hsk.shelf", shelf);
  const saveProgress = () => store.set("hsk.progress", progress);
  // Older saves don't say whether the reader scrolled. Back then only opening a story gave
  // about 10-15% on a phone, so more than 20% means they really read: convert once.
  if (Object.values(progress).some((p) => p && !("scrolled" in p))) {
    for (const p of Object.values(progress)) if (p && !("scrolled" in p)) p.scrolled = (p.pct || 0) > 20;
    saveProgress();
  }
  // progress[id] also keeps: pos {chapter, para} (where the reader stopped), pct (one-shots), last (time)
  let highlights = store.get("hsk.highlights", []);  // [{id, file, story, title, chapter, from, to, color, text, added}]
  const saveHighlights = () => store.set("hsk.highlights", highlights);
  let bookmarks = store.get("hsk.bookmarks", {});   // story id -> [{id, file, chapter, para, text, added}]
  const saveBookmarks = () => store.set("hsk.bookmarks", bookmarks);

  // ---------- helpers ----------
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isNew = (l) => l === 10 || l > readerLevel();
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
  // In 中级, stories show their easier version (title, summary, length) where there is one.
  async function getLibrary() {
    const lib = await getJSON("data/library.json");
    if (!store.get("hsk.doneFix", false)) {
      // Before, opening a story's last chapter marked it finished. Undo that once
      // (stories read since this change have a saved position, "pos").
      for (const s of lib.stories) {
        const p = progress[s.id];
        if (p && !p.pos && p.done) p.done = p.done.filter((n) => n !== s.chapter_count && n !== s.mid?.chapter_count);
      }
      saveProgress();
      store.set("hsk.doneFix", true);
    }
    if (!isMid()) return lib;
    return { ...lib, stories: lib.stories.map((s) => (s.mid ? { ...s, ...s.mid } : { ...s, onlyHigh: true })) };
  }
  const onlyHighBadge = (s) => (s.onlyHigh ? `<span class="badge only-high" lang="zh-CN">仅高级</span>` : "");

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
    wireReadSettings();
    wireSortPanel();
    syncPanels();
    app.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }

  // Round back arrow on the left, page actions (settings, Save) on the right.
  const backBtn = (href, label, text = "") => `<a class="back${text ? " with-text" : ""}" href="${href}" aria-label="Back to ${esc(label)}" title="${esc(label)}">
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" d="M19 12H5m6-6-6 6 6 6"/></svg>${text ? `<span lang="zh-CN">${esc(text)}</span>` : ""}</a>`;
  const pageBar = (href, label, actions = "", text = "") =>
    `<div class="read-bar">${backBtn(href, label, text)}<div class="title-actions">${actions}</div></div>`;
  const aaBtn = `<button class="aa-btn" id="aa-btn" aria-expanded="false" aria-controls="read-settings" aria-label="阅读设置 Reading settings" title="Reading settings"><svg viewBox="0 0 24 24" width="19" height="19" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M10.3 5.2L10.4 2.1L13.6 2.1L13.7 5.2L15.6 6.0L17.9 3.9L20.1 6.1L18.0 8.4L18.8 10.3L21.9 10.4L21.9 13.6L18.8 13.7L18.0 15.6L20.1 17.9L17.9 20.1L15.6 18.0L13.7 18.8L13.6 21.9L10.4 21.9L10.3 18.8L8.4 18.0L6.1 20.1L3.9 17.9L6.0 15.6L5.2 13.7L2.1 13.6L2.1 10.4L5.2 10.3L6.0 8.4L3.9 6.1L6.1 3.9L8.4 6.0z"/><circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="2"/></svg></button>`;

  const heartIcon = (on) => `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="${on ? "currentColor" : "none"}" stroke="currentColor" stroke-width="2" stroke-linejoin="round" d="M12 20.5s-7.5-4.6-9.3-9.4C1.6 8 3.6 4.5 7 4.5c2 0 3.4 1.1 5 3 1.6-1.9 3-3 5-3 3.4 0 5.4 3.5 4.3 6.6-1.8 4.8-9.3 9.4-9.3 9.4z"/></svg>`;
  const bookmarkIcon = (on) => `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="${on ? "currentColor" : "none"}" stroke="currentColor" stroke-width="2" stroke-linejoin="round" d="M6 3h12v18l-6-4.5L6 21z"/></svg>`;
  const saveStoryBtn = (id) => {
    const on = Boolean(shelf[id]);
    return `<button class="shelf-btn${on ? " on" : ""}" data-shelf="${esc(id)}" aria-pressed="${on}"
      aria-label="收藏 Save story" title="${on ? "Remove from saved" : "Save story"}">${heartIcon(on)}</button>`;
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
    if (location.hash.startsWith("#/me")) route();
    else refreshGenreList();
  });

  // Chinese text on a card: pinyin follows the Aa settings, but words are not tappable (the card is a link).
  const showsPinyin = ([, pinyin, level]) => pinyin && (settings.pinyin === "all" ||
    (settings.pinyin === "hard" && (level <= 0 || isNew(level))));
  const cardText = (tokens, text) => (tokens && tokens.length
    ? tokens.map((t) => (typeof t === "string" ? esc(t)
      : showsPinyin(t) ? `<ruby>${rubyFor(t[0], t[1])}</ruby>` : esc(t[0]))).join("")
    : esc(text));
  const listClass = () => `size-${esc(settings.size)}${settings.pinyin !== "off" ? " pinyin" : ""}`;

  // Has the reader really read some of it? Scrolling with a finger, wheel or keys counts;
  // only opening a story does not.
  const hasRead = (p) => Boolean(p && (p.scrolled || p.done?.length || p.chapter > 1));
  const readPct = (s, p) => Math.max(1, p.pct || Math.round(((p.chapter || 1) - 1) / s.chapter_count * 100));
  const readingStatus = (s, p) => !p ? "" : isFinished(s) ? "✓ Finished" : !hasRead(p) ? "Opened"
    : `Reading · ${readPct(s, p)}%`;

  function storyCard(s, { showGenre = false, genres = [] } = {}) {
    const p = progress[s.id];
    const genre = genres.find((g) => g.id === s.genre);
    const status = readingStatus(s, p);
    const start = p && p.chapter ? p.chapter : 1;
    return `
      <article class="card">
        <a class="card-link" href="#/read/${encodeURIComponent(s.id)}/${start}">
          <div class="card-top">
            ${s.date ? `<span class="badge">${esc(newsDate(s.date))}</span>` : `<span class="badge">${lengthLabel(s)}</span>`}
            <span class="meta">${fmt(s.characters)} 字</span>
            ${onlyHighBadge(s)}
            ${showGenre && genre ? `<span class="badge cat">${esc(genre.en)}</span>` : ""}
            ${status ? `<span class="meta status">${status}</span>` : ""}
          </div>
          <h2><span class="zh-title" lang="zh-CN">${cardText(s.title_tokens, s.title)}</span><span class="title-en">${esc(s.title_en)}</span></h2>
          ${s.genre === "news" ? "" : `<p class="blurb zh" lang="zh-CN">${cardText(s.blurb_tokens, s.blurb_zh)}</p>`}
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
  function newsSource(s) {
    const m = String(s.blurb_zh || "").match(/（([^（）]+)）\s*$/);
    return s.source || (m ? m[1] : "");
  }

  function newsItem(s) {
    const source = newsSource(s);
    return `
      <a class="news-item" href="#/read/${encodeURIComponent(s.id)}/1">
        <span class="news-meta"><span class="news-date">${esc(newsDate(s.date, true))}</span>${
          source ? `<span class="news-source" lang="zh-CN">${esc(source)}</span>` : ""}</span>
        <span class="news-headline" lang="zh-CN">${cardText(s.title_tokens, s.title)}</span>
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
        <div class="news-list ${listClass()}">${items.slice(0, 2).map(newsItem).join("")}</div>
      </section>`;
  }

  // The story read most recently and not finished yet, to pick up where the reader stopped.
  function continueCard(stories) {
    const s = stories.filter((x) => progress[x.id]?.last && !isFinished(x))
      .sort((a, b) => progress[b.id].last - progress[a.id].last)[0];
    if (!s) return "";
    const p = progress[s.id];
    const where = !hasRead(p) ? "已打开" : `${readPct(s, p)}%`;
    return `
      <a class="continue-card" href="#/read/${encodeURIComponent(s.id)}/${p.chapter || 1}">
        <span class="cc-label" lang="zh-CN">继续阅读</span>
        <span class="cc-title" lang="zh-CN">${esc(s.title)}</span>
        <span class="cc-meta" lang="zh-CN">${where} →</span>
      </a>`;
  }

  async function renderDiscover() {
    setTab("discover");
    const { genres, stories } = await getLibrary();
    const count = (g) => stories.filter((s) => s.genre === g.id).length;
    const newsGenre = genres.find((g) => g.id === "news");
    const news = stories.filter((s) => s.genre === "news").sort(byDate);
    show(`
      <section class="hero">
        <h1 lang="zh-CN">今天想读什么？</h1>
        <div class="title-actions">
          <div class="seg level-toggle" id="level-toggle" role="group" aria-label="难度 Level" lang="zh-CN">
            <button data-level="mid" aria-pressed="${isMid()}" title="HSK 4">中级</button><button data-level="high" aria-pressed="${!isMid()}" title="HSK 6">高级</button>
          </div>
          ${aaBtn}
        </div>
      </section>
      ${readSettingsPanel()}
      ${continueCard(stories)}
      ${newsGenre && !isMid() ? newsSection(newsGenre, news) : ""}
      <h2 class="section-title"><span lang="zh-CN">故事</span> · Stories</h2>
      <div class="tiles bento">
        ${genres.filter((g) => g.id !== "news")
          .map((g) => ({ g, n: count(g) }))
          .sort((a, b) => (a.g.soon || !a.n) - (b.g.soon || !b.n) || b.n - a.n)   // most stories first, "soon" last
          .map(({ g, n }) => genreTile(g, n)).join("")}
      </div>`);
    $("level-toggle").addEventListener("click", (e) => {
      const b = e.target.closest("[data-level]");
      if (!b || b.dataset.level === settings.level) return;
      settings.level = b.dataset.level;
      saveSettings();
      applyLevel();
      Promise.resolve(route()).then(() => document.querySelector(`[data-level="${settings.level}"]`)?.focus({ preventScroll: true }));
    });
  }

  // ---------- one genre: list of stories ----------
  async function renderGenre(id) {
    if (id === "news" && isMid()) { location.replace("#/"); return; }   // news is 高级 only
    setTab("discover");
    const { genres, stories } = await getLibrary();
    const g = genres.find((x) => x.id === id);
    if (!g) return show(`${pageBar("#/", "All genres")}<p class="empty">Genre not found.</p>`);
    genrePage = { id, all: stories.filter((s) => s.genre === id) };
    show(`
      <div class="sticky-head">
      ${pageBar("#/", "All genres", (genrePage.all.length ? sortBtn : "") + aaBtn)}
      ${sortPanel(genreView(id))}
      ${readSettingsPanel()}
      </div>
      <header class="genre-head g-${esc(g.id)}">
        <span class="tile-mark" lang="zh-CN" aria-hidden="true">${esc(g.mark)}</span>
        <h1><span lang="zh-CN">${esc(g.zh)}</span> <span class="en">· ${esc(g.en)}</span></h1>
      </header>
      <p class="filter-note" id="filter-note" aria-live="polite" lang="zh-CN" hidden></p>
      <div class="cards ${listClass()}" id="genre-list"></div>`);
    refreshGenreList();
  }

  // Sorting and filters, remembered for each genre until the app is closed or reloaded.
  const SORT_GROUPS = [
    { key: "sort", label: "排序", options: [["default", "默认"], ["short", "字数少→多"], ["long", "字数多→少"]] },
    { key: "done", label: "进度", options: [["all", "全部"], ["no", "未读完"], ["yes", "已读完"]] },
    { key: "shelf", label: "收藏", options: [["all", "全部"], ["no", "未收藏"], ["yes", "已收藏"]] },
  ];
  const SORT_DEFAULT = { sort: "default", done: "all", shelf: "all" };
  const genreViews = {};
  let genrePage = null;
  const genreView = (id) => genreViews[id] || (genreViews[id] = { ...SORT_DEFAULT });
  const isFinished = (s) => (progress[s.id]?.done?.length || 0) >= s.chapter_count;
  const sortBtn = `<button class="aa-btn" id="sort-btn" aria-expanded="false" aria-controls="sort-panel" aria-label="排序和筛选 Sort and filter" title="Sort and filter"><svg viewBox="0 0 24 24" width="19" height="19" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M3 6h18M6.5 12h11M10 18h4"/></svg></button>`;
  const sortPanel = (view) => `
      <section class="read-settings" id="sort-panel" ${openPanel === "sort-panel" ? "" : "hidden"} lang="zh-CN">${optionRows(SORT_GROUPS, view, "sort")}</section>`;

  function refreshGenreList() {
    const box = $("genre-list");
    if (!box || !genrePage) return;
    const { id, all } = genrePage, v = genreView(id);
    const order = v.sort === "short" ? (a, b) => a.characters - b.characters
      : v.sort === "long" ? (a, b) => b.characters - a.characters
      : id === "news" ? byDate
      : (a, b) => Boolean(a.onlyHigh) - Boolean(b.onlyHigh)   // 中级: stories with an easy version first
        || a.chapter_count - b.chapter_count || a.characters - b.characters;
    const list = all
      .filter((s) => v.done === "all" || isFinished(s) === (v.done === "yes"))
      .filter((s) => v.shelf === "all" || Boolean(shelf[s.id]) === (v.shelf === "yes"))
      .sort(order);
    const filtered = v.done !== "all" || v.shelf !== "all";
    box.innerHTML = list.map((s) => storyCard(s)).join("")
      || `<p class="empty" lang="zh-CN">${all.length ? "没有符合条件的文章。" : "Stories coming soon."}</p>`;
    const note = $("filter-note");
    note.hidden = !filtered;
    note.innerHTML = filtered ? `${list.length} / ${all.length} 篇 <button class="link-btn" id="filter-clear">清除筛选</button>` : "";
    $("sort-btn")?.classList.toggle("active", filtered || v.sort !== "default");
    document.querySelectorAll("[data-sort]").forEach((b) => b.setAttribute("aria-pressed", String(v[b.dataset.sort] === b.dataset.val)));
  }
  function wireSortPanel() {
    const btn = $("sort-btn"), panel = $("sort-panel");
    if (!btn || !panel) return;
    btn.addEventListener("click", () => showPanel(panel.hidden ? "sort-panel" : ""));
    panel.addEventListener("click", (e) => {
      const b = e.target.closest("[data-sort]");
      if (!b) return;
      genreView(genrePage.id)[b.dataset.sort] = b.dataset.val;
      refreshGenreList();
    });
    $("filter-note").addEventListener("click", (e) => {
      if (!e.target.closest("#filter-clear")) return;
      Object.assign(genreView(genrePage.id), { done: "all", shelf: "all" });
      refreshGenreList();
    });
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
    if (saved[wordKey(word, pinyin)]) cls.push("saved");
    const inner = showsPinyin(t) ? `<ruby>${rubyFor(word, pinyin)}</ruby>` : esc(word);
    return `<span class="${cls.join(" ")}" data-i="${i}" role="button" tabindex="0">${inner}</span>`;
  }

  // Tokens on the current page, plus the sentence each one sits in
  // (saved with a word so flashcards can show it in context).
  let currentTokens = [];
  let tokenSentence = [];
  let plainAfter = [];     // punctuation after each word, to copy a highlighted phrase
  let currentStory = null;
  let currentFile = "", currentChapter = 0;   // which text the highlights belong to
  let readState = null;    // {id, n, total} while a story is open
  let pendingJump = null;  // a highlight to scroll to after the page opens

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
        const a = currentTokens.length - 1;            // the word just before it
        if (a >= 0) plainAfter[a] = (plainAfter[a] || "") + t;
        return `<span class="pu" data-a="${a}">${esc(t)}</span>`;
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
    setTab(progress[id] || shelf[id] ? "me" : "discover");
    let s, lib, easy;
    try {
      lib = await getLibrary();
      easy = isMid() && lib.stories.some((x) => x.id === id && x.mid);
      s = await getJSON(`data/stories/${encodeURIComponent(id)}${easy ? ".mid" : ""}.json`);
      s = { ...s, onlyHigh: isMid() && !easy };
    }
    catch { return show(`${pageBar("#/", "Discover")}<p class="empty">Story not found.</p>`); }
    const total = s.chapters.length;
    n = Math.min(Math.max(1, n || 1), total);
    const ch = s.chapters[n - 1];
    const genre = lib.genres.find((g) => g.id === s.genre);
    currentStory = s;

    // remember where the reader is
    const p = progress[id] || { chapter: 1, done: [], scrolled: false };
    p.chapter = n;
    p.last = Date.now();
    progress[id] = p;
    saveProgress();
    currentFile = id + (easy ? ".mid" : "");
    currentChapter = n;

    currentTokens = []; tokenSentence = []; plainAfter = [];
    // titles are tappable too: render them like a one-line paragraph
    const inline = (tokens) => renderParagraph(tokens || []).replace(/^<p>|<\/p>$/g, "");
    const titleHtml = s.title_tokens ? inline(s.title_tokens) : esc(s.title);
    const blurbHtml = s.blurb_tokens ? inline(s.blurb_tokens) : esc(s.blurb_zh);
    // news: date and a link to the original article
    const link = (s.sources || [])[0];
    const byline = s.date ? `<p class="byline">${esc(newsDate(s.date, true))}${link
      ? ` · <a href="${esc(link)}" rel="noopener" target="_blank" lang="zh-CN">${esc(newsSource(s) || "原文")} ↗</a>` : ""}</p>` : "";
    const chapterTitleHtml = ch.title_tokens ? inline(ch.title_tokens) : esc(ch.title);
    const paras = ch.paragraphs.map(renderParagraph).join("");
    const pinyinTitles = settings.pinyin !== "off" ? "pinyin" : "";

    const chapterOptions = s.chapters.map((c, i) =>
      `<option value="${i + 1}" ${i + 1 === n ? "selected" : ""}>${esc(c.title || chapterLabel(i + 1))}</option>`).join("");
    // previous / all chapters / next: the same row above and below the text
    const chapterRow = (where) => total > 1 ? `
      <nav class="chapter-top ${where}" aria-label="Chapters">
        ${n > 1 ? `<a class="ct-step" href="#/read/${encodeURIComponent(id)}/${n - 1}" aria-label="Previous chapter">‹</a>` : `<span class="ct-step off" aria-hidden="true">‹</span>`}
        <select class="chapter-select" aria-label="Go to chapter">${chapterOptions}</select>
        ${n < total ? `<a class="ct-step" data-next href="#/read/${encodeURIComponent(id)}/${n + 1}" aria-label="Next chapter">›</a>` : `<span class="ct-step off" aria-hidden="true">›</span>`}
      </nav>` : "";
    const listHref = genre ? `#/genre/${esc(genre.id)}` : "#/";

    const finished = n === total;
    const sources = (s.sources || []).length ? `
      <section class="sources">
        <strong>Sources</strong> (retold for learners):
        ${s.sources.map((u) => `<a href="${esc(u)}" rel="noopener" target="_blank">${esc(decodeURIComponent(u))}</a>`).join(" · ")}
      </section>` : `<section class="sources">An original story written for 字游 Ziyou.</section>`;

    show(`
      <div class="sticky-head">
      ${pageBar(listHref, genre ? genre.en : "Discover", saveStoryBtn(id) + bmBtn(id) + aaBtn, "列表")}
      ${readSettingsPanel()}
      <section class="read-settings bm-panel" id="bm-panel" hidden lang="zh-CN"></section>
      </div>
      <header class="story-head">
        <h1 class="${pinyinTitles}" lang="zh-CN">${titleHtml}</h1>
        ${byline}
        ${s.onlyHigh ? `<p class="byline">${onlyHighBadge(s)}</p>` : ""}
        ${blurbHtml && s.genre !== "news" ? `<p class="story-blurb ${pinyinTitles}" lang="zh-CN">${blurbHtml}</p>` : ""}
      </header>
      ${chapterRow("above")}
      ${ch.title ? `<h2 class="chapter-title ${pinyinTitles}" lang="zh-CN">${chapterTitleHtml}</h2>` : ""}
      <article class="story size-${esc(settings.size)} ${settings.pinyin !== "off" ? "pinyin" : ""}" lang="zh-CN">${paras}</article>
      ${finished ? `<p class="the-end" id="the-end" lang="zh-CN">全文完</p>` : ""}
      ${chapterRow("below")}
      ${sources}`);

    // A chapter counts as read when the reader moves on to the next one, or scrolls
    // down to 全文完 at the end. Only opening a story is not finishing it.
    app.querySelectorAll("[data-next]").forEach((a) => a.addEventListener("click", () => markDone(id, n)));
    app.querySelectorAll(".chapter-select").forEach((sel) =>
      sel.addEventListener("change", () => { location.hash = `#/read/${encodeURIComponent(id)}/${sel.value}`; }));
    readState = { id, n, total };
    userMoved = false;
    applyHighlights();
    markBookmarks();
    $("bm-panel").addEventListener("click", onBookmarkPanel);

    // Open where the reader stopped (or at a highlight picked in 我的).
    const jump = pendingJump && pendingJump.file === currentFile && pendingJump.chapter === n ? pendingJump : null;
    pendingJump = null;
    const target = jump ? (jump.para != null ? app.querySelectorAll("article.story > p")[jump.para]
                                             : app.querySelector(`.w[data-i="${jump.from}"]`))
      : p.pos && p.pos.chapter === n && p.pos.para > 0 ? app.querySelectorAll("article.story > p")[p.pos.para] : null;
    if (target) {
      const top = target.getBoundingClientRect().top + window.scrollY;
      window.scrollTo(0, Math.max(0, top - (jump ? window.innerHeight / 3 : headerHeight() + 8)));
    }
    noteScroll(true);   // count what is on screen now, even if the reader never scrolls
  }

  // ---------- bookmarks: places inside a story ----------
  const storyBookmarks = (id) => (bookmarks[id] || []).filter((b) => b.file === currentFile);
  const bmBtn = (id) => {
    const on = storyBookmarks(id).length > 0;
    return `<button class="aa-btn bm-btn${on ? " on" : ""}" id="bm-btn" aria-expanded="false" aria-controls="bm-panel" aria-label="书签 Bookmarks" title="Bookmarks">${bookmarkIcon(on)}</button>`;
  };
  // The paragraph at the top of the screen: where a new bookmark goes.
  function paraOnScreen() {
    const paras = [...app.querySelectorAll("article.story > p")];
    const k = paras.findIndex((el) => el.getBoundingClientRect().bottom > headerHeight() + 8);
    return k < 0 ? Math.max(0, paras.length - 1) : k;
  }
  function paraText(k) {
    const el = app.querySelectorAll("article.story > p")[k];
    if (!el) return "";
    const c = el.cloneNode(true);
    c.querySelectorAll("rt").forEach((r) => r.remove());
    const t = c.textContent.trim();
    return t.length > 80 ? t.slice(0, 80) : t;   // the list cuts it to one line with "…"
  }
  function bmPanelHtml() {
    const { id, total } = readState;
    const list = storyBookmarks(id).sort((a, b) => a.chapter - b.chapter || a.para - b.para);
    const here = paraOnScreen();
    const exists = list.some((b) => b.chapter === readState.n && b.para === here);
    return `
      <div class="bm-head"><span class="rs-label">书签</span>
        <button class="btn bm-add" data-bm-add ${exists ? "disabled" : ""}>${exists ? "这里已有书签" : "＋ 在这里加书签"}</button></div>
      ${list.length ? `<ul class="bm-list">${list.map((b) => `
        <li><button class="bm-go" data-bm-go="${esc(b.id)}">
            <span class="bm-where">${total > 1 ? `${chapterLabel(b.chapter)} · ` : ""}第${b.para + 1}段</span>
            <span class="bm-text">${esc(b.text)}</span></button>
          <button class="bm-rm" data-bm-rm="${esc(b.id)}" aria-label="删除书签 Remove bookmark">×</button></li>`).join("")}</ul>`
        : `<p class="bm-empty">还没有书签。读到想回来的地方，点“在这里加书签”。</p>`}`;
  }
  function markBookmarks() {
    const paras = app.querySelectorAll("article.story > p");
    paras.forEach((el) => el.classList.remove("bm"));
    for (const b of storyBookmarks(readState.id)) if (b.chapter === readState.n) paras[b.para]?.classList.add("bm");
    const btn = $("bm-btn");
    if (btn) btn.outerHTML = bmBtn(readState.id).replace('aria-expanded="false"', `aria-expanded="${openPanel === "bm-panel"}"`);
    $("bm-btn")?.addEventListener("click", () => {
      if (openPanel === "bm-panel") return showPanel("");
      $("bm-panel").innerHTML = bmPanelHtml();
      showPanel("bm-panel");
    });
  }
  function onBookmarkPanel(e) {
    const { id, n } = readState;
    const add = e.target.closest("[data-bm-add]"), go = e.target.closest("[data-bm-go]"), rm = e.target.closest("[data-bm-rm]");
    if (add) {
      const para = paraOnScreen();
      (bookmarks[id] = bookmarks[id] || []).push({
        id: Date.now().toString(36), file: currentFile, chapter: n, para, text: paraText(para), added: Date.now() });
      saveBookmarks();
    } else if (rm) {
      bookmarks[id] = (bookmarks[id] || []).filter((b) => b.id !== rm.dataset.bmRm);
      if (!bookmarks[id].length) delete bookmarks[id];
      saveBookmarks();
    } else if (go) {
      const b = (bookmarks[id] || []).find((x) => x.id === go.dataset.bmGo);
      if (!b) return;
      showPanel("");
      if (b.chapter === n) {
        const el = app.querySelectorAll("article.story > p")[b.para];
        if (el) window.scrollTo(0, Math.max(0, el.getBoundingClientRect().top + window.scrollY - headerHeight() - 8));
      } else {
        pendingJump = b;
        location.hash = `#/read/${encodeURIComponent(id)}/${b.chapter}`;
      }
      return;
    } else return;
    $("bm-panel").innerHTML = bmPanelHtml();
    markBookmarks();
  }

  // Height of what stays at the top of the screen: the app bar (unless hidden) plus the story's button row.
  const headerHeight = () => (barsHidden() ? 0 : document.querySelector(".topbar")?.offsetHeight || 0)
    + (app.querySelector(".sticky-head .read-bar")?.offsetHeight || 0) + 12;
  // The button row sticks just under the app bar, whatever its height on this phone.
  const syncTopbar = () => document.documentElement.style.setProperty(
    "--topbar-h", `${document.querySelector(".topbar")?.offsetHeight || 0}px`);
  syncTopbar();
  window.addEventListener("resize", syncTopbar);

  // The 字游 bar and the tab bar slide away while the page moves down (reading on),
  // and come back as soon as it moves up, or near the top of the page.
  const barsHidden = () => document.documentElement.classList.contains("bars-hidden");
  const showBars = (on) => document.documentElement.classList.toggle("bars-hidden", !on);
  let barsY = window.scrollY;
  window.addEventListener("scroll", () => {
    const y = window.scrollY, dy = y - barsY;
    if (Math.abs(dy) < 8) return;
    showBars(dy < 0 || y < 80);
    barsY = y;
  }, { passive: true });

  function markDone(id, num) {
    const pr = progress[id];
    if (pr && !pr.done.includes(num)) { pr.done.push(num); saveProgress(); }
  }

  // While reading, remember the first paragraph on screen, and notice when 全文完 comes into view.
  let userMoved = false;   // the reader moved the page themselves since this chapter opened
  const moved = () => { if (readState) userMoved = true; };
  window.addEventListener("touchmove", moved, { passive: true });
  window.addEventListener("wheel", moved, { passive: true });
  window.addEventListener("keydown", (e) => { if (/^(Arrow(Up|Down)|PageUp|PageDown|Home|End| )$/.test(e.key)) moved(); });
  let scrollTimer = 0;
  window.addEventListener("scroll", () => {
    if (!readState || scrollTimer) return;
    scrollTimer = setTimeout(() => { scrollTimer = 0; noteScroll(); }, 300);
  }, { passive: true });
  // opened: called when the page opens. Then 全文完 on screen does not yet count as finishing.
  function noteScroll(opened = false) {
    if (!readState || !location.hash.startsWith("#/read/")) return;
    const paras = [...app.querySelectorAll("article.story > p")];
    const p = progress[readState.id];
    if (!paras.length || !p) return;
    let k = paras.findIndex((el) => el.getBoundingClientRect().bottom > headerHeight() + 8);
    if (k < 0) k = paras.length - 1;
    p.pos = { chapter: readState.n, para: k };
    if (!opened && userMoved) p.scrolled = true;
    {
      // Share of the chapter's characters that have been on screen: everything above the bottom of
      // the screen counts, a paragraph cut by the bottom edge counts in part. For the whole story,
      // earlier chapters count as read: chapter 2 of 3, half seen = 50%. Never goes down.
      const tabsTop = document.querySelector(".tabs")?.getBoundingClientRect().top ?? window.innerHeight;
      const bottom = tabsTop > window.innerHeight / 2 ? tabsTop : window.innerHeight;   // phones: above the tab bar
      const hanzi = (el) => (el.textContent.match(/[一-鿿]/g) || []).length;   // pinyin is not counted
      let seen = 0, all = 0;
      for (const el of paras) {
        const r = el.getBoundingClientRect(), n = hanzi(el);
        all += n;
        seen += n * Math.min(1, Math.max(0, (bottom - r.top) / (r.height || 1)));
      }
      const chapterShare = all ? Math.min(1, seen / all) : 0;
      const pct = Math.max(1, Math.min(100, Math.round(((readState.n - 1 + chapterShare) / readState.total) * 100)));
      p.pct = Math.max(p.pct || 0, pct);
    }
    p.last = Date.now();
    const end = $("the-end");
    if (!opened && end && end.getBoundingClientRect().top < window.innerHeight) markDone(readState.id, readState.n);
    saveProgress();
  }

  // ---------- highlights ----------
  // A highlight is a run of words (from..to, positions on the chapter page) in one of three colours.
  const HL_COLORS = [["1", "黄 Yellow"], ["2", "绿 Green"], ["3", "粉 Pink"]];
  const chapterHighlights = () => highlights.filter((h) => h.file === currentFile && h.chapter === currentChapter);
  const highlightAt = (i) => chapterHighlights().find((h) => h.from <= i && h.to >= i);
  function applyHighlights() {
    app.querySelectorAll(".hl1, .hl2, .hl3").forEach((el) => el.classList.remove("hl1", "hl2", "hl3"));
    for (const h of chapterHighlights()) {
      for (let i = h.from; i <= h.to; i++) app.querySelector(`.w[data-i="${i}"]`)?.classList.add(`hl${h.color}`);
      app.querySelectorAll(".pu").forEach((el) => {
        const a = Number(el.dataset.a);
        if (a >= h.from && a < h.to) el.classList.add(`hl${h.color}`);
      });
    }
  }
  function addHighlight(from, to, color) {
    // one colour per word: a new highlight replaces the ones it overlaps
    highlights = highlights.filter((h) => !(h.file === currentFile && h.chapter === currentChapter && h.from <= to && h.to >= from));
    let text = "";
    for (let i = from; i <= to; i++) text += currentTokens[i][0] + (i < to ? plainAfter[i] || "" : "");
    highlights.push({
      id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
      file: currentFile, story: readState.id, title: currentStory.title, chapter: currentChapter,
      from, to, color, text, added: Date.now(),
    });
    saveHighlights(); applyHighlights();
  }
  function removeHighlightAt(i) {
    highlights = highlights.filter((h) => !(h.file === currentFile && h.chapter === currentChapter && h.from <= i && h.to >= i));
    saveHighlights(); applyHighlights();
  }

  // Select text in a story (long-press on a phone) and a colour bar appears.
  let selTimer = 0, lastSel = null;
  function selectedWords() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
    const r = sel.getRangeAt(0);
    const story = app.querySelector("article.story");
    if (!story || !story.contains(r.commonAncestorContainer)) return null;
    const idx = [...story.querySelectorAll(".w")].filter((el) => r.intersectsNode(el)).map((el) => Number(el.dataset.i));
    return idx.length ? [Math.min(...idx), Math.max(...idx)] : null;
  }
  document.addEventListener("selectionchange", () => {
    clearTimeout(selTimer);
    selTimer = setTimeout(() => {
      const t = selectedWords();
      if (t) { lastSel = t; closePopup(); $("hl-bar").hidden = false; }
      else $("hl-bar").hidden = true;
    }, 250);
  });
  $("hl-bar").addEventListener("pointerdown", (e) => e.preventDefault());   // keep the selection
  $("hl-bar").addEventListener("click", (e) => {
    const b = e.target.closest("[data-hl]");
    if (!b) return;
    const t = selectedWords() || lastSel;
    if (t && b.dataset.hl !== "x") addHighlight(t[0], t[1], b.dataset.hl);
    window.getSelection()?.removeAllRanges();
    lastSel = null;
    $("hl-bar").hidden = true;
  });

  // ---------- reading settings (Discover, story lists and story pages; in Chinese) ----------
  const SETTING_GROUPS = [
    { key: "pinyin", label: "拼音", options: [["off", "关闭"], ["all", "全部"], ["hard", "仅生词"]] },
    { key: "size", label: "字号", options: [["s", "小"], ["m", "中"], ["l", "大"]] },
    { key: "theme", label: "主题", options: [["light", "浅色"], ["dark", "深色"], ["auto", "跟随系统"]] },
  ];
  const PANELS = { "read-settings": "aa-btn", "sort-panel": "sort-btn", "bm-panel": "bm-btn" };  // panel -> its button
  let openPanel = "";   // at most one is open
  const optionRows = (groups, values, attr) => groups.map((g) => `
          <div class="rs-row">
            <span class="rs-label">${g.label}</span>
            <div class="seg" role="group" aria-label="${g.label}">
              ${g.options.map(([v, text]) => `<button data-${attr}="${g.key}" data-val="${v}" aria-pressed="${values[g.key] === v}">${text}</button>`).join("")}
            </div>
          </div>`).join("");
  function readSettingsPanel() {
    return `
      <section class="read-settings" id="read-settings" ${openPanel === "read-settings" ? "" : "hidden"} lang="zh-CN">${optionRows(SETTING_GROUPS, settings, "set")}</section>`;
  }
  function showPanel(name) {
    openPanel = name;
    syncPanels();
  }
  function syncPanels() {
    document.body.classList.toggle("settings-open", Boolean(openPanel && $(openPanel)));
    for (const [panel, btn] of Object.entries(PANELS)) {
      if ($(panel)) $(panel).hidden = panel !== openPanel;
      if ($(btn)) $(btn).setAttribute("aria-expanded", String(panel === openPanel));
    }
  }
  // A click anywhere outside closes the open panel. On the page itself it does only that, so
  // closing never opens a story or a word by accident; the bars around the page still work.
  document.addEventListener("click", (e) => {
    if (!openPanel || !$(openPanel)) return;
    if (e.target.closest("#read-settings, #aa-btn, #sort-panel, #sort-btn, #filter-note, #bm-panel, #bm-btn")) return;
    showPanel("");
    if (app.contains(e.target)) { e.preventDefault(); e.stopPropagation(); }
  }, true);

  function wireReadSettings() {
    const btn = $("aa-btn"), panel = $("read-settings");
    if (!btn || !panel) return;
    btn.addEventListener("click", () => showPanel(panel.hidden ? "read-settings" : ""));
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
    const { own, others } = meaningsOf(popupToken);
    $("popup-meaning").textContent = own || "No dictionary entry.";
    $("popup-more").innerHTML = others.map(([p, ms]) =>
      `<p><span class="more-py">${esc(p)}</span> ${esc(ms.join("; "))}</p>`).join("");
    $("popup-hl").innerHTML = popupHlRow();
    updateSaveButton();
    $("popup").hidden = false;
    setPopupExpanded(false);
    selectedEl = el;
    markSelection();
  }
  // The tapped word is outlined; if it is highlighted, the whole highlighted phrase is selected instead.
  function markSelection() {
    app.querySelectorAll(".sel, .sel-hl").forEach((x) => x.classList.remove("sel", "sel-hl"));
    if (popupIndex < 0) return;
    const h = highlightAt(popupIndex);
    if (!h) { selectedEl?.classList.add("sel"); return; }
    for (let i = h.from; i <= h.to; i++) app.querySelector(`.w[data-i="${i}"]`)?.classList.add("sel-hl");
    app.querySelectorAll(".pu").forEach((x) => {
      const a = Number(x.dataset.a);
      if (a >= h.from && a < h.to) x.classList.add("sel-hl");
    });
  }
  // Every meaning of the word for this pinyin, in one line; other pinyin (还 huán) below it.
  const bare = (p) => (p || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/\s+/g, "");
  function meaningsOf([word, pinyin, , meaning]) {
    const rows = (currentStory && currentStory.senses && currentStory.senses[word]) || [];
    const parts = (ms) => ms.flatMap((m) => m.split(";")).map((m) => m.trim()).filter(Boolean);
    const first = parts([meaning || ""]);
    const here = (pinyin || "").toLowerCase();
    // this pinyin: exact match, or only the tones differ (dōng xi / dōng xī)
    const mine = rows.find(([p]) => p.toLowerCase() === here) || rows.find(([p]) => bare(p) === bare(here));
    const own = [...new Set([...first, ...(mine ? parts(mine[1]) : [])])].join("; ");
    return { own, others: rows.filter((r) => r !== mine) };
  }
  function popupHlRow() {
    const h = highlightAt(popupIndex);
    return HL_COLORS.map(([c, name]) =>
      `<button class="hl-dot c${c}${h && h.color === c ? " on" : ""}" data-phl="${c}" aria-label="${name}" aria-pressed="${Boolean(h && h.color === c)}"></button>`).join("")
      + (h ? `<button class="hl-dot hl-none" data-phl="x" aria-label="删除高亮 Remove highlight" title="删除高亮"></button>` : "");
  }

  // The word box starts short (3 lines of meaning) so it covers less of the story.
  // Pull it up to see everything; pull it down to make it short again, or to close it.
  function setPopupExpanded(on) {
    const pop = $("popup");
    pop.classList.toggle("expanded", on);
    pop.scrollTop = 0;
    const m = $("popup-meaning");
    const more = !on && (m.scrollHeight > m.clientHeight + 2 || $("popup-more").children.length > 0);
    pop.classList.toggle("can-expand", on || more);
  }
  // The pull works anywhere on the box. When the open box is scrolled down, pulling down
  // scrolls it back up first; only a pull from the top shrinks it.
  let pullY = null, pullTop = 0;
  const pullStart = (y) => { pullY = y; pullTop = $("popup").scrollTop; };
  const pullEnd = (y) => {
    if (pullY == null) return false;
    const dy = y - pullY, pop = $("popup");
    pullY = null;
    const open = pop.classList.contains("expanded");
    if (dy < -30 && !open && pop.classList.contains("can-expand")) setPopupExpanded(true);
    else if (dy > 40 && pullTop <= 0) { if (open) setPopupExpanded(false); else closePopup(); }
    else return false;
    return true;
  };
  $("popup").addEventListener("touchstart", (e) => pullStart(e.touches[0].clientY), { passive: true });
  $("popup").addEventListener("touchend", (e) => { if (pullEnd(e.changedTouches[0].clientY)) e.preventDefault(); });
  // with a mouse: press and drag on the box
  $("popup").addEventListener("mousedown", (e) => { if (!e.target.closest("button")) pullStart(e.clientY); });
  window.addEventListener("mouseup", (e) => pullEnd(e.clientY));

  function closePopup() {
    $("popup").hidden = true;
    app.querySelectorAll(".sel, .sel-hl").forEach((x) => x.classList.remove("sel", "sel-hl"));
    selectedEl = null; popupToken = null; popupIndex = -1;
  }
  function updateSaveButton() {
    const isSaved = popupToken && saved[wordKey(popupToken[0], popupToken[1])];
    const btn = $("popup-save");
    btn.textContent = isSaved ? "✓ 已加入" : "＋ 生词本";
    btn.title = isSaved ? "从生词本移除 Remove from My Words" : "加入生词本 Add to My Words";
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
  // Tap a colour in the popup: highlight this word (or recolour the highlight it is in).
  $("popup-hl").addEventListener("click", (e) => {
    const b = e.target.closest("[data-phl]");
    if (!b || popupIndex < 0) return;
    e.stopPropagation();   // the row is redrawn below; the tap must not count as a tap outside the popup
    const h = highlightAt(popupIndex);
    if (b.dataset.phl === "x") removeHighlightAt(popupIndex);
    else if (h) { h.color = b.dataset.phl; saveHighlights(); applyHighlights(); }
    else addHighlight(popupIndex, popupIndex, b.dataset.phl);
    $("popup-hl").innerHTML = popupHlRow();
    markSelection();
  });
  // Tap anywhere outside the popup (and not on another word) to close it.
  document.addEventListener("click", (e) => {
    if ($("popup").hidden) return;
    if (e.target.closest("#popup") || e.target.closest(".w")) return;
    closePopup();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closePopup(); if (openPanel) showPanel(""); } });
  $("popup-save").addEventListener("click", () => {
    if (!popupToken) return;
    const [word, pinyin, level, meaning] = popupToken;
    const key = wordKey(word, pinyin);
    if (saved[key]) delete saved[key];
    else saved[key] = {
      word, pinyin, level, meaning, added: Date.now(), box: 0,
      sentence: tokenSentence[popupIndex] || "",
      story: currentStory ? currentStory.title : "",
    };
    saveWords();
    updateSaveButton();
    document.querySelectorAll(".w").forEach((el) => {
      const t = currentTokens[Number(el.dataset.i)];
      if (t && wordKey(t[0], t[1]) === key) el.classList.toggle("saved", Boolean(saved[key]));
    });
  });

  // ---------- 我的 Me: recently read, saved stories, highlights ----------
  const ME_TABS = [["recent", "最近阅读", "Recent"], ["saved", "收藏", "Saved"], ["highlights", "高亮", "Highlights"]];
  async function renderMe(sub) {
    setTab("me");
    const { genres, stories } = await getLibrary();
    const subnav = `<nav class="subtabs" aria-label="我的">${ME_TABS.map(([k, zh, en]) =>
      `<a href="#/me${k === "recent" ? "" : `/${k}`}" class="${k === sub ? "on" : ""}" ${k === sub ? 'aria-current="page"' : ""} title="${en}"><span lang="zh-CN">${zh}</span></a>`).join("")}</nav>`;
    const cards = (list, empty) => `<div class="cards ${listClass()}">${list.map((s) => storyCard(s, { showGenre: true, genres })).join("") || empty}</div>`;
    let body;
    if (sub === "saved") {
      const mine = stories.filter((s) => shelf[s.id]).sort((a, b) => shelf[b.id].added - shelf[a.id].added);
      body = cards(mine, `<p class="empty">Nothing here yet. Open a story and tap <span class="inline-icon" role="img" aria-label="the heart">${heartIcon(false)}</span> to keep it here.<br><br>
           <a class="btn" href="#/">Discover stories</a></p>`);
    } else if (sub === "highlights") {
      const byId = Object.fromEntries(stories.map((s) => [s.id, s]));
      const list = [...highlights].sort((a, b) => b.added - a.added);
      body = list.length ? `<ul class="hl-list">${list.map((h) => {
        const s = byId[h.story];
        const where = `${esc(h.title)}${s && s.chapter_count > 1 ? ` · ${chapterLabel(h.chapter)}` : ""}${h.file.endsWith(".mid") ? " · 中级" : ""}`;
        return `
          <li class="hl-item c${esc(h.color)}">
            <a href="#/read/${encodeURIComponent(h.story)}/${h.chapter}" data-jump="${esc(h.id)}">
              <span class="hl-text" lang="zh-CN">${esc(h.text)}</span>
              <span class="meta" lang="zh-CN">${where}</span>
            </a>
            <button class="minus-btn" data-hl-rm="${esc(h.id)}" aria-label="删除高亮 Remove highlight" title="删除">−</button>
          </li>`;
      }).join("")}</ul>`
        : `<p class="empty">No highlights yet. In a story, long-press and select a phrase, then pick a colour. Or tap a word and pick a colour in its box.</p>`;
    } else {
      const read = stories.filter((s) => progress[s.id]).sort((a, b) => (progress[b.id].last || 0) - (progress[a.id].last || 0));
      body = cards(read, `<p class="empty">Stories you open will show up here.<br><br><a class="btn" href="#/">Discover stories</a></p>`);
    }
    show(`
      <div class="page-head">
        <h1 class="page-title" lang="zh-CN">我的</h1>
        ${aaBtn}
      </div>
      ${readSettingsPanel()}
      ${subnav}
      ${body}
      <p class="meta me-note">Saved in this browser only.</p>`);
    app.querySelectorAll("[data-jump]").forEach((a) => a.addEventListener("click", () => {
      pendingJump = highlights.find((h) => h.id === a.dataset.jump) || null;
    }));
    app.querySelectorAll("[data-hl-rm]").forEach((b) => b.addEventListener("click", () => {
      highlights = highlights.filter((h) => h.id !== b.dataset.hlRm);
      saveHighlights(); renderMe(sub);
    }));
  }

  // ---------- My Words ----------
  const wordEntries = () => Object.entries(saved).sort((a, b) => b[1].added - a[1].added)
    .map(([k, e]) => [wordOf(k, e), e, k]);

  function renderWords() {
    setTab("words");
    const entries = wordEntries();
    const n = entries.length;
    show(`
      <div class="page-head">
        <h1 class="page-title" lang="zh-CN">生词本</h1>
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
      <ul class="word-list">${entries.map(([w, e, k]) => `
        <li>
          <span class="zh" lang="zh-CN">${esc(w)}</span>
          <span class="py">${esc(e.pinyin)}${e.box >= 3 ? ` <span class="known">✓ known</span>` : ""}</span>
          <button class="minus-btn rm" data-key="${esc(k)}" aria-label="移除 ${esc(w)} Remove" title="移除">−</button>
          <span class="en">${esc(e.meaning)}</span>
          ${e.sentence ? `<span class="ex" lang="zh-CN">${esc(e.sentence)}</span>` : ""}
        </li>`).join("")}</ul>`
      : `<p class="empty">No saved words yet. While reading, tap a word, then <strong>+ Save to My Words</strong>.</p>`}`);

    app.querySelectorAll(".rm").forEach((b) => b.addEventListener("click", () => {
      delete saved[b.dataset.key]; saveWords(); renderWords();
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
          ${pageBar("#/words", "My Words")}
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
      const [w, e, key] = deck[i];
      flipped = false;
      show(`
        ${pageBar("#/words", "My Words")}
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
        const entry = saved[key];
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
    const qs = shuffle(all).slice(0, 10).map(([w, e, key], k) => {
      const others = shuffle(all.filter(([, , x]) => x !== key)).slice(0, 3);
      const options = shuffle([[w, e, key], ...others]);
      return { w, e, key, options, reverse: k % 2 === 1 };
    });
    let i = 0, score = 0;
    const missed = [];

    const draw = () => {
      if (i >= qs.length) {
        show(`
          ${pageBar("#/words", "My Words")}
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
        ${pageBar("#/words", "My Words")}
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
        const pick = q.options[Number(b.dataset.k)][2];
        const right = pick === q.key;
        buttons.forEach((x) => {
          x.disabled = true;
          if (q.options[Number(x.dataset.k)][2] === q.key) x.classList.add("right");
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
    document.documentElement.classList.remove("bars-hidden");   // a new page starts with the bars showing
    readState = null;
    if ($("hl-bar")) $("hl-bar").hidden = true;
    const hash = location.hash || "#/";
    let m;
    if ((m = hash.match(/^#\/read\/([^/]+)(?:\/(\d+))?$/))) return renderRead(decodeURIComponent(m[1]), Number(m[2] || 1));
    if ((m = hash.match(/^#\/story\/([^/]+)$/))) { location.replace(`#/read/${m[1]}/1`); return; } // old links
    if ((m = hash.match(/^#\/genre\/([^/]+)$/))) return renderGenre(decodeURIComponent(m[1]));
    if ((m = hash.match(/^#\/me(?:\/(saved|highlights))?$/))) return renderMe(m[1] || "recent");
    if (hash === "#/library") { location.replace("#/me/saved"); return; }   // old name
    if (hash === "#/words/cards") return renderCards();
    if (hash === "#/words/quiz") return renderQuiz();
    if (hash === "#/words") return renderWords();
    return renderDiscover();
  }
  window.addEventListener("hashchange", () => {
    openPanel = "";
    Promise.resolve(route()).catch((err) => show(`<p class="empty">Could not load (${esc(err.message)}).</p>`));
  });
  updateCount();
  Promise.resolve(route()).catch((err) => show(`<p class="empty">Could not load the library (${esc(err.message)}).</p>`));

  // ---------- installable app (PWA) ----------
  if ("serviceWorker" in navigator && location.protocol !== "file:") {
    window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").then((reg) => {
      // A home-screen app can stay open for days: look for a new version whenever it comes back.
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") reg.update().catch(() => {});
      });
    }).catch(() => {}));
    // New version ready: reload now, or after the story if someone is reading.
    let hadOne = Boolean(navigator.serviceWorker.controller), reloading = false;
    const reload = () => { if (!reloading) { reloading = true; location.reload(); } };
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (!hadOne) { hadOne = true; return; }   // first visit, nothing to replace
      if (/^#\/(read|words\/)/.test(location.hash)) window.addEventListener("hashchange", reload, { once: true });
      else reload();
    });
  }
})();
