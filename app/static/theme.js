// Tema claro/escuro, partilhado por todas as páginas.
// Carregado no <head> (sem defer) para aplicar o tema antes de a página aparecer.
(function () {
  var KEY = 'aureatex_theme';

  function systemTheme() {
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function stored() {
    try {
      var value = localStorage.getItem(KEY);
      return value === 'dark' || value === 'light' ? value : null;
    } catch (e) {
      return null;
    }
  }

  function apply(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    document.documentElement.style.colorScheme = theme;
  }

  var current = stored() || systemTheme();
  apply(current);

  window.getTheme = function () {
    return current;
  };

  window.setTheme = function (theme) {
    current = theme === 'dark' ? 'dark' : 'light';
    try { localStorage.setItem(KEY, current); } catch (e) { }
    apply(current);
    window.dispatchEvent(new CustomEvent('themechange', { detail: current }));
  };

  window.toggleTheme = function () {
    window.setTheme(current === 'dark' ? 'light' : 'dark');
  };

  // Sem preferência guardada, acompanha o sistema operativo.
  if (window.matchMedia) {
    var mq = window.matchMedia('(prefers-color-scheme: dark)');
    var onChange = function () {
      if (!stored()) {
        current = systemTheme();
        apply(current);
        window.dispatchEvent(new CustomEvent('themechange', { detail: current }));
      }
    };
    if (mq.addEventListener) mq.addEventListener('change', onChange);
  }

  // Mesma preferência em todos os separadores abertos.
  window.addEventListener('storage', function (e) {
    if (e.key === KEY) {
      current = stored() || systemTheme();
      apply(current);
      window.dispatchEvent(new CustomEvent('themechange', { detail: current }));
    }
  });
})();
