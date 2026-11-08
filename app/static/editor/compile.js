// ---------------------------------------------------------------------------
// Que ficheiro compilar
//
// - Ficheiro aberto com \documentclass (ex.: uma segunda versão do documento)
//   → compila esse, sem mudar o ficheiro principal do projeto.
// - Ficheiro aberto sem \documentclass (um capítulo incluído)
//   → compila o último documento compilado (ou o principal).
// - Caso contrário → o principal.
// ---------------------------------------------------------------------------
let lastCompiledRoot = null;

function _fileText(name) {
  const m = openModels.get(name);
  if (m) return m.getValue();
  return files.find(f => f.name === name)?.content || '';
}

function isRootTex(name) {
  return /\.tex$/i.test(name) && /^[^%\n]*\\documentclass/m.test(_fileText(name));
}

function compileTargetFile() {
  const main = files.find(f => f.is_main)?.name || null;
  const open = activeFile >= 0 ? files[activeFile]?.name : null;

  if (open && isRootTex(open)) return open;
  if (lastCompiledRoot && files.some(f => f.name === lastCompiledRoot)) return lastCompiledRoot;
  return main;
}

function updateCompileTarget() {
  const el = document.getElementById('compileTarget');
  if (!el) return;
  const target = compileTargetFile();
  const main = files.find(f => f.is_main)?.name;
  el.textContent = target ? target.split('/').pop() : '';
  el.classList.toggle('not-main', !!target && target !== main);
  el.title = target === main
    ? `Compiling the main file (${target})`
    : `Compiling ${target} — the main file stays ${main || '(none)'}. Open a file with \\documentclass to compile it.`;
}

async function compilePDF() {
  if (compiling) return;
  compiling = true;

  await saveFile();

  const target = compileTargetFile();
  const main = files.find(f => f.is_main)?.name;
  const fileParam = target && target !== main ? `?file=${encodeURIComponent(target)}` : '';
  updateCompileTarget();

  const dot   = document.getElementById('statusDot');
  const log   = document.getElementById('logBox');
  const btn   = document.getElementById('compileBtn');
  const panel = document.getElementById('errorPanel');

  dot.className    = 'status-dot compiling';
  log.className    = 'log-box';
  log.textContent  = 'Compiling on server...';
  panel.style.display = 'none';
  btn.innerHTML    = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/></svg> Compiling…';
  btn.disabled = true;

  try {
    const res = await fetch(`/api/projects/${projectId}/compile${fileParam}`, {
      method : 'POST',
      headers: { 'Authorization': `Bearer ${token}` }
    });

    if (target) lastCompiledRoot = target;

    if (res.ok) {
      await showPdf(await res.blob());

      dot.className    = 'status-dot';
      log.className    = 'log-box success';
      log.textContent  = '✓ PDF compiled successfully.';

      try {
        const logRes  = await fetch(`/api/projects/${projectId}/compile/log`, {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        const logData = await logRes.json();
        if (logData.length > 0 && logData[0].log_output) {
          const { errors, warnings } = await parseLatexLogAsync(logData[0].log_output);
          currentErrors   = errors;
          currentWarnings = warnings;
          document.getElementById('rawLogContent').textContent = logData[0].log_output;
          renderErrorPanel(errors, warnings);
          // O pdflatex gera PDF mesmo com erros: avisa em vez de dizer só "sucesso".
          if (errors.length > 0) {
            log.className   = 'log-box error';
            log.textContent = `! PDF generated with ${errors.length} error(s). Click to view.`;
          } else if (warnings.length > 0) {
            log.textContent = `✓ Compiled with ${warnings.length} warning(s). Click to view.`;
          }
        }
      } catch { }

    } else {
      if (handleAuthFailure(res)) return;
      const errData = await res.json().catch(() => ({}));
      const detail  = errData.detail;
      const summary = typeof detail === 'string' ? detail : (detail?.error || 'Compilation error.');
      const rawLog  = (typeof detail === 'object' && detail?.log) || summary;
      const { errors, warnings } = await parseLatexLogAsync(rawLog);
      currentErrors   = errors;
      currentWarnings = warnings;

      document.getElementById('rawLogContent').textContent = rawLog;
      renderErrorPanel(errors, warnings);

      panel.style.display       = 'flex';
      panel.style.flexDirection = 'column';

      dot.className   = 'status-dot error';
      log.className   = 'log-box error';
      log.textContent = errors.length > 0
        ? `! ${errors.length} compilation error(s). Click to view.`
        : `! ${summary} Click to view the log.`;
    }

  } catch {
    log.textContent = '! Server connection error.';
    dot.className   = 'status-dot error';
  } finally {
    btn.disabled  = false;
    btn.innerHTML = '<svg viewBox="0 0 24 24"><polygon points="5,3 19,12 5,21"/></svg> Compile';
    compiling     = false;
  }
}

// ---------------------------------------------------------------------------
// Pré-visualização do PDF
// ---------------------------------------------------------------------------
let currentPdfBlob = null;
let pdfViewerReady = false;

const PDF_VIEWER_URL = '/static/pdfjs/web/viewer.html';

function pdfViewerSrc(url) {
  return PDF_VIEWER_URL + '?file=' + encodeURIComponent(url) + '#toolbar=0&view=FitH';
}

/**
 * Mostra o PDF acabado de compilar.
 *
 * Da primeira vez carrega o visualizador. Nas seguintes troca só o documento
 * dentro do visualizador que já está aberto e repõe o zoom e a posição de
 * scroll — antes recarregava-se o iframe inteiro e o PDF voltava sempre ao
 * início.
 */
async function showPdf(blob) {
  const oldUrl = currentPdfUrl;
  currentPdfBlob = blob;
  currentPdfUrl = URL.createObjectURL(blob);

  const iframe = document.getElementById('pdfPreviewIframe');
  document.getElementById('previewBody').classList.add('has-pdf');

  let swapped = false;

  if (pdfViewerReady) {
    try {
      swapped = await swapPdfDocument(iframe, blob);
    } catch (err) {
      console.warn('[PDF] Could not swap the document, reloading the viewer:', err);
    }
  }

  if (!swapped) {
    pdfViewerReady = false;
    iframe.onload = () => setupPdfViewer(iframe);
    iframe.src = pdfViewerSrc(currentPdfUrl);
  }

  if (externalPdfWindow && !externalPdfWindow.closed) {
    externalPdfWindow.location.replace(PDF_VIEWER_URL + '?file=' + encodeURIComponent(currentPdfUrl) + '#view=FitH');
  }

  // O URL antigo ainda pode estar a ser lido pelo visualizador: liberta-se depois.
  if (oldUrl) setTimeout(() => URL.revokeObjectURL(oldUrl), 30000);
}

async function swapPdfDocument(iframe, blob) {
  const app = getPdfViewerApp();
  const container = iframe.contentDocument?.getElementById('viewerContainer');

  if (!app || !app.pdfViewer || !app.pdfDocument || !container) return false;

  const state = {
    top: container.scrollTop,
    left: container.scrollLeft,
    scale: app.pdfViewer.currentScaleValue,
    page: app.page,
  };

  const restore = () => {
    try {
      if (state.scale && app.pdfViewer.currentScaleValue !== state.scale) {
        app.pdfViewer.currentScaleValue = state.scale;
      }
      // Se o documento ficou mais curto, o browser limita o scroll ao fim.
      container.scrollTop = state.top;
      container.scrollLeft = state.left;
    } catch { }
  };

  // O pdf.js define a posição inicial em vários momentos do carregamento;
  // repõe-se a nossa depois de cada um.
  app.eventBus.on('pagesinit', () => { restore(); requestAnimationFrame(restore); }, { once: true });
  app.eventBus.on('pagesloaded', () => { restore(); setTimeout(restore, 60); setTimeout(restore, 250); }, { once: true });

  await app.open({ data: new Uint8Array(await blob.arrayBuffer()) });
  return true;
}

function setupPdfViewer(iframe) {
  const viewerWindow = iframe.contentWindow;
  const app = viewerWindow?.PDFViewerApplication;

  applyPdfViewerTheme();

  if (!app || !app.initializedPromise) return;

  app.initializedPromise.then(() => {
    pdfViewerReady = true;

    app.eventBus.on('pagechanging', (evt) => {
      document.getElementById('pdfCurrentPage').textContent = evt.pageNumber;
    });
    app.eventBus.on('pagesinit', () => {
      document.getElementById('pdfTotalPages').textContent = app.pagesCount;
      document.getElementById('pdfCurrentPage').textContent = app.page;
    });
    app.eventBus.on('scalechanging', (evt) => {
      document.getElementById('pdfZoomLabel').textContent = Math.round(evt.scale * 100) + '%';
    });
  });

  // Atalhos também com o foco dentro do PDF.
  viewerWindow.addEventListener('keydown', (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && (e.key.toLowerCase() === 's' || e.key === 'Enter')) {
      e.preventDefault();
      e.stopPropagation();
      compilePDF();
    }
  }, true);

  // Duplo clique no PDF → salta para a linha correspondente no código (SyncTeX).
  viewerWindow.ondblclick = async (e) => {
    const pageEl = e.target.closest('.page');
    if (!pageEl) return;

    const pageNum = parseInt(pageEl.dataset.pageNumber);
    const rect = pageEl.getBoundingClientRect();
    const scale = app.pdfViewer.currentScale || 1.0;
    const ptX = (e.clientX - rect.left) / scale;
    const ptY = (e.clientY - rect.top) / scale;

    try {
      const fileQs = lastCompiledRoot ? `&file=${encodeURIComponent(lastCompiledRoot)}` : '';
      const res = await fetch(`/api/projects/${projectId}/synctex?page=${pageNum}&x=${ptX}&y=${ptY}${fileQs}`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      const data = await res.json();
      if (data.file && data.line) jumpToLine(data.line, data.file);
    } catch (err) {
      console.error('SyncTeX communication failed:', err);
    }
  };
}

function downloadPDF() {
  if (!currentPdfBlob) {
    alert('Please compile the project first to generate the PDF.');
    return;
  }

  // Descarrega o PDF em si. (Antes usava-se o endereço do visualizador, por
  // isso o ficheiro ".pdf" era na verdade uma página HTML — "corrompido".)
  const name = (document.getElementById('projectTitle').value || 'document')
    .trim().replace(/[\\/:*?"<>|]+/g, '_').replace(/\s+/g, '_') || 'document';

  const url = URL.createObjectURL(new Blob([currentPdfBlob], { type: 'application/pdf' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = name + '.pdf';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

function downloadZip() {
  const log = document.getElementById('logBox');
  log.textContent = 'Preparing ZIP...';

  fetch(`/api/projects/${projectId}/download-zip`, {
    headers: { 'Authorization': `Bearer ${token}` }
  })
  .then(async res => {
    if (!res.ok) throw new Error(await apiError(res, 'Error generating ZIP'));
    return res.blob();
  })
  .then(blob => {
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = (document.getElementById('projectTitle').value || 'project').replace(/[\\/:*?"<>|]/g, '_') + '.zip';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    log.textContent = '✓ ZIP downloaded.';
  })
  .catch(err => { log.textContent = '! Error generating ZIP: ' + err.message; });
}

async function publishGitea() {
  await saveFile();
  const btn = document.getElementById('giteaBtn');
  const dot = document.getElementById('statusDot');
  const log = document.getElementById('logBox');

  btn.disabled    = true;
  btn.innerHTML   = 'Pushing…';
  dot.className   = 'status-dot compiling';
  log.className   = 'log-box';
  log.textContent = 'Pushing to the remote repository...';

  try {
    const res  = await fetch(`/api/projects/${projectId}/publish-remote`, {
      method : 'POST', headers: { 'Authorization': `Bearer ${token}` }
    });
    if (res.ok) {
      const data = await res.json();
      log.className = 'log-box success';
      log.textContent = '✓ ' + data.message;
      dot.className = 'status-dot';
    } else { throw new Error(await apiError(res, 'Unknown error')); }
  } catch (err) {
    log.className = 'log-box error';
    log.textContent = '! Push failed: ' + err.message;
    dot.className = 'status-dot error';
  } finally {
    btn.disabled  = false;
    btn.innerHTML = _giteaBtnHTML('Push');
  }
}

async function pullGitea() {
  if (!await uiConfirm('Pull replaces the project files with the version in the remote repository. Changes not pushed yet will be discarded.', { title: 'Pull from remote?', okText: 'Pull', danger: true })) return;

  const btn = document.getElementById('giteaPullBtn');
  const dot = document.getElementById('statusDot');
  const log = document.getElementById('logBox');

  btn.disabled    = true;
  btn.innerHTML   = 'Pulling…';
  dot.className   = 'status-dot compiling';
  log.className   = 'log-box';
  log.textContent = 'Pulling changes from the remote repository...';

  try {
    const res  = await fetch(`/api/projects/${projectId}/pull-remote`, {
      method: 'POST', headers: { 'Authorization': `Bearer ${token}` }
    });
    if (res.ok) {
      const data = await res.json();
      dot.className = 'status-dot';
      log.className = 'log-box success';
      log.textContent = `✓ ${data.message} Updating project...`;
      
      setTimeout(() => {
        window.location.reload();
      }, 1000);

    } else { throw new Error(await apiError(res, 'Unknown error')); }
  } catch (err) {
    log.className   = 'log-box error';
    log.textContent = '! Pull failed: ' + err.message;
    dot.className   = 'status-dot error';
    btn.disabled  = false;
    btn.innerHTML = _giteaBtnHTML('Pull');
  } 
}

function _giteaBtnHTML(label) {
  const arrow = label === 'Pull'
    ? '<polyline points="8 17 12 21 16 17"/>'
    : '<polyline points="16 16 12 12 8 16"/>';
  return `<svg viewBox="0 0 24 24">${arrow}<line x1="12" y1="12" x2="12" y2="21"/><path d="M20.39 18.39A5 5 0 0 0 18 9h-1.26A8 8 0 1 0 3 16.3"/></svg>${label}`;
}

// O visualizador de PDF (pdf.js, dentro do iframe) acompanha o tema da app.
function applyPdfViewerTheme() {
  try {
    const iframe = document.getElementById('pdfPreviewIframe');
    const doc = iframe?.contentDocument;
    if (!doc || !doc.head) return;

    const css = getComputedStyle(document.documentElement);
    let style = doc.getElementById('aurea-theme');
    if (!style) {
      style = doc.createElement('style');
      style.id = 'aurea-theme';
      doc.head.appendChild(style);
    }
    style.textContent = `
      :root { --body-bg-color: ${css.getPropertyValue('--bg-canvas').trim()} !important; }
      body, #outerContainer, #mainContainer, #viewerContainer { background: ${css.getPropertyValue('--bg-canvas').trim()} !important; }
      .pdfViewer .page { border: 0 !important; box-shadow: 0 2px 10px rgba(0,0,0,.25) !important; margin: 12px auto !important; }
    `;
  } catch { }
}

function parseLatexLogAsync(logData) {
  return new Promise((resolve) => {
    const workerCode = `
      self.onmessage = function(e) {
        const log = e.data;
        const errors = [];
        const warnings = [];
        const lines = log.split('\\n');

        // O log do TeX abre "(ficheiro" quando entra num ficheiro e fecha ")"
        // quando sai. Seguindo os parênteses sabe-se em que ficheiro se está
        // em cada linha (antes usava-se o último ficheiro aberto, mesmo que
        // já tivesse sido fechado, e os erros apontavam para o ficheiro errado).
        const stack = [];
        let scanned = 0;

        function scanLine(text) {
          const re = /\\(([^\\s()]*)|\\)/g;
          let m;
          while ((m = re.exec(text)) !== null) {
            if (m[0] === ')') { stack.pop(); continue; }
            const name = m[1] || '';
            const isUserTex = /\\.tex$/i.test(name) && !name.startsWith('/');
            stack.push(isUserTex ? name.replace(/^\\.\\//, '') : null);
          }
        }

        function findCurrentFile(currentIndex) {
          for (; scanned <= currentIndex; scanned++) scanLine(lines[scanned]);
          for (let k = stack.length - 1; k >= 0; k--) {
            if (stack[k]) return stack[k];
          }
          return null;
        }

        for (let i = 0; i < lines.length; i++) {
          const line = lines[i];

          if (line.startsWith('! ')) {
            const msg = line.slice(2).trim();
            let lineNum = null, context = '';
            for (let j = i + 1; j < Math.min(i + 8, lines.length); j++) {
              const m = lines[j].match(/^l\\.(\\d+)\\s+(.*)/);
              if (m) { lineNum = parseInt(m[1]); context = m[2].trim(); break; }
            }
            errors.push({ type: 'error', msg, lineNum, file: findCurrentFile(i), context });
          }

          const warnPatterns = [
            /^LaTeX Warning: (.+)/,
            /^Package (\\w+) Warning: (.+)/,
            /^Overfull \\\\hbox (.+)/,
            /^Underfull \\\\hbox (.+)/,
          ];
          for (const pat of warnPatterns) {
            const m = line.match(pat);
            if (m) {
              const lm = line.match(/(?:on input line|line) (\\d+)/i)
                || (lines[i + 1] || '').match(/(?:on input line|line) (\\d+)/i);
              warnings.push({
                type: 'warning',
                msg : (m[1] || m[0]).trim(),
                lineNum: lm ? parseInt(lm[1]) : null,
                file: null, context: ''
              });
              break;
            }
          }
        }
        self.postMessage({ errors, warnings });
      };
    `;
    const blob = new Blob([workerCode], { type: 'application/javascript' });
    const worker = new Worker(URL.createObjectURL(blob));

    worker.onmessage = function(e) {
      resolve(e.data);
      worker.terminate();
    };

    worker.postMessage(logData);
  });
}

function renderErrorPanel(errors, warnings) {
  const list    = document.getElementById('errorList');
  const countEl = document.getElementById('errorCount');
  const total   = errors.length + warnings.length;
  countEl.textContent = total;

  if (total === 0) {
    list.innerHTML = '<div class="error-empty">✓ No errors or warnings in the log.</div>';
    return;
  }

  list.innerHTML = [...errors, ...warnings].map(e => {
    const isError = e.type === 'error';
    const icon    = isError ? '!' : '▲';
    const locText = e.lineNum
      ? `${e.file ? e.file + ' ' : ''}line ${e.lineNum}`
      : (e.file || '');
    const jumpBtn = e.lineNum
      ? `<button class="error-jump" onclick="jumpToLine(${Number(e.lineNum)},${jsAttr(e.file || '')})">
           Go to line ${Number(e.lineNum)}
         </button>` : '';
    return `
      <div class="error-item ${isError ? 'is-error' : 'is-warning'}">
        <div class="error-icon">${icon}</div>
        <div class="error-body">
          <div class="error-msg" title="${escapeHtml(e.msg)}">${escapeHtml(e.msg)}</div>
          ${locText ? `<div class="error-loc">${escapeHtml(locText)}${e.context ? ' — ' + escapeHtml(e.context.substring(0, 60)) : ''}</div>` : ''}
        </div>
        ${jumpBtn}
      </div>
    `;
  }).join('');
}

function jumpToLine(lineNum, filename) {
  if (filename) {
    const idx = files.findIndex(f => f.name === filename || f.name.endsWith('/' + filename));
    if (idx >= 0 && idx !== activeFile) {
      switchFile(idx).then(() => goToLine(lineNum));
      return;
    }
  }
  goToLine(lineNum);
}


function toggleErrorPanel() {
  const panel = document.getElementById('errorPanel');
  const hidden = panel.style.display === 'none';
  panel.style.display       = hidden ? 'flex' : 'none';
  panel.style.flexDirection = 'column';
}

function closeErrorPanel() {
  document.getElementById('errorPanel').style.display = 'none';
}

function toggleRawLog() {
  const raw = document.getElementById('rawLogPanel');
  const btn = document.getElementById('rawLogBtn');
  const show = raw.style.display === 'none';
  raw.style.display = show ? 'block' : 'none';
  btn.textContent   = show ? 'Hide Log' : 'Full Log';
}

function toggleLog() { toggleErrorPanel(); }





function getPdfViewerApp() {
    const iframe = document.getElementById('pdfPreviewIframe');
    if (iframe && iframe.contentWindow && iframe.contentWindow.PDFViewerApplication) {
        return iframe.contentWindow.PDFViewerApplication;
    }
    return null;
}

function pdfPrevPage() {
    const app = getPdfViewerApp();
    if (app && app.page > 1) app.page--;
}

function pdfNextPage() {
    const app = getPdfViewerApp();
    if (app && app.page < app.pagesCount) app.page++;
}

function pdfZoomOut() {
    const app = getPdfViewerApp();
    if (app) app.zoomOut();
}

function pdfZoomIn() {
    const app = getPdfViewerApp();
    if (app) app.zoomIn();
}