
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
  log.textContent  = 'A compilar no servidor...';
  panel.style.display = 'none';
  btn.innerHTML    = `<svg viewBox="0 0 24 24" style="width:13px;height:13px;stroke:white;fill:none;stroke-width:2.5">
    <circle cx="12" cy="12" r="10"/>
    <polyline points="10,15 15,12 10,9"/>
  </svg> Compilando...`;
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

      document.getElementById('pdfPreviewIframe').src = currentPdfUrl + '#toolbar=0&view=FitH';
      if (externalPdfWindow && !externalPdfWindow.closed) {
        externalPdfWindow.location.replace(currentPdfUrl);
      }

      dot.className    = 'status-dot';
      dot.style.background = '#16A34A';
      log.className    = 'log-box success';
      log.textContent  = '✓ PDF compilado com sucesso! (clica para ver warnings)';
      setTimeout(() => dot.style.background = '', 2000);

      try {
        const logRes  = await fetch(`/api/projects/${projectId}/compile/log`, {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        const logData = await logRes.json();
        if (logData.length > 0 && logData[0].log_output) {
          const { errors, warnings } = parseLatexLog(logData[0].log_output);
          currentErrors   = errors;
          currentWarnings = warnings;
          document.getElementById('rawLogContent').textContent = logData[0].log_output;
          renderErrorPanel(errors, warnings);
          if (warnings.length > 0) {
            log.textContent = `✓ Compilado com ${warnings.length} warning(s). Clica para ver.`;
          }
        }
      } catch { }

    } else {
      const errData = await res.json();
      const rawLog  = errData.detail?.log || JSON.stringify(errData.detail);
      const { errors, warnings } = parseLatexLog(rawLog);
      currentErrors   = errors;
      currentWarnings = warnings;

      document.getElementById('rawLogContent').textContent = rawLog;
      renderErrorPanel(errors, warnings);

      panel.style.display       = 'flex';
      panel.style.flexDirection = 'column';

      dot.className   = 'status-dot error';
      log.className   = 'log-box error';
      log.textContent = errors.length > 0
        ? `! ${errors.length} erro(s) de compilação. Clica para ver.`
        : '! Erro de compilação. Clica para ver o log.';
    }

  } catch {
    log.textContent = '! Erro de ligação ao servidor.';
    dot.className   = 'status-dot error';
  } finally {
    btn.disabled  = false;
    btn.innerHTML = '<svg viewBox="0 0 24 24"><polygon points="5,3 19,12 5,21"/></svg> Compilar';
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
    alert('Por favor, compila o projeto primeiro para gerar o PDF.');
  }
}

function downloadZip() {
  const log = document.getElementById('logBox');
  log.textContent = 'A preparar ZIP...';

  fetch(`/api/projects/${projectId}/download-zip`, {
    headers: { 'Authorization': `Bearer ${token}` }
  })
  .then(res => {
    if (!res.ok) throw new Error('Erro ao gerar ZIP');
    return res.blob();
  })
  .then(blob => {
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = (document.getElementById('projectTitle').value || 'projeto') + '.zip';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    log.textContent = '✓ ZIP descarregado.';
  })
  .catch(err => { log.textContent = '! Erro ao gerar ZIP: ' + err.message; });
}

async function publishGitea() {
  await saveFile();
  const btn = document.getElementById('giteaBtn');
  const dot = document.getElementById('statusDot');
  const log = document.getElementById('logBox');

  btn.disabled    = true;
  btn.innerHTML   = '<span style="font-size:12px">A enviar...</span>';
  dot.className   = 'status-dot compiling';
  log.className   = 'log-box';
  log.textContent = 'A preparar sincronização com o Gitea...';

  try {
    const res  = await fetch(`/api/projects/${projectId}/publish-gitea`, {
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
    log.textContent = '! Falha Gitea: ' + err.message;
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
  btn.innerHTML   = '<span style="font-size:12px">A puxar...</span>';
  dot.className   = 'status-dot compiling';
  log.className   = 'log-box';
  log.textContent = 'A puxar alterações do Gitea...';

  try {
    const res  = await fetch(`/api/projects/${projectId}/pull-gitea`, {
      method: 'POST', headers: { 'Authorization': `Bearer ${token}` }
    });
    const data = await res.json();
    if (res.ok) {
      dot.className = 'status-dot'; dot.style.background = '#16A34A';
      log.className = 'log-box success';
      log.textContent = `✓ ${data.message} A atualizar o projeto...`;
      
      setTimeout(() => {
        window.location.reload();
      }, 1000);

    } else { throw new Error(data.detail || 'Erro desconhecido'); }
  } catch (err) {
    log.className   = 'log-box error';
    log.textContent = '! Falha Pull Gitea: ' + err.message;
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

function parseLatexLog(log) {
  const errors   = [];
  const warnings = [];
  const lines    = log.split('\n');

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    if (line.startsWith('! ')) {
      const msg = line.slice(2).trim();
      let lineNum = null, context = '';
      for (let j = i + 1; j < Math.min(i + 8, lines.length); j++) {
        const m = lines[j].match(/^l\.(\d+)\s+(.*)/);
        if (m) { lineNum = parseInt(m[1]); context = m[2].trim(); break; }
      }
      errors.push({ type: 'error', msg, lineNum, file: findCurrentFile(lines, i), context });
    }

    const warnPatterns = [
      /^LaTeX Warning: (.+)/,
      /^Package (\w+) Warning: (.+)/,
      /^Overfull \\hbox (.+)/,
      /^Underfull \\hbox (.+)/,
    ];
    for (const pat of warnPatterns) {
      const m = line.match(pat);
      if (m) {
        const lm = line.match(/(?:on input line|line) (\d+)/i)
          || (lines[i + 1] || '').match(/(?:on input line|line) (\d+)/i);
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
  return { errors, warnings };
}

function findCurrentFile(lines, currentIndex) {
  for (let i = currentIndex; i >= 0; i--) {
    const matches = lines[i].match(/\(([^()]+\.tex)/g);
    if (matches) {
      return matches[matches.length - 1].replace('(', '').replace(/^\.\//, '');
    }
  }
  return null;
}

function renderErrorPanel(errors, warnings) {
  const list    = document.getElementById('errorList');
  const countEl = document.getElementById('errorCount');
  const total   = errors.length + warnings.length;
  countEl.textContent = total;

  if (total === 0) {
    list.innerHTML = `<div style="padding:1rem;text-align:center;color:#16A34A;font-weight:700;font-size:0.85rem;">
      ✓ Nenhum erro encontrado no log.
    </div>`;
    return;
  }

  list.innerHTML = [...errors, ...warnings].map(e => {
    const isError = e.type === 'error';
    const icon    = isError ? '!' : '▲';
    const locText = e.lineNum
      ? `${e.file ? e.file + ' ' : ''}linha ${e.lineNum}`
      : (e.file || '');
    const jumpBtn = e.lineNum
      ? `<button class="error-jump" onclick="jumpToLine(${e.lineNum},'${e.file || ''}')">
           Ir para linha ${e.lineNum}
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

function goToLine(lineNum) {
  const line = Math.max(0, lineNum - 1);
  editor.setCursor({ line, ch: 0 });
  editor.scrollIntoView({ line, ch: 0 }, 100);
  editor.focus();
  const marker = editor.addLineClass(line, 'background', 'error-highlight');
  setTimeout(() => editor.removeLineClass(line, 'background', 'error-highlight'), 2000);
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
  btn.textContent   = show ? 'Ocultar Log' : 'Log Completo';
}

function toggleLog() { toggleErrorPanel(); }