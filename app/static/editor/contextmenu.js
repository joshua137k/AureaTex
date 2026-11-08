// ---------------------------------------------------------------------------
// Menu de clique direito da árvore de ficheiros (em vez do menu do browser).
// ---------------------------------------------------------------------------

let _ctxMenu = null;

function closeContextMenu() {
  _ctxMenu?.remove();
  _ctxMenu = null;
}

const CTX_ICONS = {
  open:     '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>',
  compile:  '<polygon points="6,4 20,12 6,20"/>',
  main:     '<polygon points="12 2 15.1 8.3 22 9.3 17 14.1 18.2 21 12 17.8 5.8 21 7 14.1 2 9.3 8.9 8.3"/>',
  copy:     '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  ai:       ICONS.ai,
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
  upload:   '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
  rename:   ICONS.rename,
  trash:    ICONS.trash,
  newFile:  ICONS.newFile,
  newFolder: ICONS.newFolder,
};

function _menuItem(label, icon, action, opts = {}) {
  return { label, icon, action, ...opts };
}

function openContextMenu(x, y, title, items) {
  closeContextMenu();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.setAttribute('role', 'menu');

  if (title) {
    const head = document.createElement('div');
    head.className = 'ctx-head';
    head.textContent = title;
    menu.appendChild(head);
  }

  items.filter(Boolean).forEach(item => {
    if (item === '-') {
      menu.appendChild(document.createElement('hr'));
      return;
    }
    const btn = document.createElement('button');
    btn.setAttribute('role', 'menuitem');
    if (item.danger) btn.className = 'danger';
    btn.innerHTML = `<svg viewBox="0 0 24 24">${CTX_ICONS[item.icon] || ''}</svg>`;
    const span = document.createElement('span');
    span.textContent = item.label;
    btn.appendChild(span);
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      closeContextMenu();
      item.action(e);
    });
    menu.appendChild(btn);
  });

  // Tira separadores repetidos ou no fim.
  [...menu.querySelectorAll('hr')].forEach(hr => {
    if (!hr.nextElementSibling || hr.nextElementSibling.tagName === 'HR') hr.remove();
  });

  document.body.appendChild(menu);
  _ctxMenu = menu;

  // Mantém o menu dentro da janela.
  const r = menu.getBoundingClientRect();
  menu.style.left = Math.max(4, Math.min(x, window.innerWidth - r.width - 4)) + 'px';
  menu.style.top = Math.max(4, Math.min(y, window.innerHeight - r.height - 4)) + 'px';
  menu.querySelector('button')?.focus();
}

document.addEventListener('click', closeContextMenu);
document.addEventListener('scroll', closeContextMenu, true);
window.addEventListener('blur', closeContextMenu);
document.addEventListener('keydown', (e) => {
  if (!_ctxMenu) return;
  if (e.key === 'Escape') { closeContextMenu(); return; }
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const btns = [..._ctxMenu.querySelectorAll('button')];
    const i = btns.indexOf(document.activeElement);
    btns[(i + (e.key === 'ArrowDown' ? 1 : -1) + btns.length) % btns.length]?.focus();
  }
});

// ------------------------------------------------------------------ ações
async function _fetchFileContent(name) {
  const res = await fetch(`/api/projects/${projectId}/files/${encPath(name)}`, { headers: authHeaders() });
  if (handleAuthFailure(res)) throw new Error('Session expired');
  if (!res.ok) throw new Error(await apiError(res, 'Could not read the file.'));
  return (await res.json()).content || '';
}

async function duplicateFile(index) {
  const f = files[index];
  const dot = f.name.lastIndexOf('.');
  const slash = f.name.lastIndexOf('/');
  const suggestion = dot > slash + 1 ? `${f.name.slice(0, dot)}-copy${f.name.slice(dot)}` : `${f.name}-copy`;
  let name = await uiPrompt(`Copy of "${f.name}" — name of the new file:`, suggestion, { title: 'Duplicate file', okText: 'Duplicate' });
  if (!name) return;
  name = name.trim().replace(/^\/+/, '');
  if (files.some(x => x.name === name)) { alert(`'${name}' already exists.`); return; }

  try {
    const content = await _fetchFileContent(f.name);
    const res = await fetch(`/api/projects/${projectId}/files/${encPath(name)}`, {
      method: 'PUT',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ content }),
    });
    if (!res.ok) throw new Error(await apiError(res, 'Could not duplicate the file.'));
    if (!files.some(x => x.name === name)) files.push({ name, content: null, binary: f.binary, is_main: false });
    renderFileTree();
    showToast(`Created ${name}`, 'ok');
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function downloadFile(index) {
  const f = files[index];
  try {
    const content = await _fetchFileContent(f.name);
    let blob;
    if (content.startsWith('data:') && content.slice(0, 200).includes(';base64,')) {
      blob = await (await fetch(content)).blob();
    } else {
      blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    }
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = f.name.split('/').pop();
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  } catch (e) {
    showToast(e.message, 'error');
  }
}

async function compileFile(index) {
  if (activeFile !== index) await switchFile(index);
  compilePDF();
}

// ------------------------------------------------------------------ menus
function _fileMenu(index, e) {
  const f = files[index];
  const canEdit = !isViewer();
  const isTex = /\.tex$/i.test(f.name) && !f.binary;

  return [
    _menuItem('Open', 'open', () => switchFile(index)),
    isTex && canEdit ? _menuItem(isRootTex(f.name) || f.is_main ? 'Compile this file' : 'Open and compile', 'compile', () => compileFile(index)) : null,
    isTex && canEdit && !f.is_main ? _menuItem('Set as main file', 'main', () => setMainFile(index)) : null,
    '-',
    !f.binary ? _menuItem('Copy for AI', 'ai', () => copyForAI('full', f.name)) : null,
    _menuItem('Download', 'download', () => downloadFile(index)),
    canEdit ? '-' : null,
    canEdit ? _menuItem('Duplicate…', 'copy', () => duplicateFile(index)) : null,
    canEdit ? _menuItem('Rename / move…', 'rename', () => promptRenameFile(index, e)) : null,
    canEdit && !f.is_main ? '-' : null,
    canEdit && !f.is_main ? _menuItem('Delete', 'trash', () => deleteFile(index, e), { danger: true }) : null,
  ];
}

function _folderMenu(path, e) {
  const canEdit = !isViewer();
  return [
    canEdit ? _menuItem('New file here', 'newFile', () => newFile(path, e)) : null,
    canEdit ? _menuItem('New folder here', 'newFolder', () => newFolder(path, e)) : null,
    canEdit ? '-' : null,
    _menuItem('Copy folder for AI', 'ai', () => copyForAI('full', path)),
    canEdit ? '-' : null,
    canEdit ? _menuItem('Delete folder', 'trash', () => deleteFolder(path, e), { danger: true }) : null,
  ];
}

function _rootMenu(e) {
  const canEdit = !isViewer();
  return [
    canEdit ? _menuItem('New file', 'newFile', () => newFile('', e)) : null,
    canEdit ? _menuItem('New folder', 'newFolder', () => newFolder('', e)) : null,
    canEdit ? _menuItem('Upload files…', 'upload', () => document.getElementById('fileUploadInput').click()) : null,
    canEdit ? '-' : null,
    _menuItem('Copy whole project for AI', 'ai', () => copyForAI('full', '')),
    _menuItem('Copy file tree for AI', 'ai', () => copyForAI('tree', '')),
  ];
}

document.getElementById('filetreeBody')?.addEventListener('contextmenu', (e) => {
  e.preventDefault();
  // Um evento "falso" para as funções que chamam stopPropagation().
  const ev = { stopPropagation() {}, preventDefault() {} };
  const row = e.target.closest('.ftree-row');

  if (row?.dataset.index !== undefined) {
    const i = Number(row.dataset.index);
    openContextMenu(e.clientX, e.clientY, files[i]?.name, _fileMenu(i, ev));
  } else if (row?.dataset.folder !== undefined) {
    openContextMenu(e.clientX, e.clientY, row.dataset.folder + '/', _folderMenu(row.dataset.folder, ev));
  } else {
    openContextMenu(e.clientX, e.clientY, 'Project', _rootMenu(ev));
  }
});

// Também nos separadores do editor.
document.getElementById('editorTabs')?.addEventListener('contextmenu', (e) => {
  const tab = e.target.closest('.tab');
  if (!tab) return;
  e.preventDefault();
  const i = Number(tab.dataset.index);
  if (!files[i]) return;
  openContextMenu(e.clientX, e.clientY, files[i].name, _fileMenu(i, { stopPropagation() {}, preventDefault() {} }));
});
