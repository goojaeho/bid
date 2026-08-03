"""공용 웹 레이아웃: SUIT 폰트 기반 디자인 시스템.

입찰공고(/), 정부과제(/gov), 즐겨찾기(/favs)가 같은 레이아웃을 공유한다.
템플릿 치환은 __TOKEN__ 방식을 사용한다 (CSS/JS 중괄호 이스케이프 회피).
"""
from __future__ import annotations

import os

# Lucide 아이콘 (https://lucide.dev, ISC 라이선스) — 인라인 SVG
ICONS = {
    "house": '<path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8" /><path d="M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />',
    "list-checks": '<path d="m3 17 2 2 4-4" /><path d="m3 7 2 2 4-4" /><path d="M13 6h8" /><path d="M13 12h8" /><path d="M13 18h8" />',
    "gavel": '<path d="m14.5 12.5-8 8a2.119 2.119 0 1 1-3-3l8-8" /><path d="m16 16 6-6" /><path d="m8 8 6-6" /><path d="m9 7 8 8" /><path d="m21 11-8-8" />',
    "landmark": '<path d="M10 18v-7" /><path d="M11.12 2.198a2 2 0 0 1 1.76.006l7.866 3.847c.476.233.31.949-.22.949H3.474c-.53 0-.695-.716-.22-.949z" /><path d="M14 18v-7" /><path d="M18 18v-7" /><path d="M3 22h18" /><path d="M6 18v-7" />',
    "star": '<path d="M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z" />',
    "bell": '<path d="M10.268 21a2 2 0 0 0 3.464 0" /><path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326" />',
    "briefcase": '<path d="M16 20V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16" /><rect width="20" height="14" x="2" y="6" rx="2" />',
    "leaf": '<path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10Z" /><path d="M2 21c0-3 1.85-5.36 5.08-6C9.5 14.52 12 13 13 12" />',
    "calendar": '<path d="M8 2v4" /><path d="M16 2v4" /><rect width="18" height="18" x="3" y="4" rx="2" /><path d="M3 10h18" />',
    "pencil": '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z" /><path d="m15 5 4 4" />',
    "x": '<path d="M18 6 6 18" /><path d="m6 6 12 12" />',
    "plus": '<path d="M5 12h14" /><path d="M12 5v14" />',
    "corner-down-right": '<path d="m15 10 5 5-5 5" /><path d="M4 4v7a4 4 0 0 0 4 4h12" />',
    "log-out": '<path d="m16 17 5-5-5-5" /><path d="M21 12H9" /><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />',
}


def icon(name: str, size: int = 16) -> str:
    inner = ICONS.get(name, "")
    return (f'<svg class="ic" width="{size}" height="{size}" viewBox="0 0 24 24" '
            f'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true">{inner}</svg>')


PERSONAL_NAV = [("/", "메인", "house"), ("/todo", "할 일", "list-checks")]
PUBLIC_NAV = [
    ("/bid", "입찰공고", "gavel"),
    ("/gov", "정부과제", "landmark"),
    ("/favs", "즐겨찾기", "star"),
]

BASE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/sun-typeface/SUIT@2/fonts/static/woff2/SUIT.css">
<style>
  :root {
    color-scheme: light;
    --accent: #3557f0;
    --accent-dark: #2645cc;
    --bg: #f5f6f8;
    --card: #ffffff;
    --line: #e7e9ee;
    --text: #1c2333;
    --muted: #7a8194;
  }
  * { box-sizing: border-box; }
  body {
    font-family: 'SUIT', 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif;
    background: var(--bg); color: var(--text);
    margin: 0; font-size: 15px; line-height: 1.5;
  }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }

  .shell { display: flex; min-height: 100vh; }
  .sidebar {
    width: 200px; flex-shrink: 0; background: var(--card);
    border-right: 1px solid var(--line); padding: 20px 12px;
    display: flex; flex-direction: column; gap: 22px;
    position: sticky; top: 0; height: 100vh;
  }
  .brand { font-size: 1.05rem; font-weight: 800; color: var(--text);
           letter-spacing: -0.01em; padding: 0 10px; }
  .brand b { color: var(--accent); }
  nav { display: flex; flex-direction: column; gap: 2px; }
  nav a {
    display: flex; align-items: center; gap: 9px;
    padding: 9px 12px; border-radius: 8px;
    color: var(--muted); font-weight: 600; font-size: 0.92rem;
  }
  .ic { vertical-align: -2px; flex-shrink: 0; }
  h1 .ic { vertical-align: -3px; margin-right: 4px; }
  nav a:hover { color: var(--text); background: #f4f5f8; text-decoration: none; }
  nav a.active { color: var(--accent); background: #eef2ff; }
  .side-foot {
    margin-top: auto; padding: 0 10px; color: var(--muted);
    font-size: 0.76rem; line-height: 1.8; word-break: break-all;
  }
  .side-foot a { color: var(--muted); }
  .content { flex: 1; min-width: 0; }

  main { max-width: 1140px; margin: 0 auto; padding: 24px 24px 48px; }
  h1 { font-size: 1.25rem; font-weight: 800; letter-spacing: -0.01em; margin: 0 0 16px; }

  .card {
    background: var(--card); border: 1px solid var(--line);
    border-radius: 12px; padding: 16px; margin-bottom: 16px;
  }
  .row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
  .row + .row { margin-top: 10px; }
  input, select, button {
    font: inherit; font-size: 0.92rem;
    border: 1px solid #d5d9e2; border-radius: 8px;
    padding: 9px 12px; background: #fff; color: var(--text);
  }
  select { min-width: 120px; }
  input:focus, select:focus {
    outline: none; border-color: var(--accent);
    box-shadow: 0 0 0 3px rgba(53, 87, 240, 0.12);
  }
  input[type=text] { flex: 1; min-width: 200px; }
  input.org { flex: 0 1 220px; min-width: 160px; }
  input.amt { width: 140px; }
  button {
    background: var(--accent); color: #fff; border-color: var(--accent);
    font-weight: 700; padding: 9px 22px; cursor: pointer;
  }
  button:hover { background: var(--accent-dark); }
  .tilde { color: var(--muted); }
  .quick-label { color: var(--muted); font-size: 0.82rem; font-weight: 700; }
  .chip {
    display: inline-block; padding: 7px 14px; border-radius: 999px;
    background: #eef2ff; color: var(--accent); font-weight: 700;
    font-size: 0.85rem; border: 1px solid #dbe3ff;
  }
  .chip:hover { background: #e2e9ff; text-decoration: none; }

  .meta { color: var(--muted); font-size: 0.85rem; margin: 4px 2px 10px; }
  .error {
    color: #b42318; background: #fef3f2; border: 1px solid #fecdca;
    border-radius: 10px; padding: 12px 14px; margin-bottom: 12px; font-size: 0.9rem;
  }

  .table-wrap {
    background: var(--card); border: 1px solid var(--line);
    border-radius: 12px; overflow-x: auto;
  }
  table { border-collapse: collapse; width: 100%; font-size: 0.88rem; }
  th, td { padding: 10px 12px; text-align: left; vertical-align: top; }
  thead th {
    color: var(--muted); font-size: 0.78rem; font-weight: 700;
    border-bottom: 1px solid var(--line); white-space: nowrap;
    cursor: pointer; user-select: none; background: #fafbfc;
  }
  th:hover { background: #f0f2f6; }
  th.asc::after { content: " ▲"; font-size: 0.7em; color: var(--accent); }
  th.desc::after { content: " ▼"; font-size: 0.7em; color: var(--accent); }
  tbody tr { border-bottom: 1px solid #f0f1f5; }
  tbody tr:last-child { border-bottom: none; }
  tbody tr.xrow { cursor: pointer; }
  tbody tr.xrow:hover { background: #f7f9ff; }
  td a { color: var(--text); font-weight: 600; }
  td a:hover { color: var(--accent); }
  td.num { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
  td.date { white-space: nowrap; color: var(--muted); font-size: 0.84rem; }
  td.nowrap { white-space: nowrap; }
  td.title-cell { min-width: 280px; }

  .cat {
    display: inline-block; padding: 2px 9px; border-radius: 999px;
    font-size: 0.74rem; font-weight: 700; white-space: nowrap;
  }
  .cat-물품 { background: #e6f6f4; color: #0e7569; }
  .cat-용역 { background: #e9eeff; color: #2645cc; }
  .cat-공사 { background: #fff1e3; color: #b45309; }
  .cat-외자 { background: #f3e8ff; color: #7e22ce; }
  .cat-default { background: #eef0f4; color: #4b5265; }
  .src-기업마당 { background: #e9eeff; color: #2645cc; }
  .src-K-Startup { background: #e6f6ec; color: #15803d; }
  .src-NIPA { background: #f3e8ff; color: #7e22ce; }
  .src-KOCCA { background: #fce7f3; color: #be185d; }
  .src-DIP { background: #e6f6f4; color: #0e7569; }
  .src-대구TP { background: #fff1e3; color: #b45309; }
  .src-경북TP { background: #eef7d9; color: #4d7c0f; }
  .src-부산TP { background: #e0f2fe; color: #0369a1; }

  .dd {
    display: inline-block; margin-left: 6px; padding: 1px 7px;
    border-radius: 999px; font-size: 0.72rem; font-weight: 700;
  }
  .dd.hot { background: #fee4e2; color: #b42318; }
  .dd.warn { background: #fef0c7; color: #b54708; }
  .dd.cool { background: #eef0f4; color: #4b5265; }
  .dd.past { background: #f2f4f7; color: #98a2b3; }

  tr.detail-row > td {
    background: #fafbff; border-top: 1px dashed var(--line);
    padding: 16px 18px; cursor: default;
  }
  .panel-grid {
    display: grid; grid-template-columns: 90px 1fr; gap: 6px 14px;
    font-size: 0.87rem; margin-bottom: 12px;
  }
  .panel-grid dt { color: var(--muted); font-weight: 700; }
  .panel-grid dd { margin: 0; white-space: pre-wrap; word-break: break-word; }
  .panel-btns { display: flex; flex-wrap: wrap; gap: 8px; }
  .panel-btns a, .panel-btns button {
    font-size: 0.84rem; padding: 7px 14px; border-radius: 8px;
    font-weight: 700; cursor: pointer; text-decoration: none;
  }
  .btn-outline {
    background: #fff; color: var(--accent); border: 1px solid var(--accent);
  }
  .btn-outline:hover { background: #eef2ff; text-decoration: none; }
  .btn-fav-on { background: #fef0c7; color: #b54708; border: 1px solid #fcd34d; }
  .ai-sum {
    margin-top: 12px; padding: 12px 14px; background: #f0fdf4;
    border: 1px solid #bbf7d0; border-radius: 10px;
    font-size: 0.87rem; white-space: pre-wrap;
  }
  .ai-sum.loading { background: #f5f6f8; border-color: var(--line); color: var(--muted); }
  .row-fav {
    background: none; border: none; font-size: 1.15rem; cursor: pointer;
    padding: 0 6px; color: #c8cdd8; line-height: 1;
  }
  .row-fav:hover { color: #f59e0b; background: none; }
  .row-fav.on { color: #f59e0b; }

  footer {
    max-width: 1140px; margin: 0 auto; padding: 0 20px 32px;
    color: var(--muted); font-size: 0.78rem;
  }
  .stat-row { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }
  .stat-tile {
    flex: 1; min-width: 130px; background: var(--card);
    border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px;
  }
  .stat-tile .num { font-size: 1.6rem; font-weight: 800; letter-spacing: -0.02em; }
  .stat-tile .lbl { color: var(--muted); font-size: 0.8rem; font-weight: 600; }
  .bars { display: flex; align-items: flex-end; gap: 2px; height: 120px; margin-top: 8px; }
  .bar-col { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 4px; min-width: 0; }
  .bar {
    width: 100%; max-width: 26px; background: var(--accent);
    border-radius: 4px 4px 0 0; min-height: 2px;
  }
  .bar.zero { background: #e7e9ee; }
  .bar.today { background: var(--accent-dark); }
  .bar-lbl { color: var(--muted); font-size: 0.62rem; white-space: nowrap; }
  .todo-list { list-style: none; margin: 0; padding: 0; }
  .todo-list li {
    display: flex; align-items: center; gap: 10px;
    padding: 9px 4px; border-bottom: 1px solid #f0f1f5;
  }
  .todo-list li:last-child { border-bottom: none; }
  .todo-list .tt { flex: 1; word-break: break-word; }
  .todo-list .done-at { color: var(--muted); font-size: 0.76rem; white-space: nowrap; }
  .todo-list .tdone { color: var(--muted); text-decoration: line-through; }
  .todo-del {
    background: none; border: none; color: #c8cdd8; cursor: pointer;
    font-size: 0.9rem; padding: 2px 6px;
  }
  .todo-del:hover { color: #b42318; background: none; }
  .todo-check {
    width: 18px; height: 18px; cursor: pointer; accent-color: var(--accent);
    flex-shrink: 0;
  }

  .seg { display: inline-flex; background: #eef0f4; border-radius: 10px; padding: 3px; gap: 2px; }
  .seg a {
    padding: 6px 14px; border-radius: 8px; font-size: 0.86rem; font-weight: 700;
    color: var(--muted);
  }
  .seg a:hover { color: var(--text); text-decoration: none; }
  .seg a.on { background: #fff; color: var(--text); box-shadow: 0 1px 2px rgba(0,0,0,0.08); }
  .pri { display: inline-block; width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .pri-1 { background: #d92d20; }
  .pri-2 { background: #f79009; }
  .pri-3 { background: #c8cdd8; }
  .area-chip {
    display: inline-block; padding: 1px 8px; border-radius: 999px;
    font-size: 0.72rem; font-weight: 700; white-space: nowrap;
  }
  .area-work { background: #e9eeff; color: #2645cc; }
  .area-personal { background: #e0f5f1; color: #0a7264; }
  .todo-act {
    background: #fff; border: 1px solid var(--line); border-radius: 7px;
    color: var(--muted); font-size: 0.74rem; padding: 3px 9px; cursor: pointer;
    font-weight: 600;
  }
  .todo-act:hover { background: #f4f5f8; color: var(--text); }
  .due-group { color: var(--muted); font-size: 0.8rem; font-weight: 700;
               margin: 14px 4px 4px; }
  .todo-list li.sub-row { padding-left: 26px; background: #fafbfd; }
  .sub-mark { color: #c8cdd8; display: flex; align-items: center; }

  .legend { display: flex; gap: 16px; align-items: center; font-size: 0.78rem;
            color: var(--muted); margin-top: 6px; }
  .legend .dot { display: inline-block; width: 9px; height: 9px; border-radius: 2px;
                 margin-right: 5px; }
  .bar-seg { width: 100%; max-width: 26px; min-height: 0; }
  .bar-seg.work { background: #3557f0; border-radius: 0; }
  .bar-seg.personal { background: #0e9384; border-radius: 4px 4px 0 0; }
  .bar-seg.first { border-radius: 4px 4px 0 0; }
  .bar-gap { height: 2px; width: 100%; }

  @media (max-width: 720px) {
    .shell { flex-direction: column; }
    .sidebar {
      width: auto; height: auto; position: static;
      flex-direction: row; align-items: center; gap: 12px;
      padding: 10px 12px; overflow-x: auto; border-right: none;
      border-bottom: 1px solid var(--line);
    }
    nav { flex-direction: row; }
    nav a { padding: 7px 10px; white-space: nowrap; }
    .side-foot { margin-top: 0; margin-left: auto; white-space: nowrap; line-height: 1.3; }
    main { padding: 16px 12px 40px; }
    .card { padding: 12px; }
  }
</style>
</head>
<body data-ai="__AI__" data-sf="__SF__">
<div class="shell">
<aside class="sidebar">
  <span class="brand">One<b>AI</b>Gen</span>
  <nav>__NAV__</nav>
  <div class="side-foot">__USER__<a href="/kakao">__BELL__ 카카오 알림</a></div>
</aside>
<div class="content">
<main>
<h1>__HEADING__</h1>
__CONTENT__
</main>
<footer>출처: 나라장터 · 기업마당 · K-Startup · NIPA · KOCCA · DIP · 대구/경북/부산TP — 실시간 조회 결과이며 원문 공고를 반드시 확인하세요.</footer>
</div>
</div>
<script>
var AI_ON = document.body.dataset.ai === "1";
var SERVER_FAVS = document.body.dataset.sf === "1";
var FAV_KEY = "oag_favs";
var FAVS_MAP = null;

function localFavs() {
  try { return JSON.parse(localStorage.getItem(FAV_KEY) || "{}"); }
  catch (e) { return {}; }
}
function favs() { return FAVS_MAP !== null ? FAVS_MAP : localFavs(); }
function saveFavs(map) {
  FAVS_MAP = map;
  if (SERVER_FAVS) {
    fetch("/api/favs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(map),
    }).catch(function () {});
  } else {
    localStorage.setItem(FAV_KEY, JSON.stringify(map));
  }
}
function escHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}
function calUrl(it) {
  if (!it.e) return null;
  var d = it.e.replace(/-/g, "");
  var nd = new Date(it.e); nd.setDate(nd.getDate() + 1);
  var d2 = nd.toISOString().slice(0, 10).replace(/-/g, "");
  return "https://calendar.google.com/calendar/render?action=TEMPLATE"
    + "&text=" + encodeURIComponent("[마감] " + it.t)
    + "&dates=" + d + "/" + d2
    + "&details=" + encodeURIComponent((it.s || "") + " 공고 마감일\\n" + (it.u || ""));
}
function panelHtml(it) {
  var rows = [];
  function add(k, v) { if (v) rows.push("<dt>" + k + "</dt><dd>" + escHtml(v) + "</dd>"); }
  add("기관", it.o); add("공고기관", it.on); add("지역", it.r);
  add("접수기간", (it.b || "") + (it.e ? " ~ " + it.e : "")); add("상태", it.st);
  add("추정가격", it.amt); add("지원대상", it.tg); add("신청방법", it.mt);
  add("개요", it.sm); add("문의처", it.ct);
  var isFav = !!favs()[it.u];
  var btns = [];
  if (it.u) btns.push('<a class="btn-outline" href="' + escHtml(it.u) + '" target="_blank">원문 공고 보기 ↗</a>');
  var cal = calUrl(it);
  if (cal) btns.push('<a class="btn-outline" href="' + escHtml(cal) + '" target="_blank">📅 캘린더에 마감일 추가</a>');
  btns.push('<button type="button" class="btn-outline fav-btn' + (isFav ? " btn-fav-on" : "") + '">'
    + (isFav ? "★ 즐겨찾기 해제" : "☆ 즐겨찾기") + "</button>");
  if (AI_ON && it.ai && it.u) btns.push('<button type="button" class="btn-outline ai-btn">🤖 AI 요약</button>');
  return '<dl class="panel-grid">' + rows.join("") + '</dl><div class="panel-btns">' + btns.join("") + "</div>"
    + '<div class="ai-slot"></div>';
}
function closePanels(tbody) {
  tbody.querySelectorAll("tr.detail-row").forEach(function (r) { r.remove(); });
}
function refreshRowFavs() {
  var map = favs();
  document.querySelectorAll("tr.xrow").forEach(function (tr) {
    var btn = tr.querySelector(".row-fav");
    if (!btn) return;
    var it = JSON.parse(tr.dataset.item);
    var on = !!map[it.u];
    btn.textContent = on ? "★" : "☆";
    btn.classList.toggle("on", on);
  });
}
document.addEventListener("click", function (e) {
  var btn = e.target.closest(".row-fav");
  if (!btn) return;
  e.stopPropagation();
  var tr = btn.closest("tr.xrow");
  if (!tr) return;
  var it = JSON.parse(tr.dataset.item);
  var map = favs();
  if (map[it.u]) delete map[it.u]; else map[it.u] = it;
  saveFavs(map);
  refreshRowFavs();
  if (window.renderFavs) window.renderFavs();
});
refreshRowFavs();
if (SERVER_FAVS) {
  fetch("/api/favs").then(function (r) { return r.json(); }).then(function (d) {
    if (!d.ok) return;
    FAVS_MAP = d.favs || {};
    var local = localFavs();
    if (!Object.keys(FAVS_MAP).length && Object.keys(local).length) {
      FAVS_MAP = local;
      saveFavs(FAVS_MAP);  // 기존 브라우저 저장분을 계정으로 자동 이전
    }
    refreshRowFavs();
    if (window.renderFavs) window.renderFavs();
  }).catch(function () {});
}
function bindPanel(detailTd, it) {
  var favBtn = detailTd.querySelector(".fav-btn");
  if (favBtn) favBtn.addEventListener("click", function () {
    var map = favs();
    if (map[it.u]) { delete map[it.u]; favBtn.textContent = "☆ 즐겨찾기"; favBtn.classList.remove("btn-fav-on"); }
    else { map[it.u] = it; favBtn.textContent = "★ 즐겨찾기 해제"; favBtn.classList.add("btn-fav-on"); }
    saveFavs(map);
    refreshRowFavs();
    if (window.renderFavs) window.renderFavs();
  });
  var aiBtn = detailTd.querySelector(".ai-btn");
  if (aiBtn) aiBtn.addEventListener("click", function () {
    var slot = detailTd.querySelector(".ai-slot");
    slot.innerHTML = '<div class="ai-sum loading">AI가 공고문을 읽고 있습니다… (첨부파일 포함, 10~20초)</div>';
    aiBtn.disabled = true;
    fetch("/summarize?u=" + encodeURIComponent(it.u))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        slot.innerHTML = d.ok
          ? '<div class="ai-sum">' + escHtml(d.summary) + "</div>"
          : '<div class="ai-sum loading">요약 실패: ' + escHtml(d.error) + "</div>";
      })
      .catch(function (e) { slot.innerHTML = '<div class="ai-sum loading">오류: ' + escHtml(e) + "</div>"; })
      .finally(function () { aiBtn.disabled = false; });
  });
}
document.addEventListener("click", function (e) {
  var tr = e.target.closest("tr.xrow");
  if (!tr || e.target.closest("a, button")) return;
  var tbody = tr.closest("tbody");
  var next = tr.nextElementSibling;
  var wasOpen = next && next.classList.contains("detail-row");
  closePanels(tbody);
  if (wasOpen) return;
  var it = JSON.parse(tr.dataset.item);
  var dr = document.createElement("tr");
  dr.className = "detail-row";
  var td = document.createElement("td");
  td.colSpan = tr.cells.length;
  td.innerHTML = panelHtml(it);
  dr.appendChild(td);
  tr.after(dr);
  bindPanel(td, it);
});
document.querySelectorAll("table th").forEach(function (th, idx) {
  th.title = "클릭하면 이 기준으로 정렬됩니다";
  th.addEventListener("click", function () {
    var table = th.closest("table");
    var tbody = table.querySelector("tbody");
    closePanels(tbody);
    var asc = !th.classList.contains("asc");
    table.querySelectorAll("th").forEach(function (t) {
      t.classList.remove("asc", "desc");
    });
    th.classList.add(asc ? "asc" : "desc");
    var rows = Array.prototype.slice.call(tbody.rows);
    rows.sort(function (a, b) {
      var ca = a.cells[idx], cb = b.cells[idx];
      var na = ca.getAttribute("data-v"), nb = cb.getAttribute("data-v");
      var r;
      if (na !== null && nb !== null) {
        r = parseFloat(na) - parseFloat(nb);
      } else {
        r = ca.textContent.trim().localeCompare(cb.textContent.trim(), "ko");
      }
      return asc ? r : -r;
    });
    rows.forEach(function (r) { tbody.appendChild(r); });
  });
});
</script>
</body>
</html>"""


def layout(title: str, heading: str, active_path: str, content: str,
           user: str | None = None, admin: bool = False) -> str:
    from app import store
    items = (PERSONAL_NAV if admin else []) + PUBLIC_NAV
    parts = []
    for path, label, icon_name in items:
        cls = ' class="active"' if path == active_path else ""
        parts.append(f'<a href="{path}"{cls}>{icon(icon_name)}<span>{label}</span></a>')
    nav = "".join(parts)
    userbox = ""
    if user:
        userbox = (f'<span>{user}</span><br>'
                   f'<a href="/logout">{icon("log-out", 12)} 로그아웃</a><br>')
    ai = "1" if os.environ.get("GEMINI_API_KEY", "").strip() else "0"
    sf = "1" if (user and store.enabled()) else "0"
    return (
        BASE.replace("__TITLE__", title)
        .replace("__NAV__", nav)
        .replace("__HEADING__", heading)
        .replace("__CONTENT__", content)
        .replace("__USER__", userbox)
        .replace("__BELL__", icon("bell", 12))
        .replace("__AI__", ai)
        .replace("__SF__", sf)
    )
