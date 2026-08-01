
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
  document.querySelectorAll('.ref-tab').forEach(b => {
    b.style.borderBottomColor = 'transparent';
    b.style.color = '#888';
  });
  const panel = document.getElementById(`refTab-${tab}`);
  panel.style.display       = 'flex';
  panel.style.flexDirection = 'column';
  if (el) { el.style.borderBottomColor = '#E03030'; el.style.color = '#E03030'; }
}


async function searchRefs() {
  const q       = document.getElementById('refSearchInput').value.trim();
  const source  = document.getElementById('refSearchSource').value;
  const results = document.getElementById('refSearchResults');
  if (!q) return;

  results.innerHTML = '<div style="text-align:center;padding:2rem;color:#888;">A pesquisar...</div>';
  try {
    const res = await fetch(
      `/api/references/search?q=${encodeURIComponent(q)}&source=${source}&limit=20`,
      { headers: { 'Authorization': `Bearer ${token}` } }
    );
    if (!res.ok) throw new Error('Erro na pesquisa');
    const items = await res.json();

    if (!items.length) {
      results.innerHTML = '<div style="text-align:center;padding:2rem;color:#888;">Sem resultados.</div>';
      return;
    }

    window._searchResults = items;
    results.innerHTML = items.map((item, i) => {
      const authors  = (item.authors || []).slice(0, 3).join('; ') + (item.authors?.length > 3 ? ' et al.' : '');
      const typeIcon = item.type?.includes('journal') ? '📄' : item.type?.includes('book') ? '📚' : '📑';
      return `
        <label style="display:flex;align-items:flex-start;gap:0.8rem;padding:0.55rem 1rem;border-bottom:1px solid #f0f0f0;cursor:pointer;"
          onmouseover="this.style.background='#fafafa'" onmouseout="this.style.background=''">
          <input type="checkbox" onchange="toggleRefItem(this, ${i})"
            style="margin-top:3px;flex-shrink:0;accent-color:#E03030;">
          <div style="flex:1;min-width:0;">
            <div style="font-weight:700;font-size:0.82rem;color:#111;margin-bottom:2px;">${typeIcon} ${item.title || 'Sem título'}</div>
            <div style="font-size:0.73rem;color:#666;">
              ${authors}${item.year ? ` · ${item.year}` : ''}${item.source ? ` · <em>${item.source}</em>` : ''}
            </div>
            ${item.doi ? `<div style="font-size:0.7rem;color:#2563EB;font-family:'JetBrains Mono',monospace;">DOI: ${item.doi}</div>` : ''}
          </div>
        </label>
      `;
    }).join('');

  } catch (err) {
    results.innerHTML = `<div style="text-align:center;padding:2rem;color:#E03030;">${err.message}</div>`;
  }
}

function toggleRefItem(checkbox, index) {
  const item = window._searchResults?.[index] ?? window._zoteroResults?.[index];
  if (!item) return;
  if (checkbox.checked) refsSelected.push(item);
  else refsSelected = refsSelected.filter(r => r !== item);
  updateRefsFooter();
}

function updateRefsFooter() {
  document.getElementById('refsSelectedInfo').textContent = `${refsSelected.length} selecionada(s)`;
  document.getElementById('refsExportBtn').disabled = refsSelected.length === 0;
}


async function resolveDoi() {
  const doi    = document.getElementById('doiInput').value.trim().replace('https://doi.org/', '');
  const result = document.getElementById('doiResult');
  const addBtn = document.getElementById('doiAddBtn');
  if (!doi) return;

  result.textContent  = 'A resolver DOI...';
  addBtn.style.display = 'none';
  doiRawItem = null;

  try {
    const res = await fetch(`/api/references/doi/${encodeURIComponent(doi)}`, {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    if (!res.ok) throw new Error('DOI não encontrado.');
    doiRawItem           = await res.json();
    result.textContent   = doiRawItem.bibtex;
    addBtn.style.display = 'block';
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
  if (!userId || !apiKey) { alert('Preenche o User ID e a API Key.'); return; }

  try {
    const res = await fetch('/api/references/zotero/connect', {
      method : 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body   : JSON.stringify({ user_id: userId, api_key: apiKey })
    });
    if (!res.ok) { const e = await res.json(); throw new Error(e.detail); }
    document.getElementById('zoteroNotConnected').style.display = 'none';
    document.getElementById('zoteroConnected').style.display    = 'flex';
    await loadZoteroRefs();
  } catch (err) { alert('Erro: ' + err.message); }
}

async function loadZoteroRefs() {
  const list = document.getElementById('zoteroRefList');
  list.innerHTML = '<div style="text-align:center;padding:2rem;color:#888;">A carregar biblioteca...</div>';
  try {
    const res = await fetch('/api/references/zotero', {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    if (!res.ok) throw new Error('Erro ao carregar Zotero');
    zoteroAllRefs          = await res.json();
    zoteroFiltered         = [...zoteroAllRefs];
    window._zoteroResults  = zoteroAllRefs;
    renderZoteroRefs();
  } catch (err) {
    list.innerHTML = `<div style="text-align:center;padding:2rem;color:#E03030;">${err.message}</div>`;
  }
}

function filterZoteroRefs() {
  const q = document.getElementById('zoteroFilter').value.toLowerCase();
  zoteroFiltered = zoteroAllRefs.filter(r =>
    r.title.toLowerCase().includes(q) ||
    r.authors.some(a => a.toLowerCase().includes(q)) ||
    (r.source || '').toLowerCase().includes(q)
  );
  renderZoteroRefs();
}

function renderZoteroRefs() {
  const list = document.getElementById('zoteroRefList');
  if (!zoteroFiltered.length) {
    list.innerHTML = '<div style="text-align:center;padding:2rem;color:#888;">Sem resultados.</div>';
    return;
  }
  list.innerHTML = zoteroFiltered.map((ref, i) => {
    const authors = ref.authors.slice(0, 3).join('; ') + (ref.authors.length > 3 ? ' et al.' : '');
    return `
      <label style="display:flex;align-items:flex-start;gap:0.8rem;padding:0.55rem 1rem;border-bottom:1px solid #f0f0f0;cursor:pointer;"
        onmouseover="this.style.background='#fafafa'" onmouseout="this.style.background=''">
        <input type="checkbox" onchange="toggleRefItem(this, ${i})"
          style="margin-top:3px;flex-shrink:0;accent-color:#E03030;">
        <div style="flex:1;min-width:0;">
          <div style="font-weight:700;font-size:0.82rem;color:#111;margin-bottom:2px;">${ref.title || 'Sem título'}</div>
          <div style="font-size:0.73rem;color:#666;">
            ${authors}${ref.year ? ` · ${ref.year}` : ''}${ref.source ? ` · <em>${ref.source}</em>` : ''}
          </div>
        </div>
      </label>
    `;
  }).join('');
}


async function addManualBib() {
  const content = document.getElementById('manualBibInput').value.trim();
  if (!content) { alert('Introduz uma entrada BibTeX.'); return; }
  if (!content.startsWith('@')) { alert('Entrada BibTeX inválida. Deve começar por @'); return; }
  await _appendRawBib(content);
}


async function exportSelectedRefs() {
  if (!refsSelected.length) return;
  await _exportToBib(refsSelected);
}

async function _exportToBib(items) {
  const log = document.getElementById('logBox');
  log.textContent = 'A exportar para refs.bib...';
  try {
    const res = await fetch(`/api/references/export-bib/${projectId}`, {
      method : 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body   : JSON.stringify({ items })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    log.className   = 'log-box success';
    log.textContent = `✓ ${data.message}`;
    refsSelected = [];
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
    const getRes = await fetch(`/api/projects/${projectId}/files/refs.bib`, {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    let current = '';
    if (getRes.ok) {
      const d = await getRes.json();
      current = d.content || '';
    }
    const newContent = current ? current.trimEnd() + '\n\n' + rawBibtex : rawBibtex;

    await fetch(`/api/projects/${projectId}/files/refs.bib`, {
      method : 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body   : JSON.stringify({ content: newContent })
    });

    log.className   = 'log-box success';
    log.textContent = '✓ Entrada adicionada ao refs.bib';
    document.getElementById('manualBibInput').value = '';
    await _reloadBibFile();
    closeRefsModal();
  } catch (err) {
    log.className   = 'log-box error';
    log.textContent = '! Erro: ' + err.message;
  }
}

async function _reloadBibFile() {
  await loadFiles();
  const bibIdx = files.findIndex(f => f.name === 'refs.bib');
  if (bibIdx >= 0) {
    files[bibIdx].content = null;
    if (!openTabs.includes(bibIdx)) openTabs.push(bibIdx);
    await switchFile(bibIdx);
  }
}
