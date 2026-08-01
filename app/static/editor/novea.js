
(function () {
  const NOVEA_EXTENSIONS = ['.m', '.nv'];

  function isNoveaFile(filename) {
    const lower = (filename || '').toLowerCase();
    return NOVEA_EXTENSIONS.some(ext => lower.endsWith(ext));
  }


  const NOVEA_LAYOUT_KEY = `joshuatex_novea_layout_${typeof projectId !== 'undefined' ? projectId : 'default'}`;
  let noveaLayout = Object.assign(
    { height: 260, fullscreen: false, open: false },
    JSON.parse(localStorage.getItem(NOVEA_LAYOUT_KEY) || '{}')
  );

  function saveNoveaLayout() {
    localStorage.setItem(NOVEA_LAYOUT_KEY, JSON.stringify(noveaLayout));
  }


  const style = document.createElement('style');
  style.textContent = `
    #noveaBar {
      display: none; align-items: center; gap: 0.5rem;
      padding: 0.4rem 0.8rem; background: #fafafa;
      border-top: 1px solid #cbd5e1; border-bottom: 1px solid #cbd5e1;
    }
    #noveaBar.visible { display: flex; }
    #noveaRunBtn {
      background: #16A34A; color: #fff; border: none; border-radius: 6px;
      padding: 0.4rem 0.9rem; font-family: 'Space Grotesk', sans-serif;
      font-weight: 700; font-size: 0.8rem; cursor: pointer; display: inline-flex;
      align-items: center; gap: 0.4rem;
    }
    #noveaRunBtn:disabled { opacity: 0.5; cursor: default; }
    #noveaStopBtn {
      background: #E03030; color: #fff; border: none; border-radius: 6px;
      padding: 0.4rem 0.9rem; font-family: 'Space Grotesk', sans-serif;
      font-weight: 700; font-size: 0.8rem; cursor: pointer;
    }
    #noveaStopBtn:disabled { opacity: 0.4; cursor: default; }
    #noveaKernelDot {
      width: 8px; height: 8px; border-radius: 50%; background: #94a3b8; flex-shrink: 0;
    }
    #noveaKernelDot.running { background: #D97706; animation: noveaPulse 1s infinite; }
    #noveaKernelDot.idle { background: #16A34A; }
    #noveaKernelDot.error { background: #E03030; }
    @keyframes noveaPulse { 0%,100% { opacity:1 } 50% { opacity:.3 } }
    #noveaStatusText { font-size: 0.8rem; color: #555; font-weight: 600; }

    #noveaPanel {
      display: none; flex-direction: column;
      border-top: 1px solid #cbd5e1; background: #fff;
      height: 260px; min-height: 120px; flex-shrink: 0;
      position: relative;
    }
    #noveaPanel.open { display: flex; }
    #noveaPanel.fullscreen {
      position: fixed; top: 100px; left: 0; right: 0; bottom: 0;
      height: auto !important; z-index: 4000;
      box-shadow: 0 -8px 24px rgba(0,0,0,0.18);
    }

    #noveaResizeHandle {
      height: 6px; cursor: ns-resize; flex-shrink: 0;
      background: transparent; position: relative;
    }
    #noveaResizeHandle:hover { background: #FFE8E8; }
    #noveaResizeHandle::after {
      content: ''; position: absolute; left: 50%; top: 50%;
      transform: translate(-50%, -50%);
      width: 34px; height: 3px; border-radius: 2px; background: #cbd5e1;
    }
    #noveaPanel.fullscreen #noveaResizeHandle { cursor: default; }
    #noveaPanel.fullscreen #noveaResizeHandle::after { opacity: 0.3; }

    #noveaTabs { display: flex; align-items: center; border-bottom: 1px solid #cbd5e1; background: #f8fafc; flex-shrink: 0; }
    .novea-tab {
      padding: 0.4rem 1rem; font-size: 0.78rem; font-weight: 700; cursor: pointer;
      color: #64748b; border-bottom: 2px solid transparent;
    }
    .novea-tab.active { color: #E03030; border-bottom-color: #E03030; }
    #noveaFullscreenBtn {
      margin-left: auto; background: none; border: none; cursor: pointer;
      padding: 0 12px; color: #555; font-size: 0.95rem; line-height: 1;
    }
    #noveaFullscreenBtn:hover { color: var(--red); }
    .novea-view { display: none; flex: 1; overflow: auto; padding: 0.6rem 0.9rem; }
    .novea-view.active { display: block; }
    #noveaConsole { font-family: 'JetBrains Mono', monospace; font-size: 0.78rem; }
    .novea-log-line { display: flex; gap: 0.5rem; padding: 1px 0; white-space: pre-wrap; word-break: break-word; }
    .novea-log-line .ts { color: #94a3b8; flex-shrink: 0; }
    .novea-log-input .msg { color: #2563EB; }
    .novea-log-result .msg { color: #16A34A; font-weight: 700; }
    .novea-log-error .msg { color: #E03030; font-weight: 700; }
    .novea-log-system .msg { color: #888; font-style: italic; }
    #noveaWorkspace { font-family: 'JetBrains Mono', monospace; font-size: 0.78rem; }
    .novea-var-row { display: flex; gap: 0.6rem; padding: 3px 0; border-bottom: 1px solid #f0f0f0; }
    .novea-var-type {
      font-size: 0.65rem; font-weight: 700; padding: 1px 6px; border-radius: 3px;
      background: #f1f5f9; color: #475569; text-transform: uppercase; flex-shrink: 0;
    }
    .novea-var-name { font-weight: 700; color: #111; }
    .novea-var-meta { color: #888; margin-left: 0.4rem; }
    #noveaPlotArea { width: 100%; height: 100%; min-height: 200px; }
  `;
  document.head.appendChild(style);

  const bar = document.createElement('div');
  bar.id = 'noveaBar';
  bar.innerHTML = `
    <div id="noveaKernelDot" class="idle"></div>
    <span id="noveaStatusText">Kernel Nôvea ocioso</span>
    <div style="margin-left:auto; display:flex; gap:0.5rem;">
      <button id="noveaRunBtn" title="Executar script (Ctrl/Cmd + Enter)">▶ Executar</button>
      <button id="noveaStopBtn" disabled>■ Parar</button>
      <button id="noveaToggleBtn" class="btn btn-ghost" title="Mostrar/ocultar painel">Consola ▾</button>
    </div>
  `;

  const panel = document.createElement('div');
  panel.id = 'noveaPanel';
  panel.innerHTML = `
    <div id="noveaResizeHandle" title="Arrastar para redimensionar"></div>
    <div id="noveaTabs">
      <div class="novea-tab active" data-view="console">Console</div>
      <div class="novea-tab" data-view="workspace">Workspace <span id="noveaVarCount">(0)</span></div>
      <div class="novea-tab" data-view="plot">Plot</div>
      <button id="noveaFullscreenBtn" title="Fullscreen">⤢</button>
    </div>
    <div class="novea-view active" data-view="console" id="noveaConsole"></div>
    <div class="novea-view" data-view="workspace" id="noveaWorkspace">
      <div style="color:#888;">Nenhuma variável ainda. Executa um script para popular o workspace.</div>
    </div>
    <div class="novea-view" data-view="plot"><div id="noveaPlotArea"></div></div>
  `;

  function mountUI() {
    const editorPane = document.getElementById('editorPane');
    if (!editorPane) return false;
    const compileBar = document.querySelector('#editorPane .compile-bar');
    editorPane.insertBefore(bar, compileBar || null);
    editorPane.insertBefore(panel, compileBar || null);
    return true;
  }

  let ws = null;
  let running = false;
  let panelOpen = false;

  function logLine(type, message) {
    const consoleEl = document.getElementById('noveaConsole');
    const time = new Date().toLocaleTimeString('pt-PT', { hour12: false });
    const row = document.createElement('div');
    row.className = `novea-log-line novea-log-${type}`;
    row.innerHTML = `<span class="ts">${time}</span><span class="msg"></span>`;
    row.querySelector('.msg').textContent = message;
    consoleEl.appendChild(row);
    consoleEl.scrollTop = consoleEl.scrollHeight;
  }

  function setKernelState(state, label) {
    const dot = document.getElementById('noveaKernelDot');
    dot.className = state;
    document.getElementById('noveaStatusText').textContent = label;
  }

  function setRunning(isRunning) {
    running = isRunning;
    document.getElementById('noveaRunBtn').disabled = isRunning;
    document.getElementById('noveaStopBtn').disabled = !isRunning;
  }

  function renderWorkspace(scope) {
    const entries = Object.entries(scope || {}).filter(([k]) => !['pi', 'e'].includes(k));
    document.getElementById('noveaVarCount').textContent = `(${entries.length})`;
    const el = document.getElementById('noveaWorkspace');
    if (entries.length === 0) {
      el.innerHTML = `<div style="color:#888;">Nenhuma variável ainda.</div>`;
      return;
    }
    el.innerHTML = entries.map(([name, value]) => {
      let type = 'scalar', meta = String(value);
      if (Array.isArray(value)) {
        if (Array.isArray(value[0])) {
          type = 'matrix';
          meta = `[${value.length}×${value[0].length}]`;
        } else {
          type = 'vector';
          meta = `[1×${value.length}] ex: ${value.slice(0, 3).map(n => Number(n).toFixed(3)).join(', ')}…`;
        }
      }
      return `<div class="novea-var-row">
        <span class="novea-var-type">${type}</span>
        <span class="novea-var-name">${name}</span>
        <span class="novea-var-meta">${meta}</span>
      </div>`;
    }).join('');
  }

  function renderPlot(traces, layoutOpts = {}) {
    if (!window.Plotly) {
      logLine('error', 'Plotly não está carregado — adiciona o <script> do Plotly ao index.html.');
      return;
    }


    switchTab('plot');
    openPanel();

    const palette = ['#6f42e8', '#9b6bff', '#4fd1a5', '#ff6b8a'];
    const styled = traces.map((t, i) => {
      const cleanT = { ...t };
      for (const k in cleanT) {
        if (cleanT[k] === null) delete cleanT[k];
      }
      return { ...cleanT, line: { color: palette[i % palette.length], width: 2.2 } };
    });

    const container = document.getElementById('noveaPlotArea');


    Plotly.newPlot(container, styled, {
      title: layoutOpts.title ? { text: layoutOpts.title } : undefined,
      margin: { t: layoutOpts.title ? 36 : 12, r: 12, b: 32, l: 40 },
      xaxis: layoutOpts.xaxis || {}, yaxis: layoutOpts.yaxis || {},
      legend: { orientation: 'h', y: -0.2 },
    }, { responsive: true, displaylogo: false }).then(() => {
      container.dataset.hasPlot = '1';
      Plotly.Plots.resize(container);
    });
  }

  function switchTab(name) {
    document.querySelectorAll('.novea-tab').forEach(t => t.classList.toggle('active', t.dataset.view === name));
    document.querySelectorAll('.novea-view').forEach(v => v.classList.toggle('active', v.dataset.view === name));
    if (name === 'plot' && window.Plotly) {
      const el = document.getElementById('noveaPlotArea');
      if (el.dataset.hasPlot) setTimeout(() => Plotly.Plots.resize(el), 0);
    }
  }

  function ensureSocket() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws/projects/${projectId}/execute?token=${token}`);

    ws.onopen = () => setKernelState('idle', 'Kernel Nôvea ligado');
    ws.onclose = () => setKernelState('error', 'Kernel desligado — clica em Executar para religar');
    ws.onerror = () => logLine('error', 'Erro de ligação ao kernel Nôvea.');
    ws.onmessage = (ev) => handleMessage(JSON.parse(ev.data));
  }

  function handleMessage(msg) {
    switch (msg.type) {
      case 'job:started':
        setKernelState('running', 'A executar…');
        break;
      case 'job:result':
        if (msg.value !== null && msg.value !== undefined) logLine('result', `ans = ${msg.value}`);
        break;
      case 'job:plot':
        renderPlot(msg.traces, msg.layout);
        logLine('system', `plot gerado com ${msg.traces.length} série(s).`);
        break;
      case 'job:clc':
        document.getElementById('noveaConsole').innerHTML = '';
        break;
      case 'job:error':
        logLine('error', msg.message);
        setKernelState('error', 'Erro na execução');
        break;
      case 'job:done':
        setRunning(false);
        if (document.getElementById('noveaKernelDot').className !== 'error') {
          setKernelState('idle', 'Kernel ocioso');
        }
        break;
      case 'job:cancelled':
        logLine('system', 'Execução cancelada. Workspace preservado.');
        break;
      case 'workspace:update':
        renderWorkspace(msg.scope);
        break;
    }
  }

  function currentJobId() {
    return (typeof crypto !== 'undefined' && crypto.randomUUID)
      ? crypto.randomUUID()
      : Date.now().toString(36) + Math.random().toString(36).slice(2);
  }

  function runActiveScript() {
    if (typeof files === 'undefined' || activeFile < 0 || !files[activeFile]) return;
    if (!isNoveaFile(files[activeFile].name)) return;
    const code = editor.getValue();
    if (!code.trim()) { logLine('error', 'Nenhum código para executar.'); return; }

    ensureSocket();
    const jobId = currentJobId();
    logLine('input', code.trim());
    setRunning(true);
    setKernelState('running', 'A executar…');
    openPanel();

    const send = () => ws.send(JSON.stringify({ type: 'execute', jobId, code }));
    if (ws.readyState === WebSocket.OPEN) send();
    else ws.addEventListener('open', send, { once: true });
  }

  function stopActiveScript() {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'cancel', jobId: currentJobId() }));
    }
    setRunning(false);
    setKernelState('idle', 'Kernel ocioso');
  }


  function openPanel() {
    panelOpen = true;
    panel.classList.add('open');
    document.getElementById('noveaToggleBtn').textContent = 'Consola ▴';
    noveaLayout.open = true;
    saveNoveaLayout();
  }

  function closePanel() {
    panelOpen = false;
    panel.classList.remove('open');
    document.getElementById('noveaToggleBtn').textContent = 'Consola ▾';
    noveaLayout.open = false;
    saveNoveaLayout();
  }

  function togglePanel() {
    if (panelOpen) closePanel(); else openPanel();
  }

  function toggleNoveaFullscreen() {
    noveaLayout.fullscreen = !noveaLayout.fullscreen;
    panel.classList.toggle('fullscreen', noveaLayout.fullscreen);
    document.getElementById('noveaFullscreenBtn').textContent = noveaLayout.fullscreen ? '⤡' : '⤢';
    document.getElementById('noveaFullscreenBtn').title = noveaLayout.fullscreen ? 'Sair de fullscreen' : 'Fullscreen';
    saveNoveaLayout();
    if (!panelOpen) openPanel();
    setTimeout(() => {
      const el = document.getElementById('noveaPlotArea');
      if (window.Plotly && el.dataset.hasPlot) Plotly.Plots.resize(el);
    }, 220);
  }

  function setupNoveaResize() {
    const handle = document.getElementById('noveaResizeHandle');
    if (!handle) return;
    let dragging = false, startY = 0, startH = 0;

    handle.addEventListener('mousedown', e => {
      if (panel.classList.contains('fullscreen')) return;
      dragging = true; startY = e.clientY; startH = panel.offsetHeight;
      document.body.style.cursor = 'ns-resize';
      document.body.style.userSelect = 'none';
    });
    document.addEventListener('mousemove', e => {
      if (!dragging) return;
      const h = Math.min(window.innerHeight - 160, Math.max(120, startH - (e.clientY - startY)));
      panel.style.height = h + 'px';
      noveaLayout.height = h;
      const el = document.getElementById('noveaPlotArea');
      if (window.Plotly && el.dataset.hasPlot) Plotly.Plots.resize(el);
    });
    document.addEventListener('mouseup', () => {
      if (!dragging) return;
      dragging = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      saveNoveaLayout();
    });
  }

  function refreshVisibility() {
    const f = (typeof files !== 'undefined' && activeFile >= 0) ? files[activeFile] : null;
    const show = !!f && isNoveaFile(f.name);
    bar.classList.toggle('visible', show);
    if (!show) {
      panel.classList.remove('open', 'fullscreen');
      panelOpen = false;
    }
  }


  function init() {
    if (!mountUI()) { setTimeout(init, 200); return; }

    panel.style.height = noveaLayout.height + 'px';

    document.getElementById('noveaRunBtn').addEventListener('click', runActiveScript);
    document.getElementById('noveaStopBtn').addEventListener('click', stopActiveScript);
    document.getElementById('noveaToggleBtn').addEventListener('click', togglePanel);
    document.getElementById('noveaFullscreenBtn').addEventListener('click', toggleNoveaFullscreen);
    document.querySelectorAll('.novea-tab').forEach(tab => {
      tab.addEventListener('click', () => switchTab(tab.dataset.view));
    });
    setupNoveaResize();

    window.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        const f = (typeof files !== 'undefined' && activeFile >= 0) ? files[activeFile] : null;
        if (f && isNoveaFile(f.name) && !running) {
          e.preventDefault();
          runActiveScript();
        }
      }
      if (e.key === 'Escape' && noveaLayout.fullscreen) {
        toggleNoveaFullscreen();
      }
    });

    setInterval(refreshVisibility, 400);
  }

  window.addEventListener('DOMContentLoaded', init);
})();