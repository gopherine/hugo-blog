function toggleTheme() {
  var root = document.documentElement;
  var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  root.setAttribute('data-theme', next);
  try { localStorage.setItem('theme', next); } catch (e) {}
}

/*
 * Fuzzy match. Returns -1 for no match, else a score:
 *   >= 1000  contiguous substring (higher = earlier match)
 *   >= 100   ordered subsequence (so "goconc" hits "Go Concurrency Masterclass")
 */
function fuzzyScore(query, text) {
  query = (query || '').toLowerCase().trim();
  text = (text || '').toLowerCase();
  if (!query) return 0;
  var idx = text.indexOf(query);
  if (idx !== -1) return 1000 - idx;
  var qi = 0, score = 0, last = -2;
  for (var i = 0; i < text.length && qi < query.length; i++) {
    if (text[i] === query[qi]) {
      score += (i === last + 1) ? 4 : 1;
      last = i;
      qi++;
    }
  }
  return qi === query.length ? 100 + score : -1;
}

/* Writing page: filter series + lessons by title text. */
(function () {
  var input = document.getElementById('writing-search');
  if (!input) return;

  var groups = Array.prototype.slice.call(document.querySelectorAll('[data-section-group]'));

  function apply() {
    var q = input.value.trim().toLowerCase();

    groups.forEach(function (group) {
      var anyVisible = false;

      group.querySelectorAll('.series').forEach(function (series) {
        var visible = 0;
        series.querySelectorAll('.series-item').forEach(function (item) {
          var match = !q || item.textContent.toLowerCase().indexOf(q) !== -1;
          item.style.display = match ? '' : 'none';
          if (match) visible++;
        });
        series.style.display = visible ? '' : 'none';
        series.open = !!q && visible > 0;
        if (visible) anyVisible = true;
      });

      group.style.display = anyVisible ? '' : 'none';
    });
  }

  input.addEventListener('input', apply);
})();

/* Global search page: filter a generated JSON index by title/course/tags/summary. */
(function () {
  var input = document.getElementById('site-search');
  if (!input) return;

  var list = document.getElementById('search-results');
  var status = document.getElementById('search-status');
  var items = null;

  function esc(s) {
    return (s || '').replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function run() {
    if (!items) return;
    var q = input.value.trim().toLowerCase();
    if (!q) {
      list.innerHTML = '';
      status.textContent = '';
      return;
    }
    var terms = q.split(/\s+/);
    var scored = [];
    items.forEach(function (it) {
      var title = (it.t || '').toLowerCase();
      var series = (it.s || '').toLowerCase();
      var hay = (it.t + ' ' + it.s + ' ' + it.g + ' ' + it.c + ' ' + it.f).toLowerCase();
      var ok = true, score = 0;
      terms.forEach(function (t) {
        var ts = fuzzyScore(t, title);
        var ss = fuzzyScore(t, series);
        var hs = fuzzyScore(t, hay);
        if (ts < 0 && hs < 0) ok = false;
        if (ts >= 1000) score += 12; else if (ts >= 0) score += 6;
        if (ss >= 1000) score += 4; else if (ss >= 0) score += 2;
        if (hs >= 1000) score += 1;
      });
      if (!ok) return;
      scored.push([score, it]);
    });
    scored.sort(function (a, b) { return b[0] - a[0]; });
    var top = scored.slice(0, 40).map(function (x) { return x[1]; });
    status.textContent = scored.length
      ? scored.length + ' result' + (scored.length === 1 ? '' : 's')
      : 'No results for \u201c' + input.value.trim() + '\u201d';
    list.innerHTML = top.map(function (it) {
      return '<li class="post-row"><time class="post-date">' + esc(it.d) + '</time><div>' +
        '<a href="' + it.u + '"><h3 class="post-title">' + esc(it.t) + '</h3></a>' +
        (it.s ? '<div class="post-meta"><span class="tag">' + esc(it.s) + '</span></div>' : '') +
        '</div></li>';
    }).join('');
  }

  var timer;
  input.addEventListener('input', function () {
    clearTimeout(timer);
    timer = setTimeout(run, 80);
  });

  fetch(input.dataset.index)
    .then(function (r) { return r.json(); })
    .then(function (d) { items = d; run(); })
    .catch(function () { status.textContent = 'Search index failed to load.'; });

  var q = new URLSearchParams(location.search).get('q');
  if (q) { input.value = q; run(); }
})();

/* Courses page: fuzzy-filter the grouped course cards. */
(function () {
  var input = document.getElementById('course-search');
  if (!input) return;

  var sections = Array.prototype.slice.call(document.querySelectorAll('.course-section'));
  var status = document.getElementById('course-search-status');
  var jump = document.getElementById('courses-jump');

  sections.forEach(function (sec) {
    var el = sec.querySelector('.course-section-count');
    if (el) el.setAttribute('data-total', (el.textContent.match(/\d+/) || ['0'])[0]);
  });

  function run() {
    var q = input.value.trim().toLowerCase();
    var terms = q.split(/\s+/).filter(Boolean);
    var shown = 0;

    sections.forEach(function (sec) {
      var visible = 0;
      sec.querySelectorAll('.course-card').forEach(function (card) {
        var nameEl = card.querySelector('.course-card-name');
        var name = nameEl ? nameEl.textContent : card.textContent;
        var ok = !terms.length || terms.every(function (t) { return fuzzyScore(t, name) >= 0; });
        card.style.display = ok ? '' : 'none';
        if (ok) visible++;
      });
      sec.style.display = visible ? '' : 'none';

      var count = sec.querySelector('.course-section-count');
      if (count) {
        var total = count.getAttribute('data-total') || '0';
        var unit = total === '1' ? ' course' : ' courses';
        count.textContent = terms.length ? visible + ' of ' + total + unit : total + unit;
      }
      shown += visible;
    });

    if (jump) jump.style.display = terms.length ? 'none' : '';
    status.textContent = terms.length
      ? (shown ? shown + ' course' + (shown === 1 ? '' : 's') : 'No courses match \u201c' + input.value.trim() + '\u201d')
      : '';
  }

  var timer;
  input.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(run, 60); });
})();

/* Press "/" to focus the page's search field. */
document.addEventListener('keydown', function (e) {
  if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey) return;
  var tag = (document.activeElement && document.activeElement.tagName) || '';
  if (tag === 'INPUT' || tag === 'TEXTAREA') return;
  var input = document.getElementById('course-search') || document.getElementById('site-search') || document.getElementById('writing-search');
  if (input) { e.preventDefault(); input.focus(); }
});
