// ---------------------------------------------------------------------------
// "Copy for AI": copia a árvore + conteúdo dos ficheiros para a área de
// transferência, pronto a colar (Ctrl+V) num chat de IA.
// ---------------------------------------------------------------------------

function toggleAiMenu(event) {
  event.stopPropagation();
  const menu = document.getElementById('aiMenu');

  // O painel de ficheiros "prende" até elementos position:fixed (e corta-os):
  // o menu passa a viver no <body>, por cima de tudo.
  if (menu.parentElement !== document.body) document.body.appendChild(menu);

  const open = menu.classList.toggle('open');
  if (!open) return;

  // O painel de ficheiros é estreito e corta o que sai dele: o menu é
  // posicionado em relação à janela, junto ao botão e sempre dentro do ecrã.
  const r = document.getElementById('aiCopyBtn').getBoundingClientRect();
  const w = menu.offsetWidth, h = menu.offsetHeight;
  menu.style.left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8)) + 'px';
  menu.style.top = (r.bottom + h + 8 > window.innerHeight ? Math.max(8, r.top - h - 4) : r.bottom + 4) + 'px';
}

window.addEventListener('resize', () => document.getElementById('aiMenu')?.classList.remove('open'));

document.addEventListener('click', (e) => {
  const menu = document.getElementById('aiMenu');
  if (menu && !e.target.closest('.ai-copy-wrap, #aiMenu')) menu.classList.remove('open');
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') document.getElementById('aiMenu')?.classList.remove('open');
});

function currentFileName() {
  return activeFile >= 0 && files[activeFile] ? files[activeFile].name : '';
}

function showToast(message, kind = 'ok') {
  let el = document.getElementById('appToast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'appToast';
    el.className = 'app-toast';
    el.setAttribute('role', 'status');
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.dataset.kind = kind;
  el.classList.add('show');
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.remove('show'), 3500);
}

async function _fetchAiContext(mode, path) {
  const qs = new URLSearchParams({ mode, path: path || '' });
  const res = await fetch(`/api/projects/${projectId}/ai-context?${qs}`, { headers: authHeaders() });
  if (handleAuthFailure(res)) throw new Error('Session expired');
  if (!res.ok) throw new Error(await apiError(res, 'Could not build the text.'));
  return res.json();
}

// O Safari só deixa escrever na área de transferência durante o clique; por
// isso passa-se ao ClipboardItem uma *promessa* do texto, criada logo no clique.
async function _copyText(promiseOfText) {
  if (window.ClipboardItem && navigator.clipboard?.write) {
    try {
      const blob = promiseOfText.then(t => new Blob([t], { type: 'text/plain' }));
      await navigator.clipboard.write([new ClipboardItem({ 'text/plain': blob })]);
      return;
    } catch (e) {
      if (e?.name !== 'NotAllowedError' && e?.name !== 'TypeError') throw e;
    }
  }

  const text = await promiseOfText;

  if (navigator.clipboard?.writeText) {
    try { await navigator.clipboard.writeText(text); return; } catch { }
  }

  // Último recurso (HTTP sem TLS, browsers antigos).
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.cssText = 'position:fixed;top:-1000px;opacity:0';
  document.body.appendChild(ta);
  ta.select();
  const ok = document.execCommand('copy');
  ta.remove();
  if (!ok) throw new Error('The browser blocked the clipboard.');
}

function copyCurrentFileForAI() {
  const name = currentFileName();
  if (!name || files[activeFile].binary) {
    document.getElementById('aiMenu')?.classList.remove('open');
    showToast('Open a text file first.', 'error');
    return;
  }
  copyForAI('full', name);
}

// O servidor junta o que ainda estiver na memória da colaboração, por isso o
// texto copiado inclui o que acabou de ser escrito.
async function copyForAI(mode, path = '') {
  document.getElementById('aiMenu')?.classList.remove('open');
  showToast('Preparing…', 'info');

  let info = null;
  const textPromise = _fetchAiContext(mode, path).then(data => { info = data; return data.text; });

  try {
    await _copyText(textPromise);
  } catch (e) {
    showToast('Copy failed: ' + e.message, 'error');
    return;
  }

  const what = path ? `“${path}”` : (mode === 'tree' ? 'File tree' : 'Project');
  const tokens = Math.round(info.chars / 4).toLocaleString();
  let msg = `${what} copied — ${info.included} file(s), ~${tokens} tokens. Paste with Ctrl+V.`;
  if (mode === 'tree') msg = `${what} copied (${info.files} files). Paste with Ctrl+V.`;
  if (info.skipped?.length) msg += ` ${info.skipped.length} large file(s) left out.`;
  showToast(msg, 'ok');
}
