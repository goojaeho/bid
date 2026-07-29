"""공용 웹 레이아웃: SUIT 폰트 기반 디자인 시스템.

입찰공고(/)와 정부과제(/gov) 페이지가 같은 레이아웃을 공유한다.
템플릿 치환은 __TOKEN__ 방식을 사용한다 (CSS 중괄호 이스케이프 회피).
"""
from __future__ import annotations

NAV_ITEMS = [
    ("/", "입찰공고"),
    ("/gov", "정부과제"),
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

  header {
    background: var(--card); border-bottom: 1px solid var(--line);
    position: sticky; top: 0; z-index: 10;
  }
  .header-inner {
    max-width: 1140px; margin: 0 auto; padding: 0 20px;
    display: flex; align-items: center; gap: 28px; height: 56px;
  }
  .brand { font-size: 1.05rem; font-weight: 800; color: var(--text); letter-spacing: -0.01em; }
  .brand b { color: var(--accent); }
  nav { display: flex; gap: 4px; height: 100%; }
  nav a {
    display: flex; align-items: center; padding: 0 14px;
    color: var(--muted); font-weight: 600; font-size: 0.92rem;
    border-bottom: 2px solid transparent; margin-bottom: -1px;
  }
  nav a:hover { color: var(--text); text-decoration: none; }
  nav a.active { color: var(--accent); border-bottom-color: var(--accent); }

  main { max-width: 1140px; margin: 0 auto; padding: 24px 20px 48px; }
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
  thead th:first-child { border-top-left-radius: 12px; }
  thead th:last-child { border-top-right-radius: 12px; }
  th:hover { background: #f0f2f6; }
  th.asc::after { content: " ▲"; font-size: 0.7em; color: var(--accent); }
  th.desc::after { content: " ▼"; font-size: 0.7em; color: var(--accent); }
  tbody tr { border-bottom: 1px solid #f0f1f5; }
  tbody tr:last-child { border-bottom: none; }
  tbody tr:hover { background: #f7f9ff; }
  td a { color: var(--text); font-weight: 600; }
  td a:hover { color: var(--accent); }
  td.num { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
  td.date { white-space: nowrap; color: var(--muted); font-size: 0.84rem; }

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

  footer {
    max-width: 1140px; margin: 0 auto; padding: 0 20px 32px;
    color: var(--muted); font-size: 0.78rem;
  }
  @media (max-width: 640px) {
    .header-inner { gap: 14px; }
    main { padding: 16px 12px 40px; }
    .card { padding: 12px; }
  }
</style>
</head>
<body>
<header>
  <div class="header-inner">
    <span class="brand">One<b>AI</b>Gen</span>
    <nav>__NAV__</nav>
  </div>
</header>
<main>
<h1>__HEADING__</h1>
__CONTENT__
</main>
<footer>출처: 나라장터 · 기업마당 · K-Startup · NIPA · KOCCA · DIP · 대구/경북/부산TP — 실시간 조회 결과이며 원문 공고를 반드시 확인하세요.</footer>
<script>
document.querySelectorAll("table th").forEach(function (th, idx) {
  th.title = "클릭하면 이 기준으로 정렬됩니다";
  th.addEventListener("click", function () {
    var table = th.closest("table");
    var tbody = table.querySelector("tbody");
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


def layout(title: str, heading: str, active_path: str, content: str) -> str:
    parts = []
    for path, label in NAV_ITEMS:
        cls = ' class="active"' if path == active_path else ""
        parts.append(f'<a href="{path}"{cls}>{label}</a>')
    nav = "".join(parts)
    return (
        BASE.replace("__TITLE__", title)
        .replace("__NAV__", nav)
        .replace("__HEADING__", heading)
        .replace("__CONTENT__", content)
    )
