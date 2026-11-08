
const LAYOUT_KEY = `joshuatex_layout_proj_${projectId}`;

const TREE_MIN = 170, TREE_MAX = 560, TREE_DEFAULT = 240;
const PREVIEW_MIN = 280, EDITOR_MIN = 260;

function readLayoutPrefs() {
  try {
    return JSON.parse(localStorage.getItem(LAYOUT_KEY) || '{}') || {};
  } catch {
    return {};
  }
}

let layoutPrefs = Object.assign(
  {
    treeWidth: TREE_DEFAULT,
    treeCollapsed: false,
    previewWidth: null,
  },
  readLayoutPrefs()
);

function saveLayoutPrefs() {
  try {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(layoutPrefs));
  } catch { }
}

function relayoutEditor() {
  if (typeof editor !== 'undefined' && editor) editor.layout();
}

function applyLayoutPrefs() {
  const tree    = document.getElementById('filetree');
  const preview = document.getElementById('previewPane');

  if (tree) {
    tree.style.width = layoutPrefs.treeWidth + 'px';
    tree.classList.toggle('collapsed', layoutPrefs.treeCollapsed);
  }
  if (preview) {
    if (layoutPrefs.previewWidth) {
      preview.style.width = layoutPrefs.previewWidth + 'px';
    } else {
      preview.style.width = '';
    }
  }
  updateSidebarToggleBtn();
  setTimeout(relayoutEditor, 30);
}


function toggleSidebar() {
  layoutPrefs.treeCollapsed = !layoutPrefs.treeCollapsed;
  document.getElementById('filetree').classList.toggle('collapsed', layoutPrefs.treeCollapsed);
  updateSidebarToggleBtn();
  saveLayoutPrefs();
  setTimeout(relayoutEditor, 200);
}

function updateSidebarToggleBtn() {
  const btn = document.getElementById('sidebarToggleBtn');
  if (btn) btn.classList.toggle('active', !layoutPrefs.treeCollapsed);
}


/**
 * Divisória arrastável.
 *
 * Usa Pointer Events com "pointer capture": enquanto o botão está premido, o
 * próprio separador recebe todos os movimentos, mesmo que o rato passe por
 * cima do iframe do PDF ou do Monaco (era por isso que o arrasto "parava" —
 * o iframe engolia os eventos de rato do documento).
 */
function makeResizer(handle, { onStart, onMove, onEnd, onReset }) {
  if (!handle) return;

  let startX = 0;
  let frame = 0;
  let lastX = 0;

  handle.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    if (onStart(e) === false) return;

    e.preventDefault();
    startX = lastX = e.clientX;

    handle.setPointerCapture(e.pointerId);
    handle.classList.add('dragging');
    document.body.classList.add('is-resizing');
  });

  handle.addEventListener('pointermove', (e) => {
    if (!handle.hasPointerCapture(e.pointerId)) return;

    lastX = e.clientX;

    // No máximo uma atualização por frame: arrasto suave mesmo com o Monaco a redesenhar.
    if (!frame) {
      frame = requestAnimationFrame(() => {
        frame = 0;
        onMove(lastX - startX);
        relayoutEditor();
      });
    }
  });

  const finish = (e) => {
    if (!handle.classList.contains('dragging')) return;

    if (handle.hasPointerCapture(e.pointerId)) handle.releasePointerCapture(e.pointerId);
    handle.classList.remove('dragging');
    document.body.classList.remove('is-resizing');

    if (frame) { cancelAnimationFrame(frame); frame = 0; }
    onMove(lastX - startX);
    relayoutEditor();
    onEnd();
  };

  handle.addEventListener('pointerup', finish);
  handle.addEventListener('pointercancel', finish);
  handle.addEventListener('lostpointercapture', finish);

  handle.addEventListener('dblclick', () => {
    onReset();
    relayoutEditor();
  });
}

function setupTreeResizer() {
  const tree = document.getElementById('filetree');
  if (!tree) return;

  let startW = 0;

  makeResizer(document.getElementById('resizer'), {
    onStart() {
      if (tree.classList.contains('collapsed')) return false;
      startW = tree.offsetWidth;
    },
    onMove(dx) {
      const w = Math.min(TREE_MAX, Math.max(TREE_MIN, startW + dx));
      tree.style.width = w + 'px';
      layoutPrefs.treeWidth = w;
    },
    onEnd: saveLayoutPrefs,
    onReset() {
      layoutPrefs.treeWidth = TREE_DEFAULT;
      tree.style.width = TREE_DEFAULT + 'px';
      saveLayoutPrefs();
    },
  });
}

function setupPreviewResizer() {
  const preview  = document.getElementById('previewPane');
  const layoutEl = document.querySelector('.editor-layout');
  const tree     = document.getElementById('filetree');
  if (!preview || !layoutEl) return;

  let startW = 0;

  const maxPreviewWidth = () =>
    Math.max(PREVIEW_MIN, layoutEl.offsetWidth - (tree ? tree.offsetWidth : 0) - EDITOR_MIN);

  makeResizer(document.getElementById('previewResizer'), {
    onStart() {
      startW = preview.offsetWidth;
    },
    onMove(dx) {
      const w = Math.min(maxPreviewWidth(), Math.max(PREVIEW_MIN, startW - dx));
      preview.style.width = w + 'px';
      layoutPrefs.previewWidth = w;
    },
    onEnd: saveLayoutPrefs,
    onReset() {
      layoutPrefs.previewWidth = null;
      preview.style.width = '';
      saveLayoutPrefs();
    },
  });

  // Se a janela encolher, o PDF não pode empurrar o editor para fora do ecrã.
  window.addEventListener('resize', () => {
    if (layoutPrefs.previewWidth && preview.offsetWidth > maxPreviewWidth()) {
      preview.style.width = maxPreviewWidth() + 'px';
    }
    relayoutEditor();
  });
}


window.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'b') {
    e.preventDefault();
    toggleSidebar();
  }

  if (e.key === 'Escape') {
    document.querySelectorAll('.modal-overlay').forEach(m => {
      if (m.style.display !== 'none') m.style.display = 'none';
    });
  }
});

// Clicar fora de um diálogo fecha-o.
document.addEventListener('mousedown', (e) => {
  if (e.target.classList && e.target.classList.contains('modal-overlay')) {
    e.target.style.display = 'none';
  }
});


window.addEventListener('DOMContentLoaded', () => {
  applyLayoutPrefs();
  setupTreeResizer();
  setupPreviewResizer();
});
