// ---------------------------------------------------------------------------
// Select próprio, com as cores do tema (a lista nativa do browser ignora o
// tema escuro). O <select> original continua no DOM, escondido: o resto do
// código lê/escreve .value e ouve "change" como antes.
//
// Uso: qualquer <select> da página é convertido; data-native="1" para não.
// ---------------------------------------------------------------------------
(function () {
  const valueDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
  const indexDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'selectedIndex');
  let openOne = null;

  function enhance(select) {
    if (select.dataset.enhanced || select.dataset.native || select.multiple) return;
    select.dataset.enhanced = '1';

    const wrap = document.createElement('div');
    wrap.className = 'xsel';
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);
    select.classList.add('xsel-native');
    select.tabIndex = -1;
    select.setAttribute('aria-hidden', 'true');

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'xsel-btn';
    btn.setAttribute('aria-haspopup', 'listbox');
    btn.setAttribute('aria-expanded', 'false');
    btn.innerHTML = '<span class="xsel-label"></span><svg viewBox="0 0 24 24" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>';
    if (select.id) {
      const label = document.querySelector(`label[for="${CSS.escape(select.id)}"]`);
      if (label) {
        if (!label.id) label.id = select.id + '-label';
        btn.setAttribute('aria-labelledby', label.id);
        label.addEventListener('click', (e) => { e.preventDefault(); btn.focus(); });
      }
    }
    wrap.appendChild(btn);

    const list = document.createElement('div');
    list.className = 'xsel-list';
    list.setAttribute('role', 'listbox');
    wrap.appendChild(list);

    let active = -1;

    const options = () => [...select.options];
    const enabledIdx = () => options().map((o, i) => (o.disabled ? -1 : i)).filter(i => i >= 0);

    function refresh() {
      const sel = select.options[indexDesc.get.call(select)];
      btn.querySelector('.xsel-label').textContent = sel ? sel.textContent : '';
      btn.disabled = select.disabled;
      btn.classList.toggle('placeholder', !!sel && sel.disabled);
    }

    function render() {
      list.innerHTML = '';
      options().forEach((o, i) => {
        const item = document.createElement('div');
        item.className = 'xsel-opt';
        item.setAttribute('role', 'option');
        item.dataset.i = i;
        item.textContent = o.textContent;
        if (o.disabled) item.setAttribute('aria-disabled', 'true');
        if (i === indexDesc.get.call(select)) item.setAttribute('aria-selected', 'true');
        list.appendChild(item);
      });
      highlight(indexDesc.get.call(select));
    }

    function highlight(i) {
      active = i;
      [...list.children].forEach((el, j) => el.classList.toggle('active', j === i));
      list.children[i]?.scrollIntoView({ block: 'nearest' });
    }

    function open() {
      if (btn.disabled) return;
      if (openOne && openOne !== close) openOne();
      render();
      wrap.classList.add('open');
      btn.setAttribute('aria-expanded', 'true');
      // Abre para cima se não couber em baixo.
      const r = btn.getBoundingClientRect();
      wrap.classList.toggle('up', window.innerHeight - r.bottom < Math.min(260, list.scrollHeight + 12) && r.top > window.innerHeight - r.bottom);
      openOne = close;
    }

    function close() {
      wrap.classList.remove('open');
      btn.setAttribute('aria-expanded', 'false');
      if (openOne === close) openOne = null;
    }

    function choose(i) {
      const o = select.options[i];
      if (!o || o.disabled) return;
      const changed = indexDesc.get.call(select) !== i;
      indexDesc.set.call(select, i);
      refresh();
      close();
      btn.focus();
      if (changed) {
        select.dispatchEvent(new Event('input', { bubbles: true }));
        select.dispatchEvent(new Event('change', { bubbles: true }));
      }
    }

    btn.addEventListener('click', () => (wrap.classList.contains('open') ? close() : open()));
    list.addEventListener('mousedown', (e) => e.preventDefault());
    list.addEventListener('click', (e) => {
      const item = e.target.closest('.xsel-opt');
      if (item) choose(Number(item.dataset.i));
    });
    list.addEventListener('mousemove', (e) => {
      const item = e.target.closest('.xsel-opt');
      if (item && Number(item.dataset.i) !== active && !item.hasAttribute('aria-disabled')) highlight(Number(item.dataset.i));
    });

    let typed = '', typedAt = 0;
    btn.addEventListener('keydown', (e) => {
      const isOpen = wrap.classList.contains('open');
      const idx = enabledIdx();
      const pos = idx.indexOf(isOpen ? active : indexDesc.get.call(select));

      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        const next = idx[Math.max(0, Math.min(idx.length - 1, pos + (e.key === 'ArrowDown' ? 1 : -1)))];
        if (isOpen) highlight(next); else choose(next);
      } else if (e.key === 'Home' || e.key === 'End') {
        e.preventDefault();
        const next = e.key === 'Home' ? idx[0] : idx[idx.length - 1];
        if (isOpen) highlight(next); else choose(next);
      } else if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        if (isOpen) choose(active); else open();
      } else if (e.key === 'Escape' && isOpen) {
        e.preventDefault();
        e.stopPropagation();
        close();
      } else if (e.key === 'Tab') {
        close();
      } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey) {
        // Escrever salta para a opção que começa por essas letras.
        typed = (Date.now() - typedAt > 700 ? '' : typed) + e.key.toLowerCase();
        typedAt = Date.now();
        const hit = idx.find(i => select.options[i].textContent.trim().toLowerCase().startsWith(typed));
        if (hit !== undefined) { if (isOpen) highlight(hit); else choose(hit); }
      }
    });

    // Valor mudado por código (select.value = ...) também atualiza o botão.
    Object.defineProperty(select, 'value', {
      configurable: true,
      get() { return valueDesc.get.call(this); },
      set(v) { valueDesc.set.call(this, v); refresh(); },
    });
    Object.defineProperty(select, 'selectedIndex', {
      configurable: true,
      get() { return indexDesc.get.call(this); },
      set(v) { indexDesc.set.call(this, v); refresh(); },
    });

    // Opções preenchidas depois (ex.: lista de repositórios do GitHub).
    new MutationObserver(() => { refresh(); if (wrap.classList.contains('open')) render(); })
      .observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'selected'] });
    select.addEventListener('change', refresh);
    select.form?.addEventListener('reset', () => setTimeout(refresh));

    refresh();
  }

  document.addEventListener('mousedown', (e) => {
    if (openOne && !e.target.closest('.xsel.open')) openOne();
  });
  window.addEventListener('resize', () => openOne && openOne());

  function enhanceAll(root = document) {
    root.querySelectorAll('select').forEach(enhance);
  }

  window.enhanceSelects = enhanceAll;

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => enhanceAll());
  else enhanceAll();
})();
