// ---------------------------------------------------------------------------
// Diálogos próprios (em vez de alert/confirm/prompt do browser), com as cores
// do tema. Todos devolvem uma Promise:
//
//   await uiAlert('Texto')                         → undefined
//   await uiConfirm('Apagar?', { danger: true })   → true / false
//   await uiPrompt('Nome:', 'main.tex')            → texto / null
//   await uiForm({ title, fields: [{ name, label, value, type }] }) → {…} / null
//
// window.alert é substituído por uiAlert (não bloqueia; serve para avisos).
// ---------------------------------------------------------------------------
(function () {
  const CSS = `
  .uidlg-overlay {
    position: fixed; inset: 0; z-index: 10000;
    display: flex; align-items: center; justify-content: center; padding: 16px;
    background: rgba(15, 23, 42, .45);
    animation: uidlg-fade .12s ease-out;
  }
  :root[data-theme="dark"] .uidlg-overlay { background: rgba(0, 0, 0, .6); }
  .uidlg {
    width: min(440px, 100%);
    max-height: calc(100vh - 32px); overflow-y: auto;
    padding: 1.35rem 1.4rem 1.1rem;
    background: var(--bg, var(--white, #fff));
    color: var(--text, var(--black, #1e293b));
    border: 1px solid var(--border, var(--gray-border, #e2e8f0));
    border-radius: 12px;
    box-shadow: 0 20px 40px -8px rgba(15, 23, 42, .3), 0 8px 16px -8px rgba(15, 23, 42, .2);
    font-family: "Space Grotesk", system-ui, -apple-system, "Segoe UI", sans-serif;
    animation: uidlg-pop .14s ease-out;
  }
  :root[data-theme="dark"] .uidlg { border-color: #3a4252; }
  .uidlg h3 { margin: 0 0 .45rem; font-size: 1.02rem; font-weight: 800; line-height: 1.3; }
  .uidlg p { margin: 0 0 1rem; font-size: .88rem; line-height: 1.5; color: var(--text-muted, #64748b); white-space: pre-wrap; overflow-wrap: anywhere; }
  .uidlg p:only-of-type:first-child { color: inherit; }
  .uidlg label { display: block; margin: 0 0 .3rem; font-size: .72rem; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; color: var(--text-muted, #64748b); }
  .uidlg .uidlg-field { margin-bottom: .8rem; }
  .uidlg input {
    width: 100%; box-sizing: border-box; padding: .6rem .75rem;
    font: inherit; font-size: .92rem;
    color: inherit;
    background: var(--bg-muted, var(--gray2, #f1f5f9));
    border: 2px solid var(--border-strong, var(--gray-border, #cbd5e1));
    border-radius: 8px; outline: none;
  }
  :root[data-theme="dark"] .uidlg input { border-color: #3a4252; }
  .uidlg input:focus { border-color: var(--accent, var(--red, #d92d2d)); }
  .uidlg .uidlg-error { min-height: 1rem; margin: -.3rem 0 .5rem; font-size: .78rem; font-weight: 600; color: var(--danger, var(--red, #dc2626)); }
  .uidlg-actions { display: flex; justify-content: flex-end; gap: .5rem; margin-top: .4rem; }
  .uidlg-actions button {
    min-width: 84px; padding: .55rem 1rem;
    font: inherit; font-size: .85rem; font-weight: 700;
    border-radius: 8px; cursor: pointer;
    border: 1px solid var(--border, var(--gray-border, #e2e8f0));
    background: var(--bg, var(--white, #fff)); color: inherit;
  }
  .uidlg-actions button:hover { background: var(--bg-hover, var(--gray, #f8fafc)); }
  .uidlg-actions .uidlg-ok { background: var(--text, var(--black, #1e293b)); color: var(--bg, var(--white, #fff)); border-color: transparent; }
  .uidlg-actions .uidlg-ok:hover { opacity: .9; background: var(--text, var(--black, #1e293b)); }
  .uidlg-actions .uidlg-ok.danger { background: var(--danger, var(--red, #dc2626)); color: #fff; }
  .uidlg-actions button:focus-visible { outline: 2px solid var(--accent, var(--red, #d92d2d)); outline-offset: 2px; }
  @keyframes uidlg-fade { from { opacity: 0; } }
  @keyframes uidlg-pop { from { opacity: 0; transform: translateY(6px) scale(.98); } }
  `;

  function injectStyle() {
    if (document.getElementById('uidlg-style')) return;
    const s = document.createElement('style');
    s.id = 'uidlg-style';
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  // Um diálogo de cada vez; os seguintes esperam pela vez.
  let queue = Promise.resolve();

  function show(build) {
    const run = () => new Promise((resolve) => {
      injectStyle();
      const previousFocus = document.activeElement;
      const overlay = document.createElement('div');
      overlay.className = 'uidlg-overlay';
      const box = document.createElement('div');
      box.className = 'uidlg';
      box.setAttribute('role', 'dialog');
      box.setAttribute('aria-modal', 'true');
      overlay.appendChild(box);

      const done = (value) => {
        overlay.remove();
        document.removeEventListener('keydown', onKey, true);
        try { previousFocus?.focus?.(); } catch { }
        resolve(value);
      };

      const api = build(box, done);

      function onKey(e) {
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done(api.cancelValue); }
        else if (e.key === 'Tab') {
          // Mantém o foco dentro do diálogo.
          const f = [...box.querySelectorAll('input, button')];
          const i = f.indexOf(document.activeElement);
          if (e.shiftKey && i <= 0) { e.preventDefault(); f[f.length - 1].focus(); }
          else if (!e.shiftKey && i === f.length - 1) { e.preventDefault(); f[0].focus(); }
        }
      }

      overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) done(api.cancelValue); });
      document.addEventListener('keydown', onKey, true);
      document.body.appendChild(overlay);
      (box.querySelector('input') || box.querySelector('.uidlg-ok'))?.focus();
      box.querySelector('input')?.select?.();
    });

    const p = queue.then(run, run);
    queue = p.catch(() => {});
    return p;
  }

  function header(box, title, message) {
    if (title) {
      const h = document.createElement('h3');
      h.textContent = title;
      box.appendChild(h);
    }
    if (message) {
      const p = document.createElement('p');
      p.textContent = message;
      box.appendChild(p);
    }
  }

  function actions(box, { okText = 'OK', cancelText = 'Cancel', danger = false, cancel = true }) {
    const row = document.createElement('div');
    row.className = 'uidlg-actions';
    let cancelBtn = null;
    if (cancel) {
      cancelBtn = document.createElement('button');
      cancelBtn.type = 'button';
      cancelBtn.textContent = cancelText;
      row.appendChild(cancelBtn);
    }
    const ok = document.createElement('button');
    ok.type = 'submit';
    ok.className = 'uidlg-ok' + (danger ? ' danger' : '');
    ok.textContent = okText;
    row.appendChild(ok);
    box.appendChild(row);
    return { ok, cancelBtn };
  }

  window.uiAlert = function (message, opts = {}) {
    return show((box, done) => {
      header(box, opts.title || '', message);
      const { ok } = actions(box, { okText: opts.okText || 'OK', cancel: false });
      ok.addEventListener('click', () => done());
      return { cancelValue: undefined };
    });
  };

  window.uiConfirm = function (message, opts = {}) {
    return show((box, done) => {
      header(box, opts.title || '', message);
      const { ok, cancelBtn } = actions(box, {
        okText: opts.okText || (opts.danger ? 'Delete' : 'OK'),
        cancelText: opts.cancelText || 'Cancel',
        danger: !!opts.danger,
      });
      ok.addEventListener('click', () => done(true));
      cancelBtn.addEventListener('click', () => done(false));
      return { cancelValue: false };
    });
  };

  window.uiForm = function ({ title = '', message = '', fields = [], okText = 'OK', validate } = {}) {
    return show((box, done) => {
      const form = document.createElement('form');
      form.noValidate = true;
      box.appendChild(form);
      header(form, title, message);

      const inputs = fields.map((f, i) => {
        const wrap = document.createElement('div');
        wrap.className = 'uidlg-field';
        const id = `uidlg-f${Date.now()}-${i}`;
        if (f.label) {
          const l = document.createElement('label');
          l.htmlFor = id;
          l.textContent = f.label;
          wrap.appendChild(l);
        }
        const input = document.createElement('input');
        input.id = id;
        input.type = f.type || 'text';
        input.value = f.value ?? '';
        if (f.placeholder) input.placeholder = f.placeholder;
        if (f.min !== undefined) input.min = f.min;
        if (f.max !== undefined) input.max = f.max;
        input.autocomplete = 'off';
        input.spellcheck = false;
        wrap.appendChild(input);
        form.appendChild(wrap);
        return [f.name || `f${i}`, input];
      });

      const err = document.createElement('div');
      err.className = 'uidlg-error';
      form.appendChild(err);

      const { cancelBtn } = actions(form, { okText });
      cancelBtn.addEventListener('click', () => done(null));

      form.addEventListener('submit', (e) => {
        e.preventDefault();
        const values = Object.fromEntries(inputs.map(([n, el]) => [n, el.value]));
        const problem = validate ? validate(values) : '';
        if (problem) {
          err.textContent = problem;
          inputs[0]?.[1].focus();
          return;
        }
        done(values);
      });
      inputs.forEach(([, el]) => el.addEventListener('input', () => { err.textContent = ''; }));
      return { cancelValue: null };
    });
  };

  window.uiPrompt = async function (message, defaultValue = '', opts = {}) {
    const res = await window.uiForm({
      title: opts.title || message,
      message: opts.title ? message : '',
      okText: opts.okText || 'OK',
      fields: [{ name: 'v', value: defaultValue, placeholder: opts.placeholder }],
      validate: opts.validate ? (v) => opts.validate(v.v) : (v) => (v.v.trim() ? '' : 'Please fill this in.'),
    });
    return res ? res.v : null;
  };

  // Avisos simples: não bloqueiam o código, mas aparecem com o tema da app.
  window.alert = (message) => { window.uiAlert(String(message ?? '')); };
})();
