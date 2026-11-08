// ---------------------------------------------------------------------------
// Visualizador de imagens: zoom, recorte e rotação, com gravação no projeto.
// As edições são feitas num <canvas> no browser; só "Save" envia a imagem.
// ---------------------------------------------------------------------------

const IMG_EDITABLE = /^data:image\/(png|jpe?g|webp);base64,/i;
const ZOOM_STEPS = [0.1, 0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1, 1.25, 1.5, 2, 3, 4, 6, 8];

const imgState = {
  file: null,        // entrada de `files`
  original: '',      // data-URI como está no servidor
  current: '',       // data-URI com as edições
  mime: 'image/png',
  zoom: 1,
  fit: true,
  cropping: false,
  crop: null,        // {x, y, w, h} em pixels da imagem
  drag: null,
};

const $img = (id) => document.getElementById(id);

function openImageViewer(f, isImg) {
  imgToggleCrop(false);
  imgState.file = f;
  imgState.drag = null;

  const img = $img('previewImg');
  const editable = isImg && IMG_EDITABLE.test(f.content);

  $img('imgToolbar').classList.toggle('no-image', !isImg);
  $img('imagePreview').classList.toggle('can-edit', editable && !isViewer());

  if (!isImg) {
    img.removeAttribute('src');
    $img('imgWrap').style.display = 'none';
    $img('imgEmpty').textContent = `Binary file: ${f.name} — no preview available.`;
    $img('imageInfo').textContent = '';
    return;
  }

  $img('imgWrap').style.display = '';
  $img('imgEmpty').textContent = '';
  imgState.original = imgState.current = f.content;
  imgState.mime = (f.content.match(/^data:([^;]+);/) || [])[1] || 'image/png';
  imgState.fit = true;

  img.onload = () => { if (imgState.fit) imgFit(); else _applyZoom(); _updateInfo(); };
  img.src = f.content;
  _updateDirty();
}

function _updateInfo() {
  const img = $img('previewImg');
  const f = imgState.file;
  if (!f || !img.naturalWidth) return;
  const b64 = imgState.current.split(',')[1] || '';
  const kb = Math.round(b64.length * 0.75 / 1024);
  const size = kb > 1024 ? (kb / 1024).toFixed(1) + ' MB' : kb + ' KB';
  $img('imageInfo').textContent = `${f.name} · ${img.naturalWidth}×${img.naturalHeight} px · ${size}`;
}

function _updateDirty() {
  const dirty = imgState.current !== imgState.original;
  ['imgSaveBtn', 'imgSaveCopyBtn', 'imgUndoBtn'].forEach(id => { $img(id).disabled = !dirty; });
}

// ------------------------------------------------------------------ zoom
function _applyZoom() {
  const img = $img('previewImg');
  if (!img.naturalWidth) return;
  img.style.width = Math.max(1, Math.round(img.naturalWidth * imgState.zoom)) + 'px';
  img.style.height = Math.max(1, Math.round(img.naturalHeight * imgState.zoom)) + 'px';
  $img('imgZoomLabel').textContent = Math.round(imgState.zoom * 100) + '%';
  _drawCrop();
}

function imgSetZoom(z, anchor) {
  const stage = $img('imgStage');
  const old = imgState.zoom;
  imgState.zoom = Math.min(8, Math.max(0.05, z));
  imgState.fit = false;

  // Mantém o ponto debaixo do rato no mesmo sítio.
  const ax = anchor ? anchor.x : stage.clientWidth / 2;
  const ay = anchor ? anchor.y : stage.clientHeight / 2;
  const cx = (stage.scrollLeft + ax) / old, cy = (stage.scrollTop + ay) / old;
  _applyZoom();
  stage.scrollLeft = cx * imgState.zoom - ax;
  stage.scrollTop = cy * imgState.zoom - ay;
}

function imgZoomStep(dir, anchor) {
  const z = imgState.zoom;
  const next = dir > 0 ? ZOOM_STEPS.find(s => s > z + 1e-3) : [...ZOOM_STEPS].reverse().find(s => s < z - 1e-3);
  imgSetZoom(next ?? z, anchor);
}

function imgFit() {
  const img = $img('previewImg');
  const stage = $img('imgStage');
  if (!img.naturalWidth) return;
  const pad = 48;
  const z = Math.min(1, (stage.clientWidth - pad) / img.naturalWidth, (stage.clientHeight - pad) / img.naturalHeight);
  imgState.zoom = Math.max(0.05, z);
  imgState.fit = true;
  _applyZoom();
}

$img('imgStage')?.addEventListener('wheel', (e) => {
  if (!e.ctrlKey && !e.metaKey) return;
  e.preventDefault();
  const r = $img('imgStage').getBoundingClientRect();
  imgZoomStep(e.deltaY < 0 ? 1 : -1, { x: e.clientX - r.left, y: e.clientY - r.top });
}, { passive: false });

window.addEventListener('resize', () => { if (imgState.fit && $img('imagePreview').style.display !== 'none') imgFit(); });

// ------------------------------------------------------------------ crop
function imgToggleCrop(on) {
  const want = on === undefined ? !imgState.cropping : on;
  imgState.cropping = want;
  imgState.crop = null;
  $img('imagePreview')?.classList.toggle('cropping', want);
  $img('imgCropBtn')?.classList.toggle('active', want);
  _drawCrop();
}

function _drawCrop() {
  const box = $img('cropBox');
  const c = imgState.crop;
  if (!box) return;
  const show = imgState.cropping && c && c.w > 0 && c.h > 0;
  box.style.display = show ? 'block' : 'none';
  $img('imgCropApply').disabled = !(show && c.w >= 2 && c.h >= 2);
  $img('imgCropSize').textContent = show ? `${Math.round(c.w)} × ${Math.round(c.h)} px` : '';
  if (!show) return;
  const z = imgState.zoom;
  Object.assign(box.style, { left: c.x * z + 'px', top: c.y * z + 'px', width: c.w * z + 'px', height: c.h * z + 'px' });
}

function _imgPoint(e) {
  const img = $img('previewImg');
  const r = img.getBoundingClientRect();
  return {
    x: Math.min(img.naturalWidth, Math.max(0, (e.clientX - r.left) / imgState.zoom)),
    y: Math.min(img.naturalHeight, Math.max(0, (e.clientY - r.top) / imgState.zoom)),
  };
}

$img('imgWrap')?.addEventListener('pointerdown', (e) => {
  if (!imgState.cropping || e.button !== 0) return;
  e.preventDefault();
  const p = _imgPoint(e);
  const handle = e.target.closest('.crop-h')?.dataset.h;
  const c = imgState.crop;

  if (handle && c) {
    // Canto oposto fica fixo.
    const fx = handle.includes('w') ? c.x + c.w : c.x;
    const fy = handle.includes('n') ? c.y + c.h : c.y;
    imgState.drag = { mode: 'new', ox: fx, oy: fy };
  } else if (c && e.target.closest('#cropBox')) {
    imgState.drag = { mode: 'move', dx: p.x - c.x, dy: p.y - c.y };
  } else {
    imgState.drag = { mode: 'new', ox: p.x, oy: p.y };
    imgState.crop = { x: p.x, y: p.y, w: 0, h: 0 };
  }
  e.currentTarget.setPointerCapture(e.pointerId);
});

$img('imgWrap')?.addEventListener('pointermove', (e) => {
  const d = imgState.drag;
  if (!d) return;
  const img = $img('previewImg');
  const p = _imgPoint(e);

  if (d.mode === 'new') {
    imgState.crop = {
      x: Math.min(d.ox, p.x), y: Math.min(d.oy, p.y),
      w: Math.abs(p.x - d.ox), h: Math.abs(p.y - d.oy),
    };
  } else {
    const c = imgState.crop;
    c.x = Math.min(img.naturalWidth - c.w, Math.max(0, p.x - d.dx));
    c.y = Math.min(img.naturalHeight - c.h, Math.max(0, p.y - d.dy));
  }
  _drawCrop();
});

['pointerup', 'pointercancel'].forEach(ev =>
  $img('imgWrap')?.addEventListener(ev, () => { imgState.drag = null; }));

document.addEventListener('keydown', (e) => {
  if ($img('imagePreview')?.style.display === 'none') return;
  if (e.target.closest?.('input, textarea, .monaco-editor')) return;
  if (e.key === 'Escape' && imgState.cropping) imgToggleCrop(false);
  else if (e.key === 'Enter' && imgState.cropping && !$img('imgCropApply').disabled) imgApplyCrop();
  else if ((e.key === 'c' || e.key === 'C') && !e.ctrlKey && !e.metaKey
           && $img('imagePreview').classList.contains('can-edit')) imgToggleCrop();
});

// ------------------------------------------------------------------ canvas
function _render(draw, w, h) {
  return new Promise((resolve) => {
    const src = new Image();
    src.onload = () => {
      const canvas = document.createElement('canvas');
      canvas.width = w(src);
      canvas.height = h(src);
      const ctx = canvas.getContext('2d');
      if (imgState.mime === 'image/jpeg') { ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, canvas.width, canvas.height); }
      draw(ctx, src);
      resolve(canvas.toDataURL(imgState.mime, 0.92));
    };
    src.src = imgState.current;
  });
}

async function _setCurrent(dataUrl) {
  imgState.current = dataUrl;
  const img = $img('previewImg');
  img.onload = () => { imgFit(); _updateInfo(); };
  img.src = dataUrl;
  _updateDirty();
}

async function imgApplyCrop() {
  const c = imgState.crop;
  if (!c) return;
  const x = Math.round(c.x), y = Math.round(c.y), w = Math.round(c.w), h = Math.round(c.h);
  const url = await _render((ctx, src) => ctx.drawImage(src, x, y, w, h, 0, 0, w, h), () => w, () => h);
  imgToggleCrop(false);
  await _setCurrent(url);
}

async function imgRotate() {
  const url = await _render((ctx, src) => {
    ctx.translate(src.naturalHeight, 0);
    ctx.rotate(Math.PI / 2);
    ctx.drawImage(src, 0, 0);
  }, s => s.naturalHeight, s => s.naturalWidth);
  imgToggleCrop(false);
  await _setCurrent(url);
}

function imgReset() {
  imgToggleCrop(false);
  _setCurrent(imgState.original);
}

async function imgSave(asCopy) {
  const f = imgState.file;
  if (!f) return;

  let name = f.name;
  if (asCopy) {
    const dot = name.lastIndexOf('.');
    const suggestion = dot > 0 ? `${name.slice(0, dot)}-edited${name.slice(dot)}` : name + '-edited';
    name = await uiPrompt('Name of the new image file:', suggestion, { title: 'Save as copy', okText: 'Save' });
    if (!name) return;
    name = name.trim().replace(/^\/+/, '');
    if (files.some(x => x.name === name) && !await uiConfirm(`'${name}' already exists.`, { title: 'Replace file?', okText: 'Replace', danger: true })) return;
  }

  const btn = $img(asCopy ? 'imgSaveCopyBtn' : 'imgSaveBtn');
  btn.disabled = true;

  try {
    const res = await fetch(`/api/projects/${projectId}/files/${encPath(name)}`, {
      method: 'PUT',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ content: imgState.current }),
    });
    if (handleAuthFailure(res)) return;
    if (!res.ok) throw new Error(await apiError(res, 'Could not save the image.'));

    const idx = files.findIndex(x => x.name === name);
    if (idx >= 0) files[idx].content = imgState.current;
    else files.push({ name, content: imgState.current, binary: true, is_main: false });

    if (!asCopy) imgState.original = imgState.current;
    _updateDirty();
    renderFileTree();
    showToast(asCopy ? `Saved as ${name}` : `Saved ${name}`, 'ok');
  } catch (e) {
    showToast(e.message, 'error');
    _updateDirty();
  }
}
