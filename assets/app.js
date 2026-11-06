/* 申论素材站 —— 前端逻辑
 * 数据源：index.json（唯一真相源）
 * 四个 tab：今日素材 / 规范词库 / 考点提纲 / 全部索引
 */

const S = {
  data: null,        // index.json 全文
  articles: [],
  terms: [],
  filtered: [],
  selected: null,    // 当前选中的文章 id
  tab: 'material',
  filters: { q: '', source: '', column: '', date: '' },
};

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function highlight(text, q) {
  if (!q) return esc(text);
  const t = esc(text);
  const safe = q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return t.replace(new RegExp(safe, 'gi'), (m) => `<mark style="background:#ffe9a8">${m}</mark>`);
}

// ─────────────────────────── 加载 ───────────────────────────

async function load() {
  try {
    const res = await fetch('index.json?t=' + Date.now());
    if (!res.ok) throw new Error('HTTP ' + res.status);
    S.data = await res.json();
  } catch (e) {
    $('#articles').innerHTML = `<div class="empty">加载 index.json 失败：${esc(e.message)}<br>请确认已运行 build_index.py</div>`;
    return;
  }
  S.articles = S.data.articles || [];
  S.terms = S.data.terms || [];
  buildFilters();
  applyFilters();
  renderTerms();
  renderOutlines();
  renderAll();
  renderStats();
}

// ─────────────────────────── 筛选 ───────────────────────────

function buildFilters() {
  const sources = [...new Set(S.articles.map(a => a.source))].filter(Boolean).sort();
  const columns = [...new Set(S.articles.map(a => a.column))].filter(Boolean).sort();
  const dates = [...new Set(S.articles.map(a => a.fetch_date || a.date))].filter(Boolean).sort().reverse();

  const opt = (v, label, cur) =>
    `<option value="${esc(v)}"${v === cur ? ' selected' : ''}>${esc(label)}</option>`;

  $('#f-source').innerHTML = opt('', '全部', '') + sources.map(s => opt(s, s, '')).join('');
  $('#f-column').innerHTML = opt('', '全部', '') + columns.map(c => opt(c, c, '')).join('');
  $('#f-date').innerHTML = opt('', '全部', '') + dates.map(d => opt(d, d, '')).join('');

  // 湖南特色标签 chip
  const hunanTags = (S.data.stats && S.data.stats.hunan_tags) || [];
  const chips = $('#chips');
  chips.innerHTML = '';
  if (hunanTags.length) {
    hunanTags.slice(0, 14).forEach(t => {
      const b = document.createElement('span');
      b.className = 'chip';
      b.textContent = t;
      b.onclick = () => {
        const on = b.classList.toggle('on');
        S.filters.hunanTag = on ? t : '';
        applyFilters();
      };
      chips.appendChild(b);
    });
  }
}

function applyFilters() {
  const { q, source, column, date, hunanTag } = S.filters;
  const query = (q || '').trim().toLowerCase();

  S.filtered = S.articles.filter(a => {
    if (source && a.source !== source) return false;
    if (column && a.column !== column) return false;
    if (date && (a.fetch_date || a.date) !== date) return false;
    if (hunanTag) {
      const tags = (a.tags && a.tags.hunan) || [];
      if (!tags.includes(hunanTag)) return false;
    }
    if (query) {
      const hay = [
        a.title, a.author, a.source, a.column, a.angle,
        (a.terms || []).join(' '),
        ((a.tags && a.tags.hunan) || []).join(' '),
        ((a.tags && a.tags.transferable) || []).join(' '),
        ((a.tags && a.tags.data) || []).join(' '),
        (a.outline && a.outline.thesis) || '',
        (a.paragraphs || []).map(p => p.text).join(' '),
      ].join(' ').toLowerCase();
      if (!hay.includes(query)) return false;
    }
    return true;
  });

  renderArticles();
  renderSidebar();
  renderAll();
  const n = S.filtered.length;
  $('#count').textContent = `${n} 篇 / 共 ${S.articles.length} 篇`;
}

// ─────────────────────────── 素材列表 ───────────────────────────

const KIND_LABEL = { thesis: '总论点', point: '论点', case: '案例', quote: '金句', plain: '' };

function renderArticles() {
  const box = $('#articles');
  if (!S.filtered.length) {
    box.innerHTML = '<div class="empty">没有匹配的素材<br>换个筛选条件试试</div>';
    return;
  }
  // 按日期倒序
  const list = S.filtered.slice().sort((a, b) =>
    (b.fetch_date || b.date || '').localeCompare(a.fetch_date || a.date || '') || (b.id || '').localeCompare(a.id || ''));

  box.innerHTML = list.map(a => {
    const paras = (a.paragraphs || []).map(p => {
      const label = KIND_LABEL[p.kind];
      const tag = label ? `<span class="ptag">${label}</span>` : '';
      return `<p class="para kind-${esc(p.kind || 'plain')}">${tag}${highlight(p.text, S.filters.q)}</p>`;
    }).join('');

    const tags = a.tags || {};
    const tagHtml = [
      ...(tags.hunan || []).map(t => `<span class="tag hunan">湖南 · ${esc(t)}</span>`),
      ...(tags.transferable || []).map(t => `<span class="tag transferable">通用 · ${esc(t)}</span>`),
      ...(tags.data || []).map(t => `<span class="tag data">数据 · ${esc(t)}</span>`),
    ].join('');

    const legend = `
      <div class="legend">
        <span class="sw point">论点</span>
        <span class="sw quote">金句</span>
        <span class="sw case">案例</span>
      </div>`;

    const angle = a.angle ? `<div class="angle"><b>申论角度：</b>${highlight(a.angle, S.filters.q)}</div>` : '';

    return `<article class="article-card" id="art-${esc(a.id)}">
      <h3>${highlight(a.title, S.filters.q)}</h3>
      ${a.subtitle ? `<div class="subtitle">${esc(a.subtitle)}</div>` : ''}
      <div class="article-meta">
        <span class="src">${esc(a.source)}</span>
        ${a.column ? `<span>${esc(a.column)}</span>` : ''}
        <span>${esc(a.fetch_date || a.date || '')}</span>
        ${a.author ? `<span>${esc(a.author)}</span>` : ''}
        <a href="${esc(a.url)}" target="_blank" rel="noopener">看原文 ↗</a>
      </div>
      ${legend}
      ${paras}
      ${angle}
      ${tagHtml ? `<div class="tags">${tagHtml}</div>` : ''}
    </article>`;
  }).join('');
}

// ─────────────────────────── 侧栏提纲 ───────────────────────────

function renderSidebar() {
  const box = $('#side-list');
  if (!S.filtered.length) {
    box.innerHTML = '<div class="empty">无匹配</div>';
    return;
  }
  const list = S.filtered.slice().sort((a, b) =>
    (b.fetch_date || b.date || '').localeCompare(a.fetch_date || a.date || '') || (b.id || '').localeCompare(a.id || ''));

  box.innerHTML = list.map(a => {
    const o = a.outline || {};
    const pts = (o.points || []).map(p =>
      `<li><b>${esc(p.title || '')}</b>　${esc(p.summary || '')}</li>`).join('');
    const cases = (o.cases || []).map(c =>
      `<li><b>${esc(c.title || '')}</b>　${esc(c.summary || '')}</li>`).join('');
    return `<div class="side-article" data-id="${esc(a.id)}">
      <div class="t">${highlight(a.title, S.filters.q)}</div>
      <div class="meta"><span>${esc(a.source)}</span><span>${esc(a.column || '')}</span><span>${esc(a.fetch_date || a.date || '')}</span></div>
      ${o.thesis ? `<div class="side-thesis"><b>总论点：</b>${esc(o.thesis)}</div>` : ''}
      ${pts ? `<ul class="side-points">${pts}</ul>` : ''}
      ${cases ? `<ul class="side-cases">${cases}</ul>` : ''}
    </div>`;
  }).join('');

  $$('.side-article').forEach(el => {
    el.onclick = () => {
      const id = el.dataset.id;
      $$('.side-article').forEach(x => x.classList.remove('on'));
      el.classList.add('on');
      const target = document.getElementById('art-' + id);
      if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        target.style.outline = '2px solid var(--accent)';
        setTimeout(() => { target.style.outline = 'none'; }, 1800);
      }
    };
  });
}

// ─────────────────────────── 规范词库 ───────────────────────────

function renderTerms() {
  const box = $('#terms');
  $('#term-count').textContent = S.terms.length;
  if (!S.terms.length) {
    box.innerHTML = '<div class="empty">规范词库为空<br>运行 pipeline 后会自动积累</div>';
    return;
  }
  const q = (S.filters.q || '').trim().toLowerCase();
  const list = S.terms.filter(t => {
    if (!q) return true;
    return (t.term + ' ' + (t.definition || '') + ' ' + (t.scene || '') + ' ' + (t.source || ''))
      .toLowerCase().includes(q);
  });
  if (!list.length) { box.innerHTML = '<div class="empty">无匹配规范词</div>'; return; }

  box.innerHTML = list.map(t => {
    const scenes = (t.scene || '').split(/[、,，]/).filter(Boolean)
      .map(s => `<span class="scene">${esc(s)}</span>`).join('');
    return `<div class="term-card">
      <div class="w">${highlight(t.term, S.filters.q)}${t.count > 1 ? `<span class="cnt">×${t.count}</span>` : ''}</div>
      <div class="d">${highlight(t.definition || '（待补充释义）', S.filters.q)}</div>
      <div class="s">${scenes}${t.source ? `<span>${esc(t.source)}</span>` : ''}</div>
    </div>`;
  }).join('');
}

// ─────────────────────────── 考点提纲页 ───────────────────────────

function renderOutlines() {
  const box = $('#outlines');
  const list = S.filtered.slice().sort((a, b) =>
    (b.fetch_date || b.date || '').localeCompare(a.fetch_date || a.date || '') || (b.id || '').localeCompare(a.id || ''));
  if (!list.length) { box.innerHTML = '<div class="empty">无匹配</div>'; return; }

  box.innerHTML = list.map(a => {
    const o = a.outline || {};
    const pts = (o.points || []).map(p =>
      `<li><b>${esc(p.title || '')}</b>　${esc(p.summary || '')}</li>`).join('');
    const cases = (o.cases || []).map(c =>
      `<li><b>${esc(c.title || '')}</b>　${esc(c.summary || '')}</li>`).join('');
    return `<article class="article-card">
      <h3>${esc(a.title)}</h3>
      <div class="article-meta">
        <span class="src">${esc(a.source)}</span>
        <span>${esc(a.column || '')}</span>
        <span>${esc(a.fetch_date || a.date || '')}</span>
        <a href="${esc(a.url)}" target="_blank" rel="noopener">看原文 ↗</a>
      </div>
      ${o.thesis ? `<div class="side-thesis" style="font-size:14.5px"><b>总论点：</b>${esc(o.thesis)}</div>` : ''}
      ${pts ? `<h4 style="margin:12px 0 6px;color:var(--accent);font-size:15px">论点</h4><ul class="side-points">${pts}</ul>` : ''}
      ${cases ? `<h4 style="margin:12px 0 6px;color:var(--accent);font-size:15px">案例（论据）</h4><ul class="side-cases">${cases}</ul>` : ''}
    </article>`;
  }).join('');
}

// ─────────────────────────── 全部索引 ───────────────────────────

function renderAll() {
  const box = $('#all-list');
  if (!S.filtered.length) { box.innerHTML = '<div class="empty">无匹配</div>'; return; }
  const list = S.filtered.slice().sort((a, b) =>
    (b.fetch_date || b.date || '').localeCompare(a.fetch_date || a.date || '') || (b.id || '').localeCompare(a.id || ''));
  box.innerHTML = list.map(a => `
    <div class="list-row" data-id="${esc(a.id)}">
      <span class="t">${highlight(a.title, S.filters.q)}</span>
        <span class="m">${esc(a.source)} · ${esc(a.column || '')} · ${esc(a.fetch_date || a.date || '')}</span>
    </div>`).join('');

  $$('.list-row').forEach(el => {
    el.onclick = () => {
      switchTab('material');
      setTimeout(() => {
        const t = document.getElementById('art-' + el.dataset.id);
        if (t) {
          t.scrollIntoView({ behavior: 'smooth', block: 'start' });
          t.style.outline = '2px solid var(--accent)';
          setTimeout(() => { t.style.outline = 'none'; }, 1800);
        }
      }, 60);
    };
  });
}

// ─────────────────────────── 统计条 ───────────────────────────

function renderStats() {
  const st = S.data.stats || {};
  // 把统计塞进 count 后面
  const s = $('#count');
  if (s) {
    s.textContent = `${S.filtered.length} 篇 / 共 ${S.articles.length} 篇 · `
      + `${st.total_terms || S.terms.length} 规范词`;
  }
}

// ─────────────────────────── Tab 切换 ───────────────────────────

function switchTab(name) {
  S.tab = name;
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  $$('.panel').forEach(p => p.classList.toggle('on', p.id === 'panel-' + name));
  if (name === 'terms') renderTerms();
  if (name === 'outline') renderOutlines();
  if (name === 'all') renderAll();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// ─────────────────────────── 事件绑定 ───────────────────────────

function init() {
  $$('.tab').forEach(t => { t.onclick = () => switchTab(t.dataset.tab); });

  let timer;
  $('#q').addEventListener('input', (e) => {
    clearTimeout(timer);
    const v = e.target.value;
    timer = setTimeout(() => {
      S.filters.q = v;
      applyFilters();
      if (S.tab === 'terms') renderTerms();
    }, 220);
  });

  $('#f-source').addEventListener('change', e => { S.filters.source = e.target.value; applyFilters(); });
  $('#f-column').addEventListener('change', e => { S.filters.column = e.target.value; applyFilters(); });
  $('#f-date').addEventListener('change', e => { S.filters.date = e.target.value; applyFilters(); });

  load();
}

document.addEventListener('DOMContentLoaded', init);
