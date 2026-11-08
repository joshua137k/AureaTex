function openRefsModal() {
  document.getElementById('refsModal').style.display = 'flex';
  checkZoteroStatus();
}

function closeRefsModal() {
  document.getElementById('refsModal').style.display = 'none';
}

function switchRefTab(tab, el) {
  ['search', 'doi', 'zotero', 'manual'].forEach(t => {
    document.getElementById(`refTab-${t}`).style.display = 'none';
  });
  document.querySelectorAll('.ref-tab').forEach(b => b.classList.remove('active'));
  document.getElementById(`refTab-${tab}`).style.display = 'flex';
  if (el) el.classList.add('active');
}


async function searchRefs() {
  const q       = document.getElementById('refSearchInput').value.trim();
  const source  = document.getElementById('refSearchSource').value;
  const results = document.getElementById('refSearchResults');
  if (!q) return;

  results.innerHTML = '<div class="ref-placeholder">Searching…</div>';
  try {
    const res = await fetch(
      `/api/references/search?q=${encodeURIComponent(q)}&source=${source}&limit=20`,
      { headers: { 'Authorization': `Bearer ${token}` } }
    );
    if (!res.ok) throw new Error(await apiError(res, 'Search error'));
    const items = await res.json();

    if (!items.length) {
      results.innerHTML = '<div class="ref-placeholder">No results.</div>';
      return;
    }

    window._searchResults = items;
    results.innerHTML = items.map((item, i) => {
      const authors  = escapeHtml((item.authors || []).slice(0, 3).join('; ') + (item.authors?.length > 3 ? ' et al.' : ''));
      const typeLabel = item.type?.includes('journal') ? 'Article' : item.type?.includes('book') ? 'Book'
        : item.type?.includes('proceedings') ? 'Conference' : '';
      return `
        <label class="ref-item">
          <input type="checkbox" onchange="toggleRefItem(this, ${i}, 'search')">
          <div class="ref-item-body">
            <div class="ref-item-title">${typeLabel ? `<span class="ref-type">${typeLabel}</span>` : ''}${escapeHtml(item.title || 'Untitled')}</div>
            <div class="ref-item-meta">
              ${authors}${item.year ? ` · ${escapeHtml(item.year)}` : ''}${item.source ? ` · <em>${escapeHtml(item.source)}</em>` : ''}
            </div>
            ${item.doi ? `<div class="ref-item-doi">DOI: ${escapeHtml(item.doi)}</div>` : ''}
          </div>
        </label>
      `;
    }).join('');

  } catch (err) {
    results.innerHTML = `<div class="ref-placeholder error">${escapeHtml(err.message)}</div>`;
  }
}

function toggleRefItem(checkbox, index, source) {
  // O índice refere-se à lista que está no ecrã (a do Zotero pode estar filtrada).
  const list = source === 'zotero' ? zoteroFiltered : window._searchResults;
  const item = list?.[index];
  if (!item) return;
  if (checkbox.checked) refsSelected.push(item);
  else refsSelected = refsSelected.filter(r => r !== item);
  updateRefsFooter();
}

function updateRefsFooter() {
  document.getElementById('refsSelectedInfo').textContent = `${refsSelected.length} selected`;
  document.getElementById('refsExportBtn').disabled = refsSelected.length === 0;
}


async function resolveDoi() {
  const doi    = document.getElementById('doiInput').value.trim().replace(/^https?:\/\/(dx\.)?doi\.org\//i, '').replace(/^doi:\s*/i, '');
  const result = document.getElementById('doiResult');
  const addBtn = document.getElementById('doiAddBtn');
  if (!doi) return;

  result.textContent  = 'Resolving DOI...';
  addBtn.style.display = 'none';
  doiRawItem = null;

  try {
    const res = await fetch(`/api/references/doi/${encPath(doi)}`, {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    if (!res.ok) throw new Error(await apiError(res, 'DOI not found.'));
    doiRawItem           = await res.json();
    result.textContent   = doiRawItem.bibtex;
    addBtn.style.display = 'inline-flex';
  } catch (err) {
    result.textContent = '! ' + err.message;
  }
}

async function addDoiToBib() {
  if (!doiRawItem?.bibtex) return;
  await _appendRawBib(doiRawItem.bibtex);
}


async function checkZoteroStatus() {
  try {
    const res = await fetch('/api/references/zotero/status', {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    const { connected } = await res.json();
    document.getElementById('zoteroNotConnected').style.display = connected ? 'none'  : 'block';
    document.getElementById('zoteroConnected').style.display    = connected ? 'flex'  : 'none';
    if (connected && !zoteroAllRefs.length) loadZoteroRefs();
  } catch {  }
}

async function connectZotero() {
  const userId = document.getElementById('zoteroUserId').value.trim();
  const apiKey = document.getElementById('zoteroApiKey').value.trim();
  if (!userId || !apiKey) { alert('Fill in the User ID and API Key.'); return; }

  try {
    const res = await fetch('/api/references/zotero/connect', {
      method : 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body   : JSON.stringify({ user_id: userId, api_key: apiKey })
    });
    if (!res.ok) throw new Error(await apiError(res, 'Could not connect to Zotero.'));
    document.getElementById('zoteroNotConnected').style.display = 'none';
    document.getElementById('zoteroConnected').style.display    = 'flex';
    await loadZoteroRefs();
  } catch (err) { alert('Error: ' + err.message); }
}

async function loadZoteroRefs() {
  const list = document.getElementById('zoteroRefList');
  list.innerHTML = '<div class="ref-placeholder">Loading library…</div>';
  try {
    const res = await fetch('/api/references/zotero', {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    if (!res.ok) throw new Error(await apiError(res, 'Error loading Zotero'));
    zoteroAllRefs          = (await res.json()).map(r => ({ ...r, _raw_source: 'zotero' }));
    zoteroFiltered         = [...zoteroAllRefs];
    window._zoteroResults  = zoteroAllRefs;
    renderZoteroRefs();
  } catch (err) {
    list.innerHTML = `<div class="ref-placeholder error">${escapeHtml(err.message)}</div>`;
  }
}

function filterZoteroRefs() {
  const q = document.getElementById('zoteroFilter').value.toLowerCase();
  zoteroFiltered = zoteroAllRefs.filter(r =>
    (r.title || '').toLowerCase().includes(q) ||
    (r.authors || []).some(a => a.toLowerCase().includes(q)) ||
    (r.source || '').toLowerCase().includes(q)
  );
  renderZoteroRefs();
}

function renderZoteroRefs() {
  const list = document.getElementById('zoteroRefList');
  if (!zoteroFiltered.length) {
    list.innerHTML = '<div class="ref-placeholder">No results.</div>';
    return;
  }
  list.innerHTML = zoteroFiltered.map((ref, i) => {
    const authors = escapeHtml((ref.authors || []).slice(0, 3).join('; ') + ((ref.authors || []).length > 3 ? ' et al.' : ''));
    const checked = refsSelected.includes(ref) ? 'checked' : '';
    return `
      <label class="ref-item">
        <input type="checkbox" ${checked} onchange="toggleRefItem(this, ${i}, 'zotero')">
        <div class="ref-item-body">
          <div class="ref-item-title">${escapeHtml(ref.title || 'Untitled')}</div>
          <div class="ref-item-meta">
            ${authors}${ref.year ? ` · ${escapeHtml(ref.year)}` : ''}${ref.source ? ` · <em>${escapeHtml(ref.source)}</em>` : ''}
          </div>
        </div>
      </label>
    `;
  }).join('');
}


async function addManualBib() {
  const content = document.getElementById('manualBibInput').value.trim();
  if (!content) { alert('Enter a BibTeX entry.'); return; }
  if (!content.startsWith('@')) { alert('Invalid BibTeX entry. It must start with @'); return; }
  await _appendRawBib(content);
}


async function exportSelectedRefs() {
  if (!refsSelected.length) return;
  await _exportToBib(refsSelected);
}

async function _exportToBib(items) {
  const log = document.getElementById('logBox');
  log.textContent = 'Exporting to refs.bib...';
  try {
    const res = await fetch(`/api/references/export-bib/${projectId}`, {
      method : 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body   : JSON.stringify({ items })
    });
    if (!res.ok) throw new Error(await apiError(res, 'Error exporting references.'));
    const data = await res.json();
    log.className   = 'log-box success';
    log.textContent = `✓ ${data.message}`;
    refsSelected = [];
    document.querySelectorAll('#refsModal input[type="checkbox"]').forEach(c => c.checked = false);
    updateRefsFooter();
    await _reloadBibFile();
    closeRefsModal();
  } catch (err) {
    log.className   = 'log-box error';
    log.textContent = '! ' + err.message;
  }
}

async function _appendRawBib(rawBibtex) {
  const log = document.getElementById('logBox');
  try {
    // É o servidor que acrescenta ao refs.bib atual. Antes o browser enviava o
    // ficheiro inteiro a partir da sua cópia local, que podia estar
    // desatualizada e apagava entradas adicionadas entretanto.
    const res = await fetch(`/api/references/append-bib/${projectId}`, {
      method : 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body   : JSON.stringify({ bibtex: rawBibtex })
    });
    if (!res.ok) throw new Error(await apiError(res, 'Could not add the entry.'));
    const data = await res.json();

    log.className   = data.status === 'skipped' ? 'log-box error' : 'log-box success';
    log.textContent = (data.status === 'skipped' ? '! ' : '✓ ') + data.message;
    document.getElementById('manualBibInput').value = '';
    await _reloadBibFile();
    closeRefsModal();
  } catch (err) {
    log.className   = 'log-box error';
    log.textContent = '! Error: ' + err.message;
    alert(err.message);
  }
}

async function _reloadBibFile() {
  let bibIdx = files.findIndex(f => f.name === 'refs.bib');
  if (bibIdx === -1) {
    await loadFiles();
    bibIdx = files.findIndex(f => f.name === 'refs.bib');
  }
  
  if (bibIdx >= 0) {
    if (!openTabs.includes(bibIdx)) openTabs.push(bibIdx);
    await switchFile(bibIdx);
  }
}