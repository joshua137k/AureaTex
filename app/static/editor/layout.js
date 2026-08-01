
const LAYOUT_KEY = `joshuatex_layout_proj_${projectId}`;

let layoutPrefs = Object.assign(
  {
    treeWidth: 240,
    treeCollapsed: false,
    previewWidth: null,
  },
  JSON.parse(localStorage.getItem(LAYOUT_KEY) || '{}')
);

function saveLayoutPrefs() {
  localStorage.setItem(LAYOUT_KEY, JSON.stringify(layoutPrefs));
}

function applyLayoutPrefs() {
  const tree    = document.getElementById('filetree');
  const preview = document.getElementById('previewPane');

  if (tree) {
    tree.style.width = layoutPrefs.treeWidth + 'px';
    tree.classList.toggle('collapsed', layoutPrefs.treeCollapsed);
  }
  if (preview && layoutPrefs.previewWidth) {
    preview.style.width = layoutPrefs.previewWidth + 'px';
    preview.style.flex  = 'none';
  }
  updateSidebarToggleBtn();
  setTimeout(() => editor?.layout(), 30);
}


function toggleSidebar() {
  layoutPrefs.treeCollapsed = !layoutPrefs.treeCollapsed;
  document.getElementById('filetree').classList.toggle('collapsed', layoutPrefs.treeCollapsed);
  updateSidebarToggleBtn();
  saveLayoutPrefs();
  setTimeout(() => editor?.layout(), 200);
}

function updateSidebarToggleBtn() {
  const btn = document.getElementById('sidebarToggleBtn');
  if (btn) btn.classList.toggle('active', !layoutPrefs.treeCollapsed);
}


function setupTreeResizer() {
  const resizer = document.getElementById('resizer');
  const tree    = document.getElementById('filetree');
  if (!resizer || !tree) return;

  let dragging = false, startX = 0, startW = 0;

  resizer.addEventListener('mousedown', e => {
    if (tree.classList.contains('collapsed')) return;
    dragging = true; startX = e.clientX; startW = tree.offsetWidth;
    document.body.style.cursor = 'ew-resize';
    document.body.style.userSelect = 'none';
  });
  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const w = Math.min(560, Math.max(160, startW + (e.clientX - startX)));
    tree.style.width = w + 'px';
    layoutPrefs.treeWidth = w;
    editor?.layout();
  });
  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    saveLayoutPrefs();
  });
}

function setupPreviewResizer() {
  const resizer = document.getElementById('previewResizer');
  const preview = document.getElementById('previewPane');
  const layoutEl = document.querySelector('.editor-layout');
  if (!resizer || !preview || !layoutEl) return;

  let dragging = false, startX = 0, startW = 0;

  resizer.addEventListener('mousedown', e => {
    dragging = true; startX = e.clientX; startW = preview.offsetWidth;
    document.body.style.cursor = 'ew-resize';
    document.body.style.userSelect = 'none';
  });
  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const total = layoutEl.offsetWidth;
    const w = Math.min(total - 260, Math.max(280, startW - (e.clientX - startX)));
    preview.style.width = w + 'px';
    preview.style.flex  = 'none';
    layoutPrefs.previewWidth = w;
    editor?.layout();
  });
  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    saveLayoutPrefs();
  });
}


window.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'b') {
    e.preventDefault();
    toggleSidebar();
  }
});


window.addEventListener('DOMContentLoaded', () => {
  applyLayoutPrefs();
  setupTreeResizer();
  setupPreviewResizer();
});