/* global ePub */

const ui = {
  file: document.querySelector("#epub-file"),
  viewer: document.querySelector("#viewer"),
  empty: document.querySelector("#empty-reader"),
  loading: document.querySelector("#loading"),
  loadingText: document.querySelector("#loading-text"),
  title: document.querySelector("#reader-title"),
  summary: document.querySelector("#book-summary"),
  summaryTitle: document.querySelector("#book-title"),
  author: document.querySelector("#book-author"),
  toc: document.querySelector("#toc-list"),
  chapterCount: document.querySelector("#chapter-count"),
  previous: document.querySelector("#previous-page"),
  next: document.querySelector("#next-page"),
  range: document.querySelector("#progress-range"),
  progress: document.querySelector("#progress-label"),
  chapter: document.querySelector("#chapter-label"),
  decreaseFont: document.querySelector("#decrease-font"),
  increaseFont: document.querySelector("#increase-font"),
  resetFont: document.querySelector("#reset-font"),
  theme: document.querySelector("#theme-select"),
  settingsToggle: document.querySelector("#toggle-settings"),
  settings: document.querySelector("#reader-settings"),
  fontSelect: document.querySelector("#font-select"),
  fontSizeRange: document.querySelector("#font-size-range"),
  fontSizeRangeValue: document.querySelector("#font-size-range-value"),
  lineHeightRange: document.querySelector("#line-height-range"),
  lineHeightValue: document.querySelector("#line-height-value"),
  bookFrame: document.querySelector("#book-frame"),
  comicViewer: document.querySelector("#comic-viewer"),
  comicPage: document.querySelector("#comic-page"),
  comicControls: document.querySelector("#comic-controls"),
  comicFit: document.querySelector("#comic-fit"),
  comicZoomIn: document.querySelector("#comic-zoom-in"),
  comicZoomOut: document.querySelector("#comic-zoom-out"),
  comicZoomValue: document.querySelector("#comic-zoom-value"),
  stagePrevious: document.querySelector("#stage-previous"),
  stageNext: document.querySelector("#stage-next"),
  flowButtons: [...document.querySelectorAll("[data-flow]")],
  showLibrary: document.querySelector("#show-library"),
  libraryView: document.querySelector("#library-view"),
  libraryGrid: document.querySelector("#library-grid"),
  notesToggle: document.querySelector("#toggle-notes"),
  notesView: document.querySelector("#notes-view"),
  notesList: document.querySelector("#notes-list"),
  notesClose: document.querySelector("#notes-close"),
  highlightPopup: document.querySelector("#highlight-popup"),
  popupMemo: document.querySelector("#popup-memo"),
  popupRemove: document.querySelector("#popup-remove"),
  readingStage: document.querySelector("#reading-stage"),
  translateToggle: document.querySelector("#toggle-translation"),
  translationView: document.querySelector("#translation-view"),
  translationStatus: document.querySelector("#translation-status"),
  sourcePane: document.querySelector("#source-pane"),
  translationPane: document.querySelector("#translation-pane"),
  tabSource: document.querySelector("#tab-source"),
  tabTranslation: document.querySelector("#tab-translation"),
};

const state = {
  book: null,
  rendition: null,
  locationsReady: false,
  storageKey: null,
  libraryId: null,
  fontSize: Number(localStorage.getItem("librum-font-size")) || 100,
  font: localStorage.getItem("librum-font") || "serif",
  lineHeight: Number(localStorage.getItem("librum-line-height")) || 1.8,
  theme: localStorage.getItem("librum-theme") || "paper",
  flow: localStorage.getItem("librum-flow") || "paginated",
};

const fontFamilies = {
  serif: "Georgia, 'Times New Roman', serif",
  sans: "Arial, 'Malgun Gothic', sans-serif",
  system: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
};

const OPEN_TIMEOUT_MS = 15000;
const TRANSLATION_POLL_MS = 4000;
const BLOCK_SELECTOR = "p, h1, h2, h3, h4, h5, h6, blockquote, li, pre, figcaption";

const translation = {
  active: false,
  jobId: null,
  chapterHref: null,
  paragraphs: [],
  pollTimer: null,
};

// 만화(CBZ) 뷰어 상태. 이미지가 순서대로 담긴 ZIP을 한 장씩 페이지로 넘긴다.
const comic = {
  active: false,
  entries: [], // JSZip 파일 엔트리(파일명 자연 정렬)
  urls: [], // 페이지별 blob URL(지연 생성 캐시)
  index: 0,
  storageKey: null,
  fit: localStorage.getItem("librum-comic-fit") === "width" ? "width" : "page", // 화면맞춤/폭맞춤
  zoom: 1, // 확대 배율(1~4)
  panX: 0,
  panY: 0, // 확대·폭맞춤 시 이동 오프셋(화면 px)
};
const COMIC_MAX_ZOOM = 4;

const IMAGE_RE = /\.(jpe?g|png|gif|webp|avif|bmp)$/i;

function describeFile(file) {
  return {
    name: file.name,
    sizeBytes: file.size,
    mimeType: file.type || "unknown",
    modifiedAt: file.lastModified ? new Date(file.lastModified).toISOString() : null,
  };
}

function describeError(error) {
  if (error instanceof Error) {
    return {
      name: error.name,
      message: error.message,
      stack: error.stack?.split("\n").slice(0, 8).join("\n") || null,
    };
  }
  return { name: "UnknownError", message: String(error) };
}

function reportDiagnostic(event, details = {}, level = "info") {
  const entry = {
    schemaVersion: 1,
    recordedAt: new Date().toISOString(),
    event,
    level,
    page: window.location.pathname,
    ...details,
  };

  fetch("/api/logs", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(entry),
    keepalive: true,
  }).catch(() => {});
}

async function openArchive(input, file, inputType) {
  // Create the Book before opening so binary input never takes the URL request path.
  // Blob URLs preserve local image and stylesheet references inside the reader iframe.
  const book = ePub({ replacements: "blobUrl" });
  let timeoutId;

  reportDiagnostic("epub_engine_created", { file: describeFile(file), inputType });
  try {
    await Promise.race([
      book.open(input, "binary"),
      new Promise((_, reject) => {
        timeoutId = window.setTimeout(() => {
          const timeoutError = new Error(`EPUB archive opening exceeded ${OPEN_TIMEOUT_MS}ms`);
          timeoutError.code = "EPUB_OPEN_TIMEOUT";
          reject(timeoutError);
        }, OPEN_TIMEOUT_MS);
      }),
    ]);
    reportDiagnostic("epub_packaging_ready", { file: describeFile(file), inputType });
    return book;
  } catch (error) {
    book.destroy();
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

async function loadOptional(promise, file, event) {
  let timeoutId;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timeoutId = window.setTimeout(() => reject(new Error("Optional EPUB data timed out")), 3000);
      }),
    ]);
  } catch (error) {
    reportDiagnostic(event, { file: describeFile(file), error: describeError(error) }, "warning");
    return null;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

function refreshWhenAssetsReady(book, file) {
  book.opened.then(async () => {
    if (state.book !== book || !state.rendition) return;

    reportDiagnostic("epub_assets_ready", { file: describeFile(file) });
    const location = state.rendition.currentLocation()?.start?.cfi;
    await state.rendition.display(location || undefined);
  }).catch((error) => {
    reportDiagnostic("epub_assets_refresh_failed", { file: describeFile(file), error: describeError(error) }, "warning");
  });
}

function showLoading(message) {
  ui.loadingText.textContent = message;
  ui.loading.hidden = false;
}

function hideLoading() {
  ui.loading.hidden = true;
}

function savedState() {
  try {
    return JSON.parse(localStorage.getItem(state.storageKey)) || {};
  } catch {
    return {};
  }
}

function savePosition(cfi) {
  if (!state.storageKey || !cfi) return;
  const previous = savedState();
  localStorage.setItem(state.storageKey, JSON.stringify({ ...previous, cfi, updatedAt: Date.now() }));
}

function updateFontSize(value) {
  state.fontSize = Math.max(75, Math.min(160, value));
  ui.resetFont.textContent = `${state.fontSize}%`;
  ui.fontSizeRange.value = String(state.fontSize);
  ui.fontSizeRangeValue.textContent = `${state.fontSize}%`;
  localStorage.setItem("librum-font-size", String(state.fontSize));
  if (state.rendition) state.rendition.themes.fontSize(`${state.fontSize}%`);
}

const TEXT_SELECTOR = "html, body, p, div, span, a, em, strong, b, i, li, dd, dt, "
  + "blockquote, h1, h2, h3, h4, h5, h6, td, th, figcaption, section, article";

// Books often set font-family / line-height directly on their own elements, which
// beats a body-level override. Inject an !important rule targeting every text element
// into each rendered section so the reader's font and spacing actually win.
function userStyleCss() {
  const family = fontFamilies[state.font] || fontFamilies.serif;
  return `${TEXT_SELECTOR} { font-family: ${family} !important; line-height: ${state.lineHeight} !important; }`
    // 큰 이미지가 화면 폭이나 높이를 넘지 않도록 제한해, 레이아웃이 옆으로 밀리거나
    // 세로 스크롤이 생기는 것을 막는다.
    + " img, svg, image, video { max-width: 100% !important; max-height: 100vh !important; height: auto !important; }"
    // 브라우저가 가로 스와이프를 스크롤/뒤로가기로 가로채지 못하게 한다(세로 스크롤만 허용).
    + " html, body { touch-action: pan-y !important; overscroll-behavior-x: contain !important; }";
}

function injectUserStyle(contents) {
  if (!contents?.document) return;
  const doc = contents.document;
  let style = doc.getElementById("librum-user-style");
  if (!style) {
    style = doc.createElement("style");
    style.id = "librum-user-style";
    (doc.head || doc.documentElement).appendChild(style);
  }
  style.textContent = userStyleCss();
}

function applyUserStyles() {
  if (!state.rendition) return;
  const contents = state.rendition.getContents?.() || [];
  (Array.isArray(contents) ? contents : [contents]).forEach(injectUserStyle);
}

function applyFont(value) {
  state.font = fontFamilies[value] ? value : "serif";
  ui.fontSelect.value = state.font;
  localStorage.setItem("librum-font", state.font);
  applyUserStyles();
}

function updateLineHeight(value) {
  state.lineHeight = Math.max(1.4, Math.min(2.4, Math.round(Number(value) * 10) / 10));
  ui.lineHeightRange.value = String(state.lineHeight);
  ui.lineHeightValue.textContent = state.lineHeight.toFixed(1);
  localStorage.setItem("librum-line-height", String(state.lineHeight));
  applyUserStyles();
}

function applyTheme(value) {
  state.theme = value;
  ui.theme.value = value;
  document.body.classList.remove("theme-sepia", "theme-night");
  if (value !== "paper") document.body.classList.add(`theme-${value}`);
  localStorage.setItem("librum-theme", value);

  if (!state.rendition) return;
  state.rendition.themes.select(value);
  applyFont(state.font);
  updateLineHeight(state.lineHeight);
}

function setupThemes() {
  // Colors live in the registered theme; font-family and line-height are handled by
  // injectUserStyle so they override the book's own element-level rules.
  state.rendition.themes.register("paper", {
    body: { background: "#fdfbf5", color: "#263338" },
    a: { color: "#117a65" },
  });
  state.rendition.themes.register("sepia", {
    body: { background: "#f3e6cb", color: "#4d3926" },
    a: { color: "#8c5e2d" },
  });
  state.rendition.themes.register("night", {
    body: { background: "#192321", color: "#e9ece4" },
    a: { color: "#79cdb5" },
  });
  // Re-apply user font/spacing to every section as it loads.
  state.rendition.hooks.content.register(injectUserStyle);
  updateFontSize(state.fontSize);
  applyTheme(state.theme);
  applyFont(state.font);
  updateLineHeight(state.lineHeight);
}

function flattenToc(items, depth = 0) {
  return items.flatMap((item) => [
    { ...item, depth },
    ...(item.subitems ? flattenToc(item.subitems, depth + 1) : []),
  ]);
}

function renderToc(items) {
  const entries = flattenToc(items);
  ui.toc.replaceChildren();
  ui.chapterCount.textContent = entries.length ? `${entries.length}` : "";

  if (!entries.length) {
    const text = document.createElement("p");
    text.className = "toc-empty";
    text.textContent = "목차 정보가 없습니다.";
    ui.toc.append(text);
    return;
  }

  const build = (list, depth, parent) => {
    list.forEach((item) => {
      const hasKids = Boolean(item.subitems && item.subitems.length);
      const row = document.createElement("div");
      row.className = "toc-row";

      const button = document.createElement("button");
      button.type = "button";
      button.className = `toc-item depth-${Math.min(depth, 2)}`;
      button.textContent = (item.label || "").trim() || "제목 없음";
      button.dataset.href = item.href;
      button.addEventListener("click", () => {
        if (translation.active) exitTranslationMode();
        state.rendition.display(item.href);
      });

      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "toc-toggle" + (hasKids ? "" : " spacer");
      toggle.textContent = hasKids ? "\u25B8" : "";
      row.append(toggle, button);
      parent.append(row);

      if (hasKids) {
        const kidBox = document.createElement("div");
        kidBox.className = "toc-children";
        kidBox.hidden = true;
        toggle.setAttribute("aria-expanded", "false");
        toggle.addEventListener("click", (e) => {
          e.stopPropagation();
          kidBox.hidden = !kidBox.hidden;
          toggle.textContent = kidBox.hidden ? "\u25B8" : "\u25BE";
          toggle.setAttribute("aria-expanded", String(!kidBox.hidden));
        });
        parent.append(kidBox);
        build(item.subitems, depth + 1, kidBox);
      }
    });
  };
  build(items, 0, ui.toc);
}

function nearestChapter(href) {
  const items = [...ui.toc.querySelectorAll(".toc-item")];
  const matchingItem = items.find((item) => href && href.includes(item.dataset.href.split("#")[0]));
  items.forEach((item) => item.classList.toggle("active", item === matchingItem));
  // 현재 읽는 항목이 접힌 하위 목차 안에 있으면 상위를 자동으로 펼친다
  let box = matchingItem?.closest(".toc-children");
  while (box) {
    box.hidden = false;
    const row = box.previousElementSibling;
    const toggle = row?.querySelector(".toc-toggle");
    if (toggle && !toggle.classList.contains("spacer")) {
      toggle.textContent = "\u25BE";
      toggle.setAttribute("aria-expanded", "true");
    }
    box = box.parentElement?.closest(".toc-children");
  }
  return matchingItem?.textContent || "읽는 중";
}

function pageLabel(location) {
  // 위치(locations)는 콘텐츠 분량에 비례하는 조각이라 '쪽'처럼 쓸 수 있다.
  try {
    const total = state.book?.locations?.length?.() || 0;
    if (!total) return "";
    let current = location.start.location;
    if (typeof current !== "number") current = state.book.locations.locationFromCfi(location.start.cfi);
    current = Math.min(total, Math.max(1, (current || 0) + 1));
    return `${current} / ${total}쪽`;
  } catch {
    return "";
  }
}

function onRelocated(location) {
  const percentage = Math.round((location.start.percentage || 0) * 1000) / 10;
  const chapterName = nearestChapter(location.start.href);
  ui.chapter.textContent = chapterName;
  ui.progress.textContent = state.locationsReady ? pageLabel(location) : "";
  if (state.locationsReady) ui.range.value = percentage;
  savePosition(location.start.cfi);
  scheduleLibraryProgressUpdate(location.start.cfi, state.locationsReady ? percentage : undefined);
}

async function generateLocations() {
  const fileSize = state.book?.archive?.zip?.files ? Object.keys(state.book.archive.zip.files).length : 0;
  const charactersPerLocation = fileSize > 400 ? 2200 : 1200;
  try {
    await state.book.locations.generate(charactersPerLocation);
    state.locationsReady = true;
    ui.range.disabled = false;
    const current = state.rendition.currentLocation();
    if (current?.start) onRelocated(current);
  } catch (error) {
    reportDiagnostic("locations_generation_failed", { error: describeError(error) }, "warning");
    ui.chapter.textContent = "페이지 이동은 화살표로 할 수 있습니다.";
  }
}

async function openBook(file) {
  if (!file) return;
  if (!window.ePub || !window.JSZip) {
    reportDiagnostic("epub_engine_unavailable", {
      file: describeFile(file),
      epubEngineAvailable: Boolean(window.ePub),
      zipLibraryAvailable: Boolean(window.JSZip),
    }, "error");
    showLoading("리더 구성 요소를 불러오지 못했습니다. 인터넷 연결을 확인해 주세요.");
    return;
  }

  const openedAt = performance.now();
  if (translation.active) exitTranslationMode();
  closeComic();
  setLibraryViewVisible(false);
  setNotesViewVisible(false);
  hideHighlightPopup();
  notes.items = [];
  ui.notesToggle.hidden = true;
  state.libraryId = null;
  reportDiagnostic("epub_open_started", { file: describeFile(file) });
  showLoading("EPUB 파일을 여는 중...");
  ui.viewer.replaceChildren();
  ui.viewer.classList.remove("is-ready");
  ui.range.disabled = true;
  ui.range.value = 0;
  ui.progress.textContent = "";
  state.locationsReady = false;
  state.storageKey = `librum-position:${file.name}:${file.size}:${file.lastModified}`;
  // 노트 복원이 저장된 책 ID를 참조하므로 여는 시점에 미리 확정한다.
  state.libraryId = libraryBookId(file.name, file.size);

  try {
    const data = await file.arrayBuffer();
    reportDiagnostic("epub_bytes_ready", { file: describeFile(file), bytesRead: data.byteLength });
    state.book?.destroy();
    state.book = null;

    try {
      showLoading("EPUB 구조를 확인하는 중...");
      state.book = await openArchive(data, file, "array_buffer");
    } catch (error) {
      if (error?.code !== "EPUB_OPEN_TIMEOUT") throw error;

      reportDiagnostic("epub_blob_retry_started", { file: describeFile(file), error: describeError(error) }, "warning");
      showLoading("호환 방식으로 EPUB를 다시 여는 중...");
      state.book = await openArchive(file, file, "file_blob");
    }

    const metadata = await loadOptional(state.book.loaded.metadata, file, "metadata_load_failed") || {};
    const navigation = await loadOptional(state.book.loaded.navigation, file, "navigation_load_failed") || { toc: [] };
    const title = metadata.title || file.name.replace(/\.epub$/i, "");
    const creator = metadata.creator || "";
    ui.title.textContent = title;
    ui.summaryTitle.textContent = title;
    ui.author.textContent = creator;
    ui.summary.hidden = false;
    renderToc(navigation?.toc || []);

    state.rendition = state.book.renderTo("viewer", {
      width: "100%",
      height: "100%",
      flow: state.flow,
      spread: "none",
    });
    setupThemes();
    setupSelectionHandlers();
    state.rendition.on("relocated", onRelocated);
    state.rendition.on("displayed", () => {
      ui.viewer.classList.add("is-ready");
      ui.stagePrevious.hidden = false;
      ui.stageNext.hidden = false;
      ui.notesToggle.hidden = false;
      hideLoading();
    });

    ui.empty.hidden = true;
    const stored = savedState();
    await state.rendition.display(stored.cfi || undefined);
    refreshWhenAssetsReady(state.book, file);
    restoreNotes();
    ui.chapter.textContent = stored.cfi ? "이전 읽기 위치를 불러왔습니다." : "읽는 중";
    reportDiagnostic("epub_open_completed", {
      file: describeFile(file),
      elapsedMs: Math.round(performance.now() - openedAt),
      tocItems: navigation?.toc?.length || 0,
      restoredPosition: Boolean(stored.cfi),
    });
    saveOpenedBookToLibrary(file, data, title, creator);
    window.setTimeout(() => generateLocations(), 700);
  } catch (error) {
    console.error(error);
    reportDiagnostic("epub_open_failed", {
      file: describeFile(file),
      elapsedMs: Math.round(performance.now() - openedAt),
      error: describeError(error),
    }, "error");
    ui.empty.hidden = false;
    ui.viewer.classList.remove("is-ready");
    ui.stagePrevious.hidden = true;
    ui.stageNext.hidden = true;
    ui.chapter.textContent = "파일을 열 수 없습니다.";
    ui.progress.textContent = "";
    const detail = error instanceof Error && error.message ? ` (${error.message})` : "";
    showLoading(`이 EPUB의 본문 구성을 읽을 수 없습니다${detail}`);
  }
}

/* --- 만화(CBZ) 뷰어 --- */

// "2.jpg"가 "10.jpg"보다 앞에 오도록 숫자를 자연 순서로 비교한다.
function naturalCompare(a, b) {
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
}

function isComicFile(file) {
  return /\.(cbz|cbr)$/i.test(file.name || "");
}

// RAR 서명(Rar!\x1A\x07)이면 CBR(브라우저에서 해제 불가)로 판단한다.
function looksLikeRar(bytes) {
  return bytes[0] === 0x52 && bytes[1] === 0x61 && bytes[2] === 0x72 && bytes[3] === 0x21;
}

function comicStorageKey(file) {
  return `librum-position:${file.name}:${file.size}:${file.lastModified}`;
}

function releaseComicUrls() {
  comic.urls.forEach((url) => url && URL.revokeObjectURL(url));
  comic.urls = [];
}

// EPUB 리더(rendition)와 만화 뷰어는 한 번에 하나만 살아 있게 한다.
function teardownRendition() {
  try { state.book?.destroy(); } catch { /* noop */ }
  state.book = null;
  state.rendition = null;
  state.locationsReady = false;
  ui.viewer.replaceChildren();
  ui.viewer.classList.remove("is-ready");
}

function closeComic() {
  comic.active = false;
  releaseComicUrls();
  comic.entries = [];
  comic.index = 0;
  comic.storageKey = null;
  ui.comicPage.removeAttribute("src");
  ui.comicViewer.hidden = true;
  ui.comicControls.hidden = true;
  document.body.classList.remove("comic-mode");
}

async function ensureComicUrl(index) {
  if (comic.urls[index]) return comic.urls[index];
  const blob = await comic.entries[index].async("blob");
  const url = URL.createObjectURL(blob);
  comic.urls[index] = url;
  return url;
}

function saveComicPosition() {
  if (!comic.storageKey) return;
  localStorage.setItem(comic.storageKey, JSON.stringify({ page: comic.index, updatedAt: Date.now() }));
}

function updateComicProgress() {
  const total = comic.entries.length;
  const current = comic.index + 1;
  const percentage = total ? Math.round((current / total) * 1000) / 10 : 0;
  ui.progress.textContent = total ? `${current} / ${total}쪽` : "";
  ui.range.disabled = false;
  ui.range.value = percentage;
  ui.chapter.textContent = "만화 보기";
  if (state.libraryId) scheduleLibraryProgressUpdate(null, percentage, { page: comic.index });
}

function applyComicTransform() {
  ui.comicPage.style.transform = `translate(${comic.panX}px, ${comic.panY}px) scale(${comic.zoom})`;
}

// 현재 배율에서 이미지가 화면 밖으로 넘치는 양(이동 가능 범위)을 계산한다.
function comicBounds() {
  const vw = ui.comicViewer.clientWidth || 0;
  const vh = ui.comicViewer.clientHeight || 0;
  const bw = (ui.comicPage.offsetWidth || 0) * comic.zoom;
  const bh = (ui.comicPage.offsetHeight || 0) * comic.zoom;
  return { vw, vh, bw, bh, maxX: Math.max(0, (bw - vw) / 2), maxY: Math.max(0, (bh - vh) / 2) };
}

function clampPan() {
  const b = comicBounds();
  comic.panX = Math.max(-b.maxX, Math.min(b.maxX, comic.panX));
  comic.panY = Math.max(-b.maxY, Math.min(b.maxY, comic.panY));
}

function updateComicZoomLabel() {
  if (ui.comicZoomValue) ui.comicZoomValue.textContent = `${Math.round(comic.zoom * 100)}%`;
}

function setComicZoom(zoom) {
  comic.zoom = Math.max(1, Math.min(COMIC_MAX_ZOOM, Math.round(zoom * 100) / 100));
  if (comic.zoom === 1) { comic.panX = 0; }
  clampPan();
  applyComicTransform();
  updateComicZoomLabel();
  ui.comicViewer.classList.toggle("zoomed", comic.zoom > 1);
}

// 페이지를 새로 띄우거나 맞춤 방식을 바꿀 때 배율·위치를 초기화한다.
// 폭맞춤에서 세로로 긴 이미지는 맨 위부터 보이게 한다.
function resetComicView() {
  comic.zoom = 1;
  comic.panX = 0;
  const b = comicBounds();
  comic.panY = comic.fit === "width" && b.maxY > 0 ? b.maxY : 0;
  applyComicTransform();
  updateComicZoomLabel();
  ui.comicViewer.classList.remove("zoomed");
}

function setComicFit(fit) {
  comic.fit = fit === "width" ? "width" : "page";
  localStorage.setItem("librum-comic-fit", comic.fit);
  ui.comicViewer.classList.toggle("fit-width", comic.fit === "width");
  ui.comicViewer.classList.toggle("fit-page", comic.fit === "page");
  // 버튼에는 '다음에 바뀔 방식'을 표시해 동작을 알기 쉽게 한다.
  if (ui.comicFit) ui.comicFit.textContent = comic.fit === "page" ? "폭맞춤" : "화면맞춤";
  resetComicView();
}

async function showComicPage(index) {
  if (!comic.active || !comic.entries.length) return;
  comic.index = Math.max(0, Math.min(comic.entries.length - 1, index));
  try {
    ui.comicPage.src = await ensureComicUrl(comic.index);
    await ui.comicPage.decode().catch(() => {});
  } catch (error) {
    reportDiagnostic("comic_page_decode_failed", { index: comic.index, error: describeError(error) }, "warning");
  }
  resetComicView();
  updateComicProgress();
  saveComicPosition();
  // 다음/이전 페이지를 미리 디코드해 넘김을 매끄럽게 한다.
  [comic.index + 1, comic.index - 1].forEach((n) => {
    if (n >= 0 && n < comic.entries.length && !comic.urls[n]) ensureComicUrl(n).catch(() => {});
  });
}

function comicNext() { if (comic.index < comic.entries.length - 1) showComicPage(comic.index + 1); }
function comicPrev() { if (comic.index > 0) showComicPage(comic.index - 1); }

function comicTapTurn(clientX) {
  const rect = ui.comicViewer.getBoundingClientRect();
  const x = clientX - rect.left;
  if (x < rect.width * 0.35) comicPrev();
  else if (x > rect.width * 0.65) comicNext();
}

// 확대(핀치·휠·버튼)·이동(드래그)·페이지 넘김(탭/스와이프/휠)을 함께 처리한다.
let comicGesture = null;
function bindComicGestures() {
  if (ui.comicViewer.dataset.bound) return;
  ui.comicViewer.dataset.bound = "1";
  const el = ui.comicViewer;

  const dist = (a, b) => Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);

  el.addEventListener("touchstart", (event) => {
    if (event.touches.length === 2) {
      comicGesture = { mode: "pinch", startDist: dist(event.touches[0], event.touches[1]), startZoom: comic.zoom };
    } else if (event.touches.length === 1) {
      const t = event.touches[0];
      comicGesture = { mode: "tap", x: t.clientX, y: t.clientY, lastX: t.clientX, lastY: t.clientY, startPanX: comic.panX, startPanY: comic.panY, at: event.timeStamp || Date.now(), moved: false };
    }
  }, { passive: true });

  el.addEventListener("touchmove", (event) => {
    if (!comicGesture) return;
    if (comicGesture.mode === "pinch" && event.touches.length === 2) {
      event.preventDefault();
      const ratio = dist(event.touches[0], event.touches[1]) / (comicGesture.startDist || 1);
      setComicZoom(comicGesture.startZoom * ratio);
      return;
    }
    if (event.touches.length !== 1) return;
    const t = event.touches[0];
    const dx = t.clientX - comicGesture.x, dy = t.clientY - comicGesture.y;
    if (Math.abs(dx) > 8 || Math.abs(dy) > 8) comicGesture.moved = true;
    const b = comicBounds();
    const pannable = b.maxX > 0 || b.maxY > 0;
    if (pannable) {
      event.preventDefault();
      comicGesture.mode = "pan";
      comic.panX = comicGesture.startPanX + dx;
      comic.panY = comicGesture.startPanY + dy;
      clampPan();
      applyComicTransform();
    }
    comicGesture.lastX = t.clientX; comicGesture.lastY = t.clientY;
  }, { passive: false });

  el.addEventListener("touchend", (event) => {
    const g = comicGesture; comicGesture = null;
    if (!g) return;
    if (g.mode === "pinch" || g.mode === "pan") return; // 확대·이동은 넘김으로 처리하지 않음
    const t = event.changedTouches?.[0];
    if (!t) return;
    const dx = t.clientX - g.x, dy = t.clientY - g.y;
    const dt = (event.timeStamp || Date.now()) - g.at;
    if (!g.moved || (Math.abs(dx) < 10 && Math.abs(dy) < 10)) {
      comicTapTurn(t.clientX); // 제자리 탭 → 좌/우 페이지 넘김
    } else if (Math.abs(dx) >= 45 && Math.abs(dx) > Math.abs(dy) * 1.3 && dt < 800) {
      if (dx < 0) comicNext(); else comicPrev(); // 스와이프(화면맞춤일 때)
    }
  }, { passive: true });

  // 데스크톱: 확대 시 드래그로 이동, 그 외에는 클릭으로 좌/우 넘김.
  let mouseDrag = null;
  el.addEventListener("mousedown", (event) => {
    const b = comicBounds();
    if (b.maxX > 0 || b.maxY > 0) {
      mouseDrag = { x: event.clientX, y: event.clientY, panX: comic.panX, panY: comic.panY, moved: false };
      el.classList.add("grabbing");
      event.preventDefault();
    }
  });
  window.addEventListener("mousemove", (event) => {
    if (!mouseDrag) return;
    const dx = event.clientX - mouseDrag.x, dy = event.clientY - mouseDrag.y;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) mouseDrag.moved = true;
    comic.panX = mouseDrag.panX + dx;
    comic.panY = mouseDrag.panY + dy;
    clampPan();
    applyComicTransform();
  });
  window.addEventListener("mouseup", () => {
    if (!mouseDrag) return;
    const moved = mouseDrag.moved;
    mouseDrag = null;
    el.classList.remove("grabbing");
    if (moved) comicSuppressClick = true;
  });
  el.addEventListener("click", (event) => {
    if (comicSuppressClick) { comicSuppressClick = false; return; }
    comicTapTurn(event.clientX);
  });
  el.addEventListener("dblclick", (event) => {
    event.preventDefault();
    if (comic.zoom > 1) resetComicView();
    else {
      const rect = el.getBoundingClientRect();
      setComicZoom(2);
      // 더블클릭 지점이 중심에 오도록 이동
      comic.panX = (rect.left + rect.width / 2 - event.clientX) * (comic.zoom - 1);
      comic.panY = (rect.top + rect.height / 2 - event.clientY) * (comic.zoom - 1);
      clampPan();
      applyComicTransform();
    }
  });

  // 휠: Ctrl(또는 ⌘)+휠 = 확대/축소, 세로로 넘치면 = 세로 이동(끝에서 페이지 넘김), 아니면 페이지 넘김.
  el.addEventListener("wheel", (event) => {
    if (Math.abs(event.deltaY) < 4) return;
    event.preventDefault();
    if (event.ctrlKey || event.metaKey) {
      setComicZoom(comic.zoom * (event.deltaY < 0 ? 1.12 : 0.9));
      return;
    }
    const b = comicBounds();
    if (b.maxY > 0) {
      const before = comic.panY;
      comic.panY -= event.deltaY;
      clampPan();
      applyComicTransform();
      if (comic.panY !== before) return; // 아직 스크롤할 여백이 있으면 넘기지 않음
    }
    const now = event.timeStamp || Date.now();
    if (now - lastWheelAt < 350) return;
    lastWheelAt = now;
    if (event.deltaY > 0) comicNext(); else comicPrev();
  }, { passive: false });
}
let comicSuppressClick = false;

async function saveComicToLibrary(file, data, coverBlob) {
  if (!window.LibrumLibrary) return;
  const id = libraryBookId(file.name, file.size);
  state.libraryId = id;
  try {
    const existing = await window.LibrumLibrary.get(id);
    if (existing) {
      await window.LibrumLibrary.update(id, { lastOpenedAt: Date.now() });
    } else {
      const title = file.name.replace(/\.(cbz|cbr)$/i, "");
      await window.LibrumLibrary.add({
        schemaVersion: 1,
        id,
        type: "comic",
        title,
        creator: "",
        fileName: file.name,
        fileSize: file.size,
        lastModified: file.lastModified || 0,
        coverBlob,
        cfi: null,
        page: comic.index,
        percentage: 0,
        addedAt: Date.now(),
        lastOpenedAt: Date.now(),
        updatedAt: Date.now(),
      }, new Blob([data], { type: "application/vnd.comicbook+zip" }));
      reportDiagnostic("library_comic_added", { bookId: id, file: describeFile(file) });
    }
    await refreshLibraryGrid();
  } catch (error) {
    reportDiagnostic("comic_library_save_failed", { bookId: id, error: describeError(error) }, "warning");
  }
}

async function openComic(file) {
  if (!window.JSZip) {
    showLoading("압축 해제 구성 요소를 불러오지 못했습니다. 인터넷 연결을 확인해 주세요.");
    return;
  }
  const openedAt = performance.now();
  if (translation.active) exitTranslationMode();
  setLibraryViewVisible(false);
  setNotesViewVisible(false);
  hideHighlightPopup();
  notes.items = [];
  ui.notesToggle.hidden = true;
  teardownRendition();
  closeComic();
  reportDiagnostic("comic_open_started", { file: describeFile(file) });
  showLoading("만화책을 여는 중...");
  ui.progress.textContent = "";
  ui.range.disabled = true;
  ui.range.value = 0;
  state.storageKey = null;
  state.libraryId = libraryBookId(file.name, file.size);
  comic.storageKey = comicStorageKey(file);

  try {
    const data = await file.arrayBuffer();
    const bytes = new Uint8Array(data.slice(0, 4));
    if (/\.cbr$/i.test(file.name) || looksLikeRar(bytes)) {
      throw Object.assign(new Error("CBR(RAR) 형식은 지원하지 않습니다."), { code: "COMIC_CBR" });
    }
    const zip = await window.JSZip.loadAsync(data);
    const entries = Object.values(zip.files)
      .filter((entry) => !entry.dir && IMAGE_RE.test(entry.name))
      .sort((a, b) => naturalCompare(a.name, b.name));
    if (!entries.length) throw new Error("압축 파일 안에서 이미지를 찾을 수 없습니다.");

    comic.active = true;
    comic.entries = entries;
    comic.urls = new Array(entries.length);
    document.body.classList.add("comic-mode");
    ui.comicControls.hidden = false;
    setComicFit(comic.fit);

    const title = file.name.replace(/\.(cbz|cbr)$/i, "");
    ui.title.textContent = title;
    ui.summaryTitle.textContent = title;
    ui.author.textContent = "";
    ui.summary.hidden = false;
    renderToc([]);
    ui.chapterCount.textContent = `${entries.length}쪽`;

    ui.empty.hidden = true;
    ui.comicViewer.hidden = false;
    ui.stagePrevious.hidden = false;
    ui.stageNext.hidden = false;
    bindComicGestures();

    const stored = (() => { try { return JSON.parse(localStorage.getItem(comic.storageKey)) || {}; } catch { return {}; } })();
    const startPage = Number.isInteger(stored.page) ? Math.min(stored.page, entries.length - 1) : 0;
    await showComicPage(startPage);
    hideLoading();
    ui.chapter.textContent = stored.page ? "이전 읽기 위치를 불러왔습니다." : "만화 보기";

    const coverBlob = await comic.entries[0].async("blob").catch(() => null);
    saveComicToLibrary(file, data, coverBlob);
    reportDiagnostic("comic_open_completed", {
      file: describeFile(file),
      pages: entries.length,
      elapsedMs: Math.round(performance.now() - openedAt),
      restoredPosition: Boolean(stored.page),
    });
  } catch (error) {
    console.error(error);
    closeComic();
    reportDiagnostic("comic_open_failed", { file: describeFile(file), error: describeError(error) }, "error");
    ui.empty.hidden = false;
    ui.stagePrevious.hidden = true;
    ui.stageNext.hidden = true;
    ui.progress.textContent = "";
    if (error?.code === "COMIC_CBR") {
      showLoading("CBR(RAR) 만화는 아직 지원하지 않습니다. CBZ(ZIP)로 변환해 주세요.");
    } else {
      const detail = error instanceof Error && error.message ? ` (${error.message})` : "";
      showLoading(`이 만화 파일을 열 수 없습니다${detail}`);
    }
  }
}

// 확장자로 EPUB/만화를 구분해 알맞은 뷰어로 연다.
function openFile(file) {
  if (!file) return;
  if (isComicFile(file)) return openComic(file);
  return openBook(file);
}

const libraryUi = {
  coverUrls: [],
  progressTimer: null,
};

function libraryBookId(fileName, fileSize) {
  return `b-${hashText(fileName)}-${fileSize}`;
}

function positionStorageKey(meta) {
  return `librum-position:${meta.fileName}:${meta.fileSize}:${meta.lastModified}`;
}

function setLibraryViewVisible(visible) {
  ui.libraryView.hidden = !visible;
  if (visible && translation.active) exitTranslationMode();
}

async function loadCoverBlob() {
  try {
    const coverUrl = await Promise.race([
      state.book.coverUrl(),
      new Promise((resolve) => window.setTimeout(() => resolve(null), 3000)),
    ]);
    if (!coverUrl) return null;
    return await (await fetch(coverUrl)).blob();
  } catch {
    return null;
  }
}

async function saveOpenedBookToLibrary(file, data, title, creator) {
  if (!window.LibrumLibrary) return;
  const id = libraryBookId(file.name, file.size);
  state.libraryId = id;
  try {
    const existing = await window.LibrumLibrary.get(id);
    if (existing) {
      await window.LibrumLibrary.update(id, { lastOpenedAt: Date.now(), title, creator });
    } else {
      const coverBlob = await loadCoverBlob();
      await window.LibrumLibrary.add({
        schemaVersion: 1,
        id,
        title,
        creator,
        fileName: file.name,
        fileSize: file.size,
        lastModified: file.lastModified || 0,
        coverBlob,
        cfi: null,
        percentage: 0,
        addedAt: Date.now(),
        lastOpenedAt: Date.now(),
        updatedAt: Date.now(),
      }, new Blob([data], { type: "application/epub+zip" }));
      reportDiagnostic("library_book_added", { bookId: id, file: describeFile(file) });
    }
    await refreshLibraryGrid();
  } catch (error) {
    reportDiagnostic("library_save_failed", { bookId: id, error: describeError(error) }, "warning");
  }
}

function scheduleLibraryProgressUpdate(cfi, percentage, extra) {
  if (!window.LibrumLibrary || !state.libraryId) return;
  const bookId = state.libraryId;
  const patch = { cfi, lastOpenedAt: Date.now(), ...extra };
  if (typeof percentage === "number") patch.percentage = percentage;
  window.clearTimeout(libraryUi.progressTimer);
  libraryUi.progressTimer = window.setTimeout(() => {
    window.LibrumLibrary.update(bookId, patch).catch(() => {});
  }, 1500);
}

async function openBookFromLibrary(meta) {
  if (!window.LibrumLibrary) return;
  try {
    const blob = await window.LibrumLibrary.getFile(meta.id);
    if (!blob) throw new Error("저장된 책 파일을 찾을 수 없습니다.");
    const isComic = meta.type === "comic";
    const file = new File([blob], meta.fileName, {
      type: isComic ? "application/vnd.comicbook+zip" : "application/epub+zip",
      lastModified: meta.lastModified || 0,
    });
    setLibraryViewVisible(false);
    if (isComic) await openComic(file);
    else await openBook(file);
  } catch (error) {
    reportDiagnostic("library_open_failed", { bookId: meta.id, error: describeError(error) }, "error");
    window.alert("이 책을 여는 데 실패했습니다. 파일을 다시 추가해 주세요.");
  }
}

async function removeBookFromLibrary(meta) {
  if (!window.confirm(`'${meta.title || meta.fileName}'을(를) 서재에서 삭제할까요?\n읽던 위치 기록도 함께 삭제됩니다.`)) return;
  try {
    await window.LibrumLibrary.remove(meta.id);
    localStorage.removeItem(positionStorageKey(meta));
    if (state.libraryId === meta.id) state.libraryId = null;
    reportDiagnostic("library_book_removed", { bookId: meta.id });
    const books = await refreshLibraryGrid();
    if (!books.length && !state.book) {
      setLibraryViewVisible(false);
      ui.empty.hidden = false;
    }
  } catch (error) {
    reportDiagnostic("library_remove_failed", { bookId: meta.id, error: describeError(error) }, "warning");
  }
}

function buildLibraryCard(meta) {
  const card = document.createElement("div");
  card.className = "library-card";
  card.dataset.bookId = meta.id;

  const cover = document.createElement("button");
  cover.type = "button";
  cover.className = "cover";
  cover.title = meta.title || meta.fileName;
  cover.setAttribute("aria-label", `${meta.title || meta.fileName} 이어 읽기`);
  if (meta.coverBlob) {
    const image = document.createElement("img");
    const url = URL.createObjectURL(meta.coverBlob);
    libraryUi.coverUrls.push(url);
    image.src = url;
    image.alt = "";
    cover.append(image);
  } else {
    const fallback = document.createElement("span");
    fallback.className = "fallback-title";
    fallback.textContent = meta.title || meta.fileName.replace(/\.epub$/i, "");
    cover.append(fallback);
  }
  cover.addEventListener("click", () => openBookFromLibrary(meta));

  const removeButton = document.createElement("button");
  removeButton.type = "button";
  removeButton.className = "remove-book";
  removeButton.title = "서재에서 삭제";
  removeButton.setAttribute("aria-label", `${meta.title || meta.fileName} 삭제`);
  removeButton.textContent = "×";
  removeButton.addEventListener("click", (event) => {
    event.stopPropagation();
    removeBookFromLibrary(meta);
  });
  card.append(cover, removeButton);

  const name = document.createElement("p");
  name.className = "book-name";
  name.textContent = meta.title || meta.fileName.replace(/\.epub$/i, "");
  card.append(name);

  const metaRow = document.createElement("p");
  metaRow.className = "book-meta";
  const progressLabel = document.createElement("span");
  const percentage = Math.max(0, Math.min(100, Math.round(meta.percentage || 0)));
  const started = meta.type === "comic" ? (meta.page > 0 || percentage > 0) : Boolean(meta.cfi);
  progressLabel.textContent = started ? `${percentage}% 읽음` : "새 책";
  const author = document.createElement("span");
  author.textContent = meta.creator || "";
  metaRow.append(progressLabel, author);
  card.append(metaRow);

  const progressBar = document.createElement("div");
  progressBar.className = "progress-mini";
  const progressFill = document.createElement("span");
  progressFill.style.width = `${percentage}%`;
  progressBar.append(progressFill);
  card.append(progressBar);

  return card;
}

async function refreshLibraryGrid() {
  if (!window.LibrumLibrary) return [];
  let books = [];
  try {
    books = await window.LibrumLibrary.list();
  } catch (error) {
    reportDiagnostic("library_list_failed", { error: describeError(error) }, "warning");
    return [];
  }

  libraryUi.coverUrls.forEach((url) => URL.revokeObjectURL(url));
  libraryUi.coverUrls = [];

  if (!books.length) {
    const empty = document.createElement("p");
    empty.className = "library-empty";
    empty.textContent = "서재가 비어 있습니다. EPUB 파일을 추가해 보세요.";
    ui.libraryGrid.replaceChildren(empty);
  } else {
    ui.libraryGrid.replaceChildren(...books.map((meta) => buildLibraryCard(meta)));
  }
  ui.showLibrary.hidden = !books.length;
  return books;
}

async function initializeLibrary() {
  if (!window.LibrumLibrary) return;
  const books = await refreshLibraryGrid();
  if (books.length && !state.book) {
    ui.empty.hidden = true;
    setLibraryViewVisible(true);
  }
}

/* --- 형광펜·메모 --- */
const HL_COLORS = { yellow: "#ffe27a", green: "#a6e5a0", pink: "#ffb0c8", blue: "#a6c8ff" };
const notes = {
  items: [],
  pending: null, // { cfiRange, text } — 선택 직후 아직 저장 안 된 상태
  active: null, // 현재 팝업이 가리키는 저장된 노트
};

function newNoteId() {
  if (window.crypto?.randomUUID) return `n-${window.crypto.randomUUID()}`;
  return `n-${Date.now()}-${Math.round(Math.random() * 1e9)}`;
}

function hideHighlightPopup() {
  ui.highlightPopup.hidden = true;
  notes.pending = null;
  notes.active = null;
}

function positionPopup(contents, range, below) {
  const rect = range.getBoundingClientRect();
  const frame = contents.document.defaultView.frameElement;
  if (!frame) return;
  const frameRect = frame.getBoundingClientRect();
  const stageRect = ui.readingStage.getBoundingClientRect();
  const left = frameRect.left + rect.left + rect.width / 2 - stageRect.left;
  const top = below
    ? frameRect.top + rect.bottom - stageRect.top + 10
    : frameRect.top + rect.top - stageRect.top - 8;
  ui.highlightPopup.style.left = `${Math.max(70, Math.min(left, stageRect.width - 70))}px`;
  ui.highlightPopup.style.top = `${Math.max(44, top)}px`;
  ui.highlightPopup.style.transform = below ? "translate(-50%, 0)" : "translate(-50%, -100%)";
}

function drawHighlight(note) {
  if (!state.rendition) return;
  state.rendition.annotations.add(
    "highlight",
    note.cfi,
    { id: note.id },
    null,
    `hl-${note.color}`,
    { fill: HL_COLORS[note.color] || HL_COLORS.yellow, "fill-opacity": "0.4", "mix-blend-mode": "multiply" },
  );
}

function eraseHighlight(cfi) {
  try { state.rendition?.annotations.remove(cfi, "highlight"); } catch { /* 이미 제거됨 */ }
}

async function persistNote(note) {
  try { await window.LibrumLibrary?.saveNote(note); } catch (error) {
    reportDiagnostic("note_save_failed", { error: describeError(error) }, "warning");
  }
}

function onTextSelected(cfiRange, contents) {
  const selection = contents.window.getSelection();
  if (!selection || !selection.rangeCount) return;
  const text = selection.toString().replace(/\s+/g, " ").trim();
  if (!text) return;
  notes.pending = { cfiRange, text };
  notes.active = null;
  ui.popupRemove.hidden = true;
  ui.highlightPopup.hidden = false;
  positionPopup(contents, selection.getRangeAt(0), false);
}

function onHighlightClicked(cfiRange, data, contents) {
  const note = notes.items.find((item) => item.id === data?.id || item.cfi === cfiRange);
  if (!note) return;
  notes.active = note;
  notes.pending = null;
  ui.popupRemove.hidden = false;
  const selection = contents.window.getSelection();
  const range = selection && selection.rangeCount ? selection.getRangeAt(0) : null;
  if (range && range.toString()) positionPopup(contents, range, false);
  else {
    // 클릭 지점 근처에 팝업 배치
    const frame = contents.document.defaultView.frameElement;
    const stageRect = ui.readingStage.getBoundingClientRect();
    if (frame) {
      const frameRect = frame.getBoundingClientRect();
      ui.highlightPopup.style.left = `${Math.min(Math.max(70, frameRect.width / 2), stageRect.width - 70)}px`;
      ui.highlightPopup.style.top = "60px";
      ui.highlightPopup.style.transform = "translate(-50%, 0)";
    }
  }
  ui.highlightPopup.hidden = false;
}

async function createHighlight(color) {
  if (!notes.pending || !state.libraryId) { hideHighlightPopup(); return; }
  const note = {
    id: newNoteId(),
    bookId: state.libraryId,
    cfi: notes.pending.cfiRange,
    text: notes.pending.text.slice(0, 600),
    color,
    memo: "",
    createdAt: Date.now(),
  };
  notes.items.push(note);
  drawHighlight(note);
  await persistNote(note);
  reportDiagnostic("highlight_added", { bookId: state.libraryId, color });
  clearBookSelection();
  hideHighlightPopup();
}

async function removeActiveHighlight() {
  const note = notes.active;
  if (!note) { hideHighlightPopup(); return; }
  eraseHighlight(note.cfi);
  notes.items = notes.items.filter((item) => item.id !== note.id);
  try { await window.LibrumLibrary?.removeNote(note.id); } catch { /* 무시 */ }
  hideHighlightPopup();
}

async function attachMemo() {
  let note = notes.active;
  // 선택 상태에서 곧바로 메모를 달면 형광펜(노랑)부터 만든다.
  if (!note && notes.pending && state.libraryId) {
    note = {
      id: newNoteId(),
      bookId: state.libraryId,
      cfi: notes.pending.cfiRange,
      text: notes.pending.text.slice(0, 600),
      color: "yellow",
      memo: "",
      createdAt: Date.now(),
    };
    notes.items.push(note);
    drawHighlight(note);
  }
  if (!note) { hideHighlightPopup(); return; }
  const memo = window.prompt("메모를 입력하세요", note.memo || "");
  if (memo !== null) {
    note.memo = memo.trim();
    await persistNote(note);
  }
  clearBookSelection();
  hideHighlightPopup();
}

function clearBookSelection() {
  try {
    (state.rendition?.getContents?.() || []).forEach((contents) => {
      contents.window?.getSelection?.()?.removeAllRanges?.();
    });
  } catch { /* 무시 */ }
}

async function restoreNotes() {
  notes.items = [];
  if (!window.LibrumLibrary || !state.libraryId) return;
  try {
    notes.items = await window.LibrumLibrary.listNotes(state.libraryId) || [];
    notes.items.forEach(drawHighlight);
  } catch (error) {
    reportDiagnostic("notes_restore_failed", { error: describeError(error) }, "warning");
  }
}

function renderNotesList() {
  const list = ui.notesList;
  if (!notes.items.length) {
    list.innerHTML = '<p class="notes-empty">아직 형광펜이나 메모가 없습니다. 본문에서 글자를 드래그해 표시해 보세요.</p>';
    return;
  }
  list.replaceChildren(...[...notes.items].reverse().map((note) => {
    const item = document.createElement("div");
    item.className = `note-item c-${note.color}`;
    const quote = document.createElement("p");
    quote.className = "quote";
    quote.textContent = note.text;
    item.append(quote);
    if (note.memo) {
      const memo = document.createElement("p");
      memo.className = "memo";
      memo.textContent = note.memo;
      item.append(memo);
    }
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "note-remove";
    remove.textContent = "×";
    remove.setAttribute("aria-label", "이 항목 삭제");
    remove.addEventListener("click", async (event) => {
      event.stopPropagation();
      eraseHighlight(note.cfi);
      notes.items = notes.items.filter((n) => n.id !== note.id);
      try { await window.LibrumLibrary?.removeNote(note.id); } catch { /* 무시 */ }
      renderNotesList();
    });
    item.append(remove);
    item.addEventListener("click", () => {
      setNotesViewVisible(false);
      state.rendition?.display(note.cfi);
    });
    return item;
  }));
}

function setNotesViewVisible(visible) {
  if (visible) {
    if (translation.active) exitTranslationMode();
    setLibraryViewVisible(false);
    hideHighlightPopup();
    renderNotesList();
  }
  ui.notesView.hidden = !visible;
  ui.notesToggle.classList.toggle("active", visible);
}

// 본문 안쪽을 탭하면 페이지를 넘긴다. 드래그로 글자를 선택 중이거나 링크를 누른
// 경우는 넘기지 않아, 형광펜(드래그)과 충돌하지 않는다.
let lastContentTapAt = 0;
let touchStart = null;
let suppressClick = false;
const SWIPE_MIN = 45; // 스와이프로 인정할 최소 가로 이동(px)

function onContentTouchStart(event) {
  suppressClick = false;
  if (event.touches.length !== 1) { touchStart = null; return; }
  const point = event.touches[0];
  touchStart = { x: point.clientX, y: point.clientY, at: event.timeStamp || Date.now() };
}

// 좌우로 밀면 페이지를 넘긴다. 왼쪽으로 밀기 = 다음, 오른쪽으로 밀기 = 이전.
function onContentTouchEnd(event, contents) {
  const start = touchStart;
  touchStart = null;
  if (!start || !event.changedTouches?.length) return;
  const point = event.changedTouches[0];
  const dx = point.clientX - start.x;
  const dy = point.clientY - start.y;
  const dt = (event.timeStamp || Date.now()) - start.at;
  const selection = contents.window?.getSelection?.();
  if (selection && selection.toString().trim()) return; // 글자 선택 중이면 무시
  if (Math.abs(dx) < SWIPE_MIN || Math.abs(dx) <= Math.abs(dy) * 1.3 || dt > 800) return;
  suppressClick = true; // 스와이프 직후 따라오는 click이 또 넘기지 않게
  const now = event.timeStamp || Date.now();
  if (now - lastContentTapAt < 500) return;
  lastContentTapAt = now;
  if (dx < 0) nextPage();
  else previousPage();
}

function onContentClick(event, contents) {
  if (suppressClick) { suppressClick = false; return; }
  // 드래그로 글자를 선택한 직후의 클릭이면 팝업을 유지하고 페이지도 넘기지 않는다.
  const selection = contents.window?.getSelection?.();
  if (selection && selection.toString().trim()) return;
  if (!ui.highlightPopup.hidden) { hideHighlightPopup(); return; }
  if (event.target?.closest?.("a")) return;
  // 한 번의 터치가 두 번의 페이지 이동으로 처리되는 것을 막는다(모바일 이벤트 중복 방지).
  const now = event.timeStamp || Date.now();
  if (now - lastContentTapAt < 500) return;
  const width = contents.window.innerWidth || contents.document.documentElement.clientWidth || 0;
  if (!width) return;
  const x = event.clientX;
  if (x < width * 0.3) { lastContentTapAt = now; previousPage(); }
  else if (x > width * 0.7) { lastContentTapAt = now; nextPage(); }
}

// 마우스 휠로 페이지 넘기기(페이지 모드에서만). 아래로=다음, 위로=이전.
let lastWheelAt = 0;
function onContentWheel(event) {
  if (comic.active) return; // 만화 뷰어는 자체 휠 핸들러를 쓴다
  if (state.flow !== "paginated") return; // 스크롤 모드에선 기본 스크롤을 그대로 둔다
  if (!ui.highlightPopup.hidden) return;
  if (Math.abs(event.deltaY) < 4) return;
  event.preventDefault();
  const now = event.timeStamp || Date.now();
  if (now - lastWheelAt < 350) return;
  lastWheelAt = now;
  if (event.deltaY > 0) nextPage();
  else previousPage();
}

// 본문 문서마다 탭/스와이프 리스너를 붙인다. 이미 붙은 문서는 건너뛴다.
function bindContentEvents(contents) {
  const doc = contents?.document;
  if (!doc || doc.__librumBound) return;
  doc.__librumBound = true;
  doc.addEventListener("click", (event) => onContentClick(event, contents));
  doc.addEventListener("touchstart", onContentTouchStart, { passive: true });
  doc.addEventListener("touchend", (event) => onContentTouchEnd(event, contents), { passive: true });
  doc.addEventListener("touchcancel", () => { touchStart = null; }, { passive: true });
  doc.addEventListener("wheel", onContentWheel, { passive: false });
}

function bindAllContents() {
  (state.rendition?.getContents?.() || []).forEach(bindContentEvents);
}

function setupSelectionHandlers() {
  state.rendition.on("selected", onTextSelected);
  state.rendition.on("markClicked", onHighlightClicked);
  state.rendition.on("relocated", hideHighlightPopup);
  // 각 섹션 로드 시, 그리고 페이지 이동 후에도 리스너가 붙어 있도록 이중으로 보장한다.
  state.rendition.hooks.content.register(bindContentEvents);
  state.rendition.on("rendered", bindAllContents);
  state.rendition.on("relocated", bindAllContents);
}

// 이미지가 여러 개 있는 섹션 등에서 칼럼 계산이 어긋나 next()/prev()가 제자리에
// 멈추는 epub.js 문제를 우회한다. 위치가 그대로면 인접 스파인 섹션으로 강제 이동해
// "이미지 페이지에서 페이지가 안 넘어가는" 증상을 없앤다.
async function recoverIfStuck(beforeCfi, direction) {
  const rendition = state.rendition;
  if (!rendition || !beforeCfi) return;
  const location = rendition.currentLocation()?.start;
  if (!location || location.cfi !== beforeCfi) return; // 정상적으로 이동함
  const target = state.book?.spine?.get(location.index + direction);
  if (target?.href) await rendition.display(target.href);
}

async function previousPage() {
  if (comic.active) { comicPrev(); return; }
  if (translation.active) exitTranslationMode();
  hideHighlightPopup();
  const rendition = state.rendition;
  if (!rendition) return;
  const before = rendition.currentLocation()?.start?.cfi;
  await rendition.prev();
  await recoverIfStuck(before, -1);
}

async function nextPage() {
  if (comic.active) { comicNext(); return; }
  if (translation.active) exitTranslationMode();
  hideHighlightPopup();
  const rendition = state.rendition;
  if (!rendition) return;
  const before = rendition.currentLocation()?.start?.cfi;
  await rendition.next();
  await recoverIfStuck(before, 1);
}

function hashText(text) {
  let hash = 5381;
  for (let index = 0; index < text.length; index += 1) {
    hash = ((hash << 5) + hash + text.charCodeAt(index)) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

async function extractChapterParagraphs(href) {
  const section = state.book.spine.get(href);
  if (!section) throw new Error("현재 챕터를 스파인에서 찾을 수 없습니다.");

  await section.load(state.book.load.bind(state.book));
  const paragraphs = [];
  const body = section.document?.body;
  if (body) {
    // Keep only leaf blocks so a blockquote and the paragraphs inside it are not both extracted.
    [...body.querySelectorAll(BLOCK_SELECTOR)]
      .filter((element) => !element.querySelector(BLOCK_SELECTOR))
      .forEach((element) => {
        const text = element.textContent.replace(/\s+/g, " ").trim();
        if (!text) return;
        paragraphs.push({
          id: `p-${String(paragraphs.length + 1).padStart(4, "0")}`,
          kind: /^h[1-6]$/i.test(element.tagName) ? "heading" : "text",
          textHash: hashText(text),
          text,
        });
      });
  }
  section.unload();
  return { spineIndex: section.index, paragraphs };
}

function setTranslationStatus(message) {
  ui.translationStatus.textContent = message;
  ui.translationStatus.title = message;
}

function setMobilePane(pane) {
  ui.translationView.dataset.mobilePane = pane;
  ui.tabSource.classList.toggle("active", pane === "source");
  ui.tabSource.setAttribute("aria-pressed", String(pane === "source"));
  ui.tabTranslation.classList.toggle("active", pane === "translation");
  ui.tabTranslation.setAttribute("aria-pressed", String(pane === "translation"));
}

function createParagraphElement(paragraph, text, missing = false) {
  const element = document.createElement("p");
  element.className = "para";
  if (paragraph.kind === "heading") element.classList.add("heading");
  if (missing) element.classList.add("missing");
  element.dataset.paraId = paragraph.id;
  element.textContent = text;
  return element;
}

function renderSourcePane() {
  ui.sourcePane.replaceChildren(
    ...translation.paragraphs.map((paragraph) => createParagraphElement(paragraph, paragraph.text)),
  );
}

function renderTranslationPlaceholder(resultFile) {
  const placeholder = document.createElement("div");
  placeholder.className = "translation-placeholder";
  placeholder.innerHTML = "번역이 아직 준비되지 않았습니다.<br />"
    + `<code></code> 파일이 만들어지면 자동으로 표시됩니다.`;
  placeholder.querySelector("code").textContent = resultFile;
  ui.translationPane.replaceChildren(placeholder);
}

function renderTranslationResult(result) {
  const translated = new Map(
    (result.paragraphs || []).map((paragraph) => [paragraph.id, paragraph.text]),
  );
  let matched = 0;
  ui.translationPane.replaceChildren(
    ...translation.paragraphs.map((paragraph) => {
      const text = translated.get(paragraph.id);
      if (typeof text === "string" && text.trim()) {
        matched += 1;
        return createParagraphElement(paragraph, text);
      }
      return createParagraphElement(paragraph, "(번역 없음)", true);
    }),
  );
  setTranslationStatus(`번역 완료 · ${matched}/${translation.paragraphs.length}개 문단`);
  reportDiagnostic("translation_result_rendered", {
    jobId: translation.jobId,
    matchedParagraphs: matched,
    totalParagraphs: translation.paragraphs.length,
  });
}

function highlightParagraph(paraId, originPane) {
  [ui.sourcePane, ui.translationPane].forEach((pane) => {
    pane.querySelectorAll(".para.highlight").forEach((element) => element.classList.remove("highlight"));
    if (!paraId) return;
    const match = pane.querySelector(`.para[data-para-id="${paraId}"]`);
    if (!match) return;
    match.classList.add("highlight");
    if (pane !== originPane) match.scrollIntoView({ block: "nearest", behavior: "smooth" });
  });
}

function bindPaneHover(pane) {
  pane.addEventListener("mouseover", (event) => {
    const target = event.target.closest?.(".para");
    if (target && pane.contains(target)) highlightParagraph(target.dataset.paraId, pane);
  });
  pane.addEventListener("mouseleave", () => highlightParagraph(null));
}

function stopResultPolling() {
  window.clearInterval(translation.pollTimer);
  translation.pollTimer = null;
}

async function fetchTranslationResult(jobId) {
  try {
    const response = await fetch(`/api/translations/results/${jobId}`);
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

function startResultPolling(jobId) {
  stopResultPolling();
  translation.pollTimer = window.setInterval(async () => {
    const result = await fetchTranslationResult(jobId);
    if (!result || !translation.active || translation.jobId !== jobId) return;
    stopResultPolling();
    renderTranslationResult(result);
  }, TRANSLATION_POLL_MS);
}

async function loadOrQueueTranslation(chapter) {
  const existing = await fetchTranslationResult(translation.jobId);
  if (existing) {
    renderTranslationResult(existing);
    return;
  }

  setTranslationStatus("번역 작업을 저장하는 중...");
  const job = {
    schemaVersion: 1,
    jobId: translation.jobId,
    createdAt: new Date().toISOString(),
    book: {
      title: ui.summaryTitle.textContent || "",
      creator: ui.author.textContent || "",
    },
    chapter,
    sourceLanguage: "auto",
    targetLanguage: "ko",
    paragraphs: translation.paragraphs.map(({ id, kind, textHash, text }) => ({ id, kind, textHash, text })),
  };
  const response = await fetch("/api/translations/jobs", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(job),
  });
  if (!response.ok) throw new Error(`번역 작업을 저장하지 못했습니다 (HTTP ${response.status})`);

  const saved = await response.json();
  renderTranslationPlaceholder(saved.resultFile);
  setTranslationStatus(`번역 대기 중 · ${saved.jobFile}`);
  reportDiagnostic("translation_job_queued", {
    jobId: translation.jobId,
    chapterHref: chapter.href,
    paragraphs: translation.paragraphs.length,
  });
  startResultPolling(translation.jobId);
}

function exitTranslationMode() {
  translation.active = false;
  translation.jobId = null;
  translation.chapterHref = null;
  translation.paragraphs = [];
  stopResultPolling();
  ui.translationView.hidden = true;
  ui.translateToggle.classList.remove("active");
  ui.translateToggle.setAttribute("aria-pressed", "false");
}

async function enterTranslationMode() {
  const href = state.rendition?.currentLocation()?.start?.href;
  if (!state.book || !href) {
    setSettingsOpen(false);
    reportDiagnostic("translation_mode_unavailable", { hasBook: Boolean(state.book) }, "warning");
    return;
  }

  setLibraryViewVisible(false);
  translation.active = true;
  translation.chapterHref = href;
  ui.translateToggle.classList.add("active");
  ui.translateToggle.setAttribute("aria-pressed", "true");
  ui.translationView.hidden = false;
  setMobilePane("source");
  ui.sourcePane.replaceChildren();
  ui.translationPane.replaceChildren();
  setTranslationStatus("챕터 문단을 추출하는 중...");

  try {
    const { spineIndex, paragraphs } = await extractChapterParagraphs(href);
    if (!translation.active || translation.chapterHref !== href) return;
    if (!paragraphs.length) {
      setTranslationStatus("이 챕터에서 번역할 문단을 찾지 못했습니다.");
      return;
    }

    translation.paragraphs = paragraphs;
    translation.jobId = [
      `t${hashText(ui.summaryTitle.textContent || "book")}`,
      `c${String(spineIndex).padStart(3, "0")}`,
      hashText(paragraphs.map((paragraph) => paragraph.text).join("\n")),
    ].join("-");
    renderSourcePane();
    await loadOrQueueTranslation({ href, spineIndex, label: nearestChapter(href) });
  } catch (error) {
    reportDiagnostic("translation_mode_failed", { chapterHref: href, error: describeError(error) }, "error");
    setTranslationStatus(error instanceof Error && error.message ? error.message : "번역 준비 중 문제가 발생했습니다.");
  }
}

function setSettingsOpen(isOpen) {
  ui.settings.hidden = !isOpen;
  ui.settingsToggle.setAttribute("aria-expanded", String(isOpen));
}

function changeFlow(flow) {
  state.flow = flow;
  localStorage.setItem("librum-flow", flow);
  ui.flowButtons.forEach((button) => button.classList.toggle("active", button.dataset.flow === flow));
  if (!state.rendition) return;
  const location = state.rendition.currentLocation()?.start?.cfi;
  state.rendition.flow(flow);
  state.rendition.display(location);
}

ui.file.addEventListener("change", (event) => {
  openFile(event.target.files[0]);
  event.target.value = ""; // 같은 파일을 다시 선택해도 change가 발생하도록 초기화
});
ui.previous.addEventListener("click", previousPage);
ui.next.addEventListener("click", nextPage);
ui.stagePrevious.addEventListener("click", previousPage);
ui.stageNext.addEventListener("click", nextPage);
ui.range.addEventListener("input", (event) => {
  if (comic.active) {
    const target = Math.round((Number(event.target.value) / 100) * comic.entries.length) - 1;
    showComicPage(Math.max(0, Math.min(comic.entries.length - 1, target)));
    return;
  }
  if (!state.locationsReady) return;
  if (translation.active) exitTranslationMode();
  const cfi = state.book.locations.cfiFromPercentage(Number(event.target.value) / 100);
  if (cfi) state.rendition.display(cfi);
});
ui.decreaseFont.addEventListener("click", () => updateFontSize(state.fontSize - 10));
ui.increaseFont.addEventListener("click", () => updateFontSize(state.fontSize + 10));
ui.resetFont.addEventListener("click", () => updateFontSize(100));
ui.fontSizeRange.addEventListener("input", (event) => updateFontSize(Number(event.target.value)));
ui.fontSelect.addEventListener("change", (event) => applyFont(event.target.value));
ui.lineHeightRange.addEventListener("input", (event) => updateLineHeight(event.target.value));
ui.theme.addEventListener("change", (event) => applyTheme(event.target.value));
ui.flowButtons.forEach((button) => button.addEventListener("click", () => changeFlow(button.dataset.flow)));
ui.settingsToggle.addEventListener("click", () => setSettingsOpen(ui.settings.hidden));
ui.translateToggle.addEventListener("click", () => (translation.active ? exitTranslationMode() : enterTranslationMode()));
ui.showLibrary.addEventListener("click", async () => {
  if (!ui.libraryView.hidden) {
    if (state.book || comic.active) setLibraryViewVisible(false);
    return;
  }
  await refreshLibraryGrid();
  setLibraryViewVisible(true);
});
ui.notesToggle.addEventListener("click", () => setNotesViewVisible(ui.notesView.hidden));
ui.notesClose.addEventListener("click", () => setNotesViewVisible(false));
ui.popupMemo.addEventListener("click", attachMemo);
ui.popupRemove.addEventListener("click", removeActiveHighlight);
ui.highlightPopup.querySelectorAll(".swatch").forEach((swatch) => {
  swatch.addEventListener("click", () => {
    if (notes.active) { // 기존 형광펜 색 변경
      const note = notes.active;
      eraseHighlight(note.cfi);
      note.color = swatch.dataset.color;
      drawHighlight(note);
      persistNote(note);
      hideHighlightPopup();
    } else {
      createHighlight(swatch.dataset.color);
    }
  });
});
ui.comicFit.addEventListener("click", () => setComicFit(comic.fit === "page" ? "width" : "page"));
ui.comicZoomIn.addEventListener("click", () => setComicZoom(comic.zoom + 0.25));
ui.comicZoomOut.addEventListener("click", () => setComicZoom(comic.zoom - 0.25));
ui.comicZoomValue.addEventListener("click", () => resetComicView());
ui.tabSource.addEventListener("click", () => setMobilePane("source"));
ui.tabTranslation.addEventListener("click", () => setMobilePane("translation"));
bindPaneHover(ui.sourcePane);
bindPaneHover(ui.translationPane);
// 본문 여백(iframe 바깥)에서 휠을 굴려도 페이지가 넘어가게 한다.
ui.bookFrame.addEventListener("wheel", onContentWheel, { passive: false });

document.addEventListener("click", (event) => {
  if (!ui.settings.hidden && !ui.settings.contains(event.target) && event.target !== ui.settingsToggle) {
    setSettingsOpen(false);
  }
  if (!ui.highlightPopup.hidden && !ui.highlightPopup.contains(event.target)) {
    hideHighlightPopup();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    setSettingsOpen(false);
    if (!ui.highlightPopup.hidden) hideHighlightPopup();
    else if (translation.active) exitTranslationMode();
    else if (!ui.notesView.hidden) setNotesViewVisible(false);
    else if (!ui.libraryView.hidden && (state.book || comic.active)) setLibraryViewVisible(false);
    return;
  }
  if ((!state.rendition && !comic.active) || event.altKey || event.ctrlKey || event.metaKey) return;
  if (translation.active || !ui.libraryView.hidden || !ui.notesView.hidden) return;
  if (event.key === "ArrowLeft") previousPage();
  if (event.key === "ArrowRight" || event.key === " ") {
    event.preventDefault();
    nextPage();
  }
});

if (window.ResizeObserver) {
  let frameResizeId;
  new ResizeObserver(() => {
    window.cancelAnimationFrame(frameResizeId);
    frameResizeId = window.requestAnimationFrame(() => {
      if (comic.active) { clampPan(); applyComicTransform(); return; }
      if (state.rendition) state.rendition.resize(ui.bookFrame.clientWidth, ui.bookFrame.clientHeight);
    });
  }).observe(ui.bookFrame);
}

window.addEventListener("error", (event) => {
  reportDiagnostic("window_error", {
    error: {
      name: "WindowError",
      message: event.message,
      source: event.filename,
      line: event.lineno,
      column: event.colno,
    },
  }, "error");
});

window.addEventListener("unhandledrejection", (event) => {
  reportDiagnostic("unhandled_rejection", { error: describeError(event.reason) }, "error");
});

updateFontSize(state.fontSize);
applyTheme(state.theme);
applyFont(state.font);
updateLineHeight(state.lineHeight);
changeFlow(state.flow);
initializeLibrary();
reportDiagnostic("reader_initialized", {
  epubEngineAvailable: Boolean(window.ePub),
  zipLibraryAvailable: Boolean(window.JSZip),
  libraryAvailable: Boolean(window.LibrumLibrary),
});
