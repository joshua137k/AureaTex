async function compilePDF() {
  if (compiling) return;
  compiling = true;

  await saveFile();

  const dot   = document.getElementById('statusDot');
  const log   = document.getElementById('logBox');
  const btn   = document.getElementById('compileBtn');
  const panel = document.getElementById('errorPanel');

  dot.className    = 'status-dot compiling';
  log.className    = 'log-box';
  log.textContent  = 'Compiling on server...';
  panel.style.display = 'none';
  btn.innerHTML    = `<svg viewBox="0 0 24 24" style="width:13px;height:13px;stroke:white;fill:none;stroke-width:2.5">
    <circle cx="12" cy="12" r="10"/>
    <polyline points="10,15 15,12 10,9"/>
  </svg> Compiling...`;
  btn.disabled = true;

  try {
    const res = await fetch(`/api/projects/${projectId}/compile`, {
      method : 'POST',
      headers: { 'Authorization': `Bearer ${token}` }
    });

    if (res.ok) {
      const blob = await res.blob();
      if (currentPdfUrl) URL.revokeObjectURL(currentPdfUrl);
      currentPdfUrl = URL.createObjectURL(blob);

      const iframe = document.getElementById('pdfPreviewIframe');

      iframe.onload = () => {
        const viewerWindow = iframe.contentWindow;
        


        const app = viewerWindow.PDFViewerApplication;
        
        if (app && app.initializedPromise) {
            app.initializedPromise.then(() => {
                app.eventBus.on('pagechanging', (evt) => {
                    document.getElementById('pdfCurrentPage').textContent = evt.pageNumber;
                });
                app.eventBus.on('pagesinit', () => {
                    document.getElementById('pdfTotalPages').textContent = app.pagesCount;
                });
                app.eventBus.on('scalechanging', (evt) => {
                    document.getElementById('pdfZoomLabel').textContent = Math.round(evt.scale * 100) + '%';
                });
            });
        }

        viewerWindow.ondblclick = async (e) => {
          const pageEl = e.target.closest('.page');
          if (!pageEl) return;
          
          const pageNum = parseInt(pageEl.dataset.pageNumber);
          const rect = pageEl.getBoundingClientRect();
          const scale = viewerWindow.PDFViewerApplication.pdfViewer.currentScale || 1.0;
          const ptX = (e.clientX - rect.left) / scale;
          const ptY = (e.clientY - rect.top) / scale;

          console.log(`[SyncTeX] Click detected: Page ${pageNum} | X: ${ptX.toFixed(2)} | Y: ${ptY.toFixed(2)}`);

          try {
            const res = await fetch(`/api/projects/${projectId}/synctex?page=${pageNum}&x=${ptX}&y=${ptY}`, {
               headers: { 'Authorization': `Bearer ${token}` }
            });
            const data = await res.json();
            if(data.file && data.line) jumpToLine(data.line, data.file);
          } catch(err) {
            console.error("SyncTeX communication failed:", err);
          }
        };
      };

      iframe.src = '/static/pdfjs/web/viewer.html?file=' + encodeURIComponent(currentPdfUrl) + '#toolbar=0&view=FitH';

      if (externalPdfWindow && !externalPdfWindow.closed) {
        externalPdfWindow.location.replace('/static/pdfjs/web/viewer.html?file=' + encodeURIComponent(currentPdfUrl) + '#toolbar=0&view=FitH');
      }


      dot.className    = 'status-dot';
      dot.style.background = '#16A34A';
      log.className    = 'log-box success';
      log.textContent  = '✓ PDF compiled successfully! (click to see warnings)';
      setTimeout(() => dot.style.background = '', 2000);

      try {
        const logRes  = await fetch(`/api/projects/${projectId}/compile/log`, {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        const logData = await logRes.json();
        if (logData.length > 0 && logData[0].log_output) {
          const { errors, warnings } = parseLatexLogAsync(logData[0].log_output);
          currentErrors   = errors;
          currentWarnings = warnings;
          document.getElementById('rawLogContent').textContent = logData[0].log_output;
          renderErrorPanel(errors, warnings);
          if (warnings.length > 0) {
            log.textContent = `✓ Compiled with ${warnings.length} warning(s). Click to view.`;
          }
        }
      } catch { }

    } else {
      const errData = await res.json();
      const rawLog  = errData.detail?.log || JSON.stringify(errData.detail);
      const { errors, warnings } = parseLatexLogAsync(rawLog);
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
        : '! Compilation error. Click to view the log.';
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

function downloadPDF() {
  const iframe = document.getElementById('pdfPreviewIframe');
  if (iframe.src && iframe.src !== 'about:blank') {
    const a      = document.createElement('a');
    a.href       = iframe.src;
    a.download   = document.getElementById('projectTitle').value
      .replace(/[^a-z0-9]/gi, '_').toLowerCase() + '.pdf';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  } else {
    alert('Please compile the project first to generate the PDF.');
  }
}

function downloadZip() {
  const log = document.getElementById('logBox');
  log.textContent = 'Preparing ZIP...';

  fetch(`/api/projects/${projectId}/download-zip`, {
    headers: { 'Authorization': `Bearer ${token}` }
  })
  .then(res => {
    if (!res.ok) throw new Error('Error generating ZIP');
    return res.blob();
  })
  .then(blob => {
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = (document.getElementById('projectTitle').value || 'project') + '.zip';
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
  btn.innerHTML   = '<span style="font-size:12px">Sending...</span>';
  dot.className   = 'status-dot compiling';
  log.className   = 'log-box';
  log.textContent = 'Preparing synchronization with Gitea...';

  try {
    const res  = await fetch(`/api/projects/${projectId}/publish-remote`, {
      method : 'POST', headers: { 'Authorization': `Bearer ${token}` }
    });
    const data = await res.json();
    if (res.ok) {
      log.className = 'log-box success';
      log.textContent = '✓ ' + data.message;
      dot.className = 'status-dot'; dot.style.background = '#16A34A';
    } else { throw new Error(data.detail); }
  } catch (err) {
    log.className = 'log-box error';
    log.textContent = '! Gitea failure: ' + err.message;
    dot.className = 'status-dot error';
  } finally {
    btn.disabled  = false;
    btn.innerHTML = _giteaBtnHTML('Push');
  }
}

async function pullGitea() {
  const btn = document.getElementById('giteaPullBtn');
  const dot = document.getElementById('statusDot');
  const log = document.getElementById('logBox');

  btn.disabled    = true;
  btn.innerHTML   = '<span style="font-size:12px">Pulling...</span>';
  dot.className   = 'status-dot compiling';
  log.className   = 'log-box';
  log.textContent = 'Pulling changes from Gitea...';

  try {
    const res  = await fetch(`/api/projects/${projectId}/pull-remote`, {
      method: 'POST', headers: { 'Authorization': `Bearer ${token}` }
    });
    const data = await res.json();
    if (res.ok) {
      dot.className = 'status-dot'; dot.style.background = '#16A34A';
      log.className = 'log-box success';
      log.textContent = `✓ ${data.message} Updating project...`;
      
      setTimeout(() => {
        window.location.reload();
      }, 1000);

    } else { throw new Error(data.detail || 'Unknown error'); }
  } catch (err) {
    log.className   = 'log-box error';
    log.textContent = '! Gitea Pull failure: ' + err.message;
    dot.className   = 'status-dot error';
    btn.disabled  = false;
    btn.innerHTML = _giteaBtnHTML('Pull');
  } 
}

function _giteaBtnHTML(label) {
  return `<svg viewBox="0 0 24 24" stroke="#111" fill="none" stroke-width="2.5"
    style="width:13px;height:13px;margin-right:0.35rem;">
    <path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"/>
  </svg>${label}`;
}

function parseLatexLogAsync(logData) {
  return new Promise((resolve) => {
    const workerCode = `
      self.onmessage = function(e) {
        const log = e.data;
        const errors = [];
        const warnings = [];
        const lines = log.split('\\n');

        function findCurrentFile(currentIndex) {
          for (let i = currentIndex; i >= 0; i--) {
            const matches = lines[i].match(/\\(([^()]+\\.tex)/g);
            if (matches) {
              return matches[matches.length - 1].replace('(', '').replace(/^\\.\\//, '');
            }
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
    list.innerHTML = `<div style="padding:1rem;text-align:center;color:#16A34A;font-weight:700;font-size:0.85rem;">
      ✓ No errors found in the log.
    </div>`;
    return;
  }

  list.innerHTML = [...errors, ...warnings].map(e => {
    const isError = e.type === 'error';
    const icon    = isError ? '!' : '▲';
    const locText = e.lineNum
      ? `${e.file ? e.file + ' ' : ''}line ${e.lineNum}`
      : (e.file || '');
    const jumpBtn = e.lineNum
      ? `<button class="error-jump" onclick="jumpToLine(${e.lineNum},'${e.file || ''}')">
           Go to line ${e.lineNum}
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

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
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