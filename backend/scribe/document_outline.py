"""Inline, offline annotation outline shared by standalone and in-app transcripts."""

CSS = r"""
/* Fixed, compact annotation outline. */
.annotation-outline {
  position: fixed; z-index: 30; top: 30px; right: max(20px, calc((100vw - 1220px) / 2));
  bottom: 92px; width: 244px; display: flex; flex-direction: column;
  background: #fff; border: 1px solid #e4e7eb; border-radius: 12px;
  box-shadow: 0 3px 16px rgba(15,23,42,.035); overflow: hidden; line-height: 1.5;
}
.annotation-outline[hidden], .outline-toggle[hidden] { display: none !important; }
.outline-head { display: flex; align-items: center; gap: 7px; padding: 16px 14px 11px; }
.outline-head h2 { flex: 1; margin: 0; font-size: 13px; font-weight: 650; color: var(--ink); letter-spacing: -.02em; }
.outline-total { color: var(--faint); font-size: 12px; font-weight: 400; margin-left: 5px; }
.outline-close { width: 25px; height: 25px; display: grid; place-items: center; padding: 0; border: 0; border-radius: 5px; color: var(--muted); background: transparent; cursor: pointer; }
.outline-close:hover { background: var(--soft); color: var(--ink); }
.outline-filters { display: flex; gap: 3px; padding: 0 12px 11px; border-bottom: 1px solid var(--line-2); }
.outline-filter { flex: 1; border: 0; border-radius: 5px; padding: 5px 4px; font: inherit; font-size: 11px; color: var(--muted); background: transparent; cursor: pointer; white-space: nowrap; }
.outline-filter span { opacity: .65; margin-left: 3px; font-size: 10px; }
.outline-filter[aria-pressed="true"] { background: #eef1f5; color: var(--ink); font-weight: 600; }
.outline-list { flex: 1; min-height: 0; overflow: auto; overscroll-behavior: contain; scrollbar-width: thin; scrollbar-color: #d9dde3 transparent; list-style: none; margin: 0; padding: 8px; }
.outline-list li { margin: 1px 0; padding: 0; }
.outline-list li[hidden] { display: none; }
.outline-list .jump { display: block; padding: 9px 10px; border: 0; border-left: 2px solid transparent; border-radius: 5px; color: #69717e; text-decoration: none; transition: background .12s, color .12s; }
.outline-list .jump:hover { background: #f4f5f7; color: var(--ink); }
.outline-meta { display: flex; align-items: center; gap: 7px; margin-bottom: 4px; }
.outline-list .tm { font: 10px/1.4 var(--mono); color: #9299a3; }
.outline-kind { font-size: 10px; line-height: 1.4; color: #7c8491; }
.outline-kind::before { content: ''; display: inline-block; width: 5px; height: 5px; margin-right: 4px; vertical-align: 1px; border-radius: 50%; background: #8b9bd0; }
.outline-kind.is-memo::before { background: #cba456; }
.outline-list .tx { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; font-size: 12px; line-height: 1.55; word-break: keep-all; }
.outline-list .jump[aria-current="location"] { color: #263d9b; background: #edf1fc; border-left-color: #586dc1; }
.outline-list .jump[aria-current="location"] .tm { color: #6e7eb1; }
.outline-foot { margin: 0; padding: 10px 14px; border-top: 1px solid var(--line-2); font-size: 10px; color: #9aa0aa; }
.outline-toggle { position: fixed; z-index: 31; top: 30px; right: 20px; display: flex; align-items: center; gap: 7px; padding: 9px 12px; border: 1px solid var(--line); border-radius: 8px; background: #fff; color: #4b5563; box-shadow: 0 3px 12px rgba(15,23,42,.06); cursor: pointer; font: inherit; font-size: 12px; font-weight: 500; line-height: 1.4; }
.outline-toggle svg { width: 15px; height: 15px; }
.annotation-outline button:focus-visible, .outline-toggle:focus-visible, .outline-list .jump:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.memo[data-ann], .cmt[data-ann] { scroll-margin-top: 28px; }
@media (min-width: 1100px) {
  body.has-outline { padding-right: 284px; }
  body.has-outline .paper { width: calc(100% - 64px); margin-top: 36px; }
  body.has-outline .player { left: calc((100% - 284px) / 2); }
  body.outline-collapsed { padding-right: 0; }
  body.outline-collapsed .player { left: 50%; }
}
@media (max-width: 1099px) {
  .annotation-outline { top: 16px; right: 12px; bottom: 96px; width: min(310px, calc(100vw - 24px)); box-shadow: 0 8px 40px rgba(15,23,42,.18); }
  .outline-toggle { top: auto; bottom: 80px; right: 16px; }
}
@media print {
  .annotation-outline, .outline-toggle { display: none !important; }
  body.has-outline { padding-right: 0; }
  body.has-outline .paper { width: auto; margin: 0; }
}

.in-app .annotation-outline { top: 72px; }
.in-app .memo[data-ann], .in-app .cmt[data-ann] { scroll-margin-top: 72px; }
.in-app .outline-toggle { top: 72px; }
@media (max-width: 1099px) { .in-app .outline-toggle { top: auto; } }
"""

JS = r"""
(function () {
  function buildOutline() {
    var main = document.querySelector('main');
    var sourceLinks = Array.prototype.slice.call(document.querySelectorAll('.index .jump'));
    if (!main || !sourceLinks.length) return;
    // Rebuild saved outline markup so exported HTML has working controls too.
    var previous = document.getElementById('annotation-outline'); if (previous) previous.remove();
    document.querySelectorAll('.outline-toggle').forEach(function (button) { button.remove(); });
    var aside = document.createElement('aside');
    aside.id = 'annotation-outline'; aside.className = 'annotation-outline';
    aside.setAttribute('aria-label', '메모·댓글 목차');
    aside.innerHTML = '<div class="outline-head"><h2>메모 · 댓글<span class="outline-total"></span></h2>'
      + '<button type="button" class="outline-close" aria-label="목차 접기" title="목차 접기">'
      + '<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="m6 4 4 4-4 4"/></svg></button></div>'
      + '<div class="outline-filters" aria-label="목차 종류">'
      + '<button type="button" class="outline-filter" data-kind="all" aria-pressed="true">전체<span></span></button>'
      + '<button type="button" class="outline-filter" data-kind="memo" aria-pressed="false">메모<span></span></button>'
      + '<button type="button" class="outline-filter" data-kind="comment" aria-pressed="false">댓글<span></span></button></div>'
      + '<ol class="outline-list" aria-label="시간순 메모·댓글"></ol>'
      + '<p class="outline-foot">클릭하면 해당 위치로 이동 · 녹음 재생</p>';
    var list = aside.querySelector('.outline-list');
    var entries = sourceLinks.map(function (source) {
      var note = document.getElementById(source.getAttribute('href').slice(1));
      if (!note) return null;
      var kind = note.classList.contains('memo') ? 'memo' : 'comment';
      var li = document.createElement('li'); li.dataset.kind = kind;
      var link = document.createElement('a'); link.className = 'jump'; link.href = source.getAttribute('href');
      var meta = document.createElement('span'); meta.className = 'outline-meta';
      var time = document.createElement('span'); time.className = 'tm'; time.textContent = source.querySelector('.tm').textContent;
      var tag = document.createElement('span'); tag.className = 'outline-kind is-' + kind; tag.textContent = kind === 'memo' ? '메모' : '댓글';
      var text = document.createElement('span'); text.className = 'tx';
      function refreshText() {
        var body = note.querySelector('.ann-body');
        text.textContent = (body ? body.textContent : source.querySelector('.tx').textContent).trim().replace(/\s+/g, ' ');
        link.title = time.textContent + ' · ' + tag.textContent + ' · ' + text.textContent;
      }
      refreshText(); meta.append(time, tag); link.append(meta, text); li.appendChild(link); list.appendChild(li);
      return { note: note, li: li, link: link, kind: kind, refreshText: refreshText };
    }).filter(Boolean);
    aside.querySelector('.outline-total').textContent = entries.length;
    aside.querySelectorAll('.outline-filter').forEach(function (button) {
      var kind = button.dataset.kind;
      button.querySelector('span').textContent = kind === 'all' ? entries.length : entries.filter(function (e) { return e.kind === kind; }).length;
      button.addEventListener('click', function () {
        aside.querySelectorAll('.outline-filter').forEach(function (b) { b.setAttribute('aria-pressed', String(b === button)); });
        entries.forEach(function (e) { e.li.hidden = kind !== 'all' && e.kind !== kind; });
        followCurrent();
      });
    });
    var toggle = document.createElement('button'); toggle.type = 'button'; toggle.className = 'outline-toggle';
    toggle.setAttribute('aria-controls', aside.id);
    toggle.innerHTML = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M5 4h8M5 8h8M5 12h8M2 4h.5M2 8h.5M2 12h.5"/></svg><span>메모 · 댓글 ' + entries.length + '</span>';
    document.body.append(aside, toggle); document.body.classList.add('has-outline');
    var narrow = window.matchMedia('(max-width: 1099px)');
    function setOpen(open, focus) {
      aside.hidden = !open; toggle.hidden = open;
      toggle.setAttribute('aria-expanded', String(open)); document.body.classList.toggle('outline-collapsed', !open);
      if (focus) { if (open) aside.querySelector('.outline-close').focus(); else toggle.focus(); }
      if (open) followCurrent();
    }
    aside.querySelector('.outline-close').addEventListener('click', function () { setOpen(false, true); });
    toggle.addEventListener('click', function () { setOpen(true, true); });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && !aside.hidden) setOpen(false, aside.contains(document.activeElement));
    });
    narrow.addEventListener('change', function () { setOpen(!narrow.matches, false); schedule(); });
    var current = null;
    function followCurrent() {
      if (!current || aside.hidden || current.li.hidden || list.matches(':hover')) return;
      var box = list.getBoundingClientRect(), row = current.li.getBoundingClientRect();
      if (row.top < box.top + 8 || row.bottom > box.bottom - 8)
        list.scrollTop += row.top - box.top - Math.max(8, (box.height - row.height) / 2);
    }
    function activate(entry) {
      if (current === entry) return;
      if (current) current.link.removeAttribute('aria-current');
      current = entry; if (entry) entry.link.setAttribute('aria-current', 'location');
      followCurrent();
    }
    function update() {
      queued = false;
      var at = entries[0], threshold = document.body.classList.contains('in-app') ? 76 : 32;
      for (var i = 0; i < entries.length; i++) {
        if (entries[i].note.getBoundingClientRect().top <= threshold) at = entries[i]; else break;
      }
      if (window.scrollY > 0 && window.scrollY + innerHeight >= document.documentElement.scrollHeight - 2)
        at = entries[entries.length - 1];
      activate(at);
    }
    var queued = false;
    function schedule() { if (!queued) { queued = true; requestAnimationFrame(update); } }
    window.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule, { passive: true });
    list.addEventListener('click', function (event) {
      var link = event.target.closest('.jump'); if (!link) return;
      var entry = entries.find(function (e) { return e.link === link; }); activate(entry);
      if (narrow.matches) setOpen(false, false);
    });
    // Keep the short titles in sync with edits to the document.
    var refreshQueued = false;
    var observer = new MutationObserver(function () {
      if (refreshQueued) return; refreshQueued = true;
      requestAnimationFrame(function () { refreshQueued = false; entries.forEach(function (e) { e.refreshText(); }); schedule(); });
    });
    entries.forEach(function (entry) { observer.observe(entry.note, { subtree: true, childList: true, characterData: true }); });
    setOpen(!narrow.matches, false); schedule();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', buildOutline, { once: true });
  else buildOutline();
})();
"""
