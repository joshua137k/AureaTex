const NOVEATEX_LATEX_LANG_ID = 'joshuatex-latex';


const latexCommands = [
  'documentclass','usepackage','title','author','date','thanks','today',
  'maketitle','tableofcontents','listoffigures','listoftables',
  'begin','end','chapter','section','subsection','subsubsection',
  'paragraph','subparagraph','part','appendix',
  'frontmatter','mainmatter','backmatter','include','input',
  'newpage','clearpage','cleardoublepage','pagebreak','linebreak','newline',
  'document','abstract','itemize','enumerate','description',
  'figure','table','tabular','center','flushleft','flushright',
  'quote','quotation','verbatim','minipage','equation','equation*',
  'align','align*','gather','gather*','multline','eqnarray',
  'matrix','pmatrix','bmatrix','vmatrix','Vmatrix',
  'theorem','lemma','proof','definition','corollary','remark',
  'textbf','textit','texttt','textsc','textsf','textrm','textsl',
  'emph','underline','sout','textcolor','color','colorbox',
  'uppercase','lowercase','MakeUppercase','MakeLowercase',
  'noindent','indent','hspace','vspace','hfill','vfill',
  'tiny','scriptsize','footnotesize','small','normalsize',
  'large','Large','LARGE','huge','Huge',
  'label','ref','pageref','eqref','autoref','cref',
  'cite','nocite','bibitem','bibliography','bibliographystyle',
  'addbibresource','printbibliography',
  'href','url','hypertarget','hyperlink',
  'includegraphics','caption','subcaption','centering',
  'hline','cline','multicolumn','multirow','rule','arraystretch',
  'toprule','midrule','bottomrule',
  'frac','cfrac','sqrt','sum','prod','coprod','int','iint','iiint','oint',
  'lim','liminf','limsup','max','min','inf','sup',
  'sin','cos','tan','csc','sec','cot','sinh','cosh','tanh',
  'arcsin','arccos','arctan','ln','log','exp','det','gcd',
  'partial','nabla','infty','aleph','ell','Re','Im',
  'pm','mp','times','div','ast','star','cdot','oplus','otimes',
  'cup','cap','vee','wedge','setminus','emptyset','varnothing',
  'leq','geq','equiv','sim','simeq','approx','cong','neq','propto',
  'in','notin','subset','supset','subseteq','supseteq',
  'mid','parallel','perp','forall','exists','nexists',
  'mathbb','mathcal','mathfrak','mathrm','mathbf','mathit','mathsf','text',
  'overline','underline','widehat','widetilde','vec','dot','ddot','bar','hat','tilde',
  'alpha','beta','gamma','delta','epsilon','varepsilon','zeta','eta',
  'theta','vartheta','iota','kappa','lambda','mu','nu','xi','pi','varpi',
  'rho','varrho','sigma','varsigma','tau','upsilon','phi','varphi',
  'chi','psi','omega',
  'Gamma','Delta','Theta','Lambda','Xi','Pi','Sigma','Upsilon','Phi','Psi','Omega',
  'rightarrow','leftarrow','uparrow','downarrow','leftrightarrow',
  'Rightarrow','Leftarrow','Uparrow','Downarrow','Leftrightarrow',
  'longrightarrow','longleftarrow','Longrightarrow','Longleftarrow',
  'mapsto','longmapsto','hookrightarrow','iff','implies',
  'ldots','cdots','vdots','ddots',
  'frame','frametitle','framesubtitle','pause',
  'block','alertblock','exampleblock','alert','only','onslide'
];


function registerLatexLanguage() {
  monaco.languages.register({ id: NOVEATEX_LATEX_LANG_ID });

  monaco.languages.setLanguageConfiguration(NOVEATEX_LATEX_LANG_ID, {
    comments: { lineComment: '%' },
    brackets: [['{', '}'], ['[', ']'], ['(', ')']],
    autoClosingPairs: [
      { open: '{', close: '}' },
      { open: '[', close: ']' },
      { open: '(', close: ')' },
      { open: '$', close: '$' },
    ],
  });

  monaco.languages.setMonarchTokensProvider(NOVEATEX_LATEX_LANG_ID, {
    tokenizer: {
      root: [
        [/%.*$/, 'comment'],
        [/\\[a-zA-Z]+\*?/, 'keyword'],
        [/\\./, 'keyword'],
        [/\$\$/, { token: 'string', next: '@mathblock' }],
        [/\$/, { token: 'string', next: '@mathinline' }],
        [/[{}]/, 'delimiter.curly'],
        [/[\[\]]/, 'delimiter.square'],
        [/\d+(\.\d+)?/, 'number'],
      ],
      mathinline: [
        [/\$/, { token: 'string', next: '@pop' }],
        [/\\[a-zA-Z]+/, 'keyword'],
        [/[^$\\]+/, 'string'],
      ],
      mathblock: [
        [/\$\$/, { token: 'string', next: '@pop' }],
        [/\\[a-zA-Z]+/, 'keyword'],
        [/[^$\\]+/, 'string'],
      ],
    },
  });

  monaco.editor.defineTheme('joshuatex-dark', {
    base: 'vs-dark', inherit: true,
    rules: [
      { token: 'comment', foreground: '6A9955', fontStyle: 'italic' },
      { token: 'keyword', foreground: 'F47174', fontStyle: 'bold' },
      { token: 'string', foreground: 'C4A7F5' },
      { token: 'number', foreground: 'F0B45A' },
      { token: 'delimiter.curly', foreground: '79B8FF', fontStyle: 'bold' },
      { token: 'delimiter.square', foreground: '79B8FF' },
    ],
    colors: {
      'editor.background': '#171A21',
      'editor.foreground': '#E5E9F0',
      'editorCursor.foreground': '#F05252',
      'editor.selectionBackground': '#3A2A33',
      'editor.lineHighlightBackground': '#1C2029',
      'editorLineNumber.foreground': '#4B5567',
      'editorLineNumber.activeForeground': '#F05252',
      'editorIndentGuide.background1': '#262C38',
      'editorWidget.background': '#1C2029',
      'editorWidget.border': '#2A303C',
      'editorSuggestWidget.background': '#1C2029',
      'editorSuggestWidget.border': '#2A303C',
      'editorSuggestWidget.selectedBackground': '#2A303D',
      'input.background': '#222733',
      'scrollbarSlider.background': '#3A425280',
      'scrollbarSlider.hoverBackground': '#4B5567A0',
    },
  });

  monaco.editor.defineTheme('joshuatex-light', {
    base: 'vs', inherit: true,
    rules: [
      { token: 'comment', foreground: '16A34A', fontStyle: 'italic' },
      { token: 'keyword', foreground: 'E03030', fontStyle: 'bold' },
      { token: 'string', foreground: '7C3AED' },
      { token: 'number', foreground: 'D97706' },
      { token: 'delimiter.curly', foreground: '2563EB', fontStyle: 'bold' },
      { token: 'delimiter.square', foreground: '2563EB' },
    ],
    colors: {
      'editor.background': '#FFFFFF',
      'editorCursor.foreground': '#E03030',
      'editor.selectionBackground': '#FFE8E8',
      'editorLineNumber.foreground': '#cbd5e1',
      'editorLineNumber.activeForeground': '#E03030',
      'editor.lineHighlightBackground': '#F8FAFC',
      'scrollbarSlider.background': '#CBD5E180',
    },
  });

  monaco.languages.registerCompletionItemProvider(NOVEATEX_LATEX_LANG_ID, {
    triggerCharacters: ['\\'],
    provideCompletionItems(model, position) {
      const line = model.getLineContent(position.lineNumber);
      const textUntil = line.substring(0, position.column - 1);
      const match = textUntil.match(/\\([a-zA-Z]*)$/);
      if (!match) return { suggestions: [] };
      const word = match[1];
      const range = new monaco.Range(
        position.lineNumber, position.column - word.length,
        position.lineNumber, position.column
      );
      const suggestions = latexCommands
        .filter(cmd => cmd.startsWith(word))
        .map(cmd => ({
          label: '\\' + cmd,
          kind: monaco.languages.CompletionItemKind.Function,
          insertText: cmd,
          range,
        }));
      return { suggestions };
    },
  });

  // Packages, classes, ambientes, \cite e \ref (latex-complete.js).
  if (typeof registerLatexArgCompletion === 'function') registerLatexArgCompletion(NOVEATEX_LATEX_LANG_ID);
}




function monacoThemeName() {
  return (typeof getTheme === 'function' && getTheme() === 'dark') ? 'joshuatex-dark' : 'joshuatex-light';
}

// O tema do editor acompanha o tema da aplicação.
window.addEventListener('themechange', () => {
  if (typeof monaco !== 'undefined') monaco.editor.setTheme(monacoThemeName());
  if (typeof applyPdfViewerTheme === 'function') applyPdfViewerTheme();
});

function editorUndo() { editor?.trigger('toolbar', 'undo', null); editor?.focus(); }
function editorRedo() { editor?.trigger('toolbar', 'redo', null); editor?.focus(); }

function languageForFile(filename) {
  const lower = (filename || '').toLowerCase();
  if (lower.endsWith('.bib')) return 'plaintext';
  return NOVEATEX_LATEX_LANG_ID;
}


function initEditor() {
  registerLatexLanguage();

  editor = monaco.editor.create(document.getElementById('monaco-container'), {
    model: null,
    theme: monacoThemeName(),
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 14,
    lineHeight: 1.75,
    minimap: { enabled: false },
    automaticLayout: true,
    scrollBeyondLastLine: false,
    wordWrap: 'on',
    padding: { top: 12, bottom: 12 },
    renderLineHighlight: 'line',
    smoothScrolling: true,
    overviewRulerBorder: false,
    scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10, useShadows: false },
  });

  editor.onDidChangeCursorPosition((e) => {
    document.getElementById('lineNum').textContent = `${e.position.lineNumber}:${e.position.column}`;
  });

  editor.onDidChangeModelContent(() => {
    if (collab?.applyingRemote) return;
    scheduleSave();
  });

  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => compilePDF());
  // O texto grava-se sozinho; Ctrl+S compila (como Ctrl+Enter).
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => compilePDF());
}


function wrapCmd(before, after) {
  const sel = editor.getSelection();
  const model = editor.getModel();
  const text = model.getValueInRange(sel);
  editor.executeEdits('toolbar', [{ range: sel, text: before + text + after }]);
  if (!text) {
    const pos = editor.getPosition();
    editor.setPosition({ lineNumber: pos.lineNumber, column: Math.max(1, pos.column - after.length) });
  }
  editor.focus();
}

function insertSnippet(text) {
  const pos = editor.getPosition();
  const range = new monaco.Range(pos.lineNumber, pos.column, pos.lineNumber, pos.column);
  editor.executeEdits('toolbar', [{ range, text: '\n' + text + '\n' }]);
  editor.focus();
}

async function insertTable() {
  const size = await uiForm({
    title: 'Insert table',
    okText: 'Insert',
    fields: [
      { name: 'rows', label: 'Rows', value: '3', type: 'number', min: 1, max: 100 },
      { name: 'cols', label: 'Columns', value: '3', type: 'number', min: 1, max: 30 },
    ],
    validate: (v) => {
      const r = parseInt(v.rows), c = parseInt(v.cols);
      return r >= 1 && r <= 100 && c >= 1 && c <= 30 ? '' : 'Use 1–100 rows and 1–30 columns.';
    },
  });
  if (!size) return;
  const r = parseInt(size.rows), c = parseInt(size.cols);
  const colSpec = Array(c).fill('c').join('|');
  const header = Array(c).fill('Header').join(' & ') + ' \\\\';
  const dataRow = Array(c).fill('Data').join(' & ') + ' \\\\';
  const dataRows = Array(r - 1).fill(dataRow).join('\n    ');
  insertSnippet(
    `\\begin{table}[h]\n  \\centering\n  \\begin{tabular}{|${colSpec}|}` +
    `\n    \\hline\n    ${header}\n    \\hline\n    ${dataRows}\n    \\hline` +
    `\n  \\end{tabular}\n  \\caption{Caption}\n\\end{table}`
  );
}

function toggleComment() {
  editor.getAction('editor.action.commentLine').run();
  editor.focus();
}

function findInEditor() {
  editor.getAction('actions.find').run();
}


function setViewMode(mode, btnElement) {
  const layout = document.querySelector('.editor-layout');
  layout.classList.remove('mode-code', 'mode-pdf');
  if (mode === 'code') layout.classList.add('mode-code');
  else if (mode === 'pdf') layout.classList.add('mode-pdf');

  if (btnElement) {
    document.querySelectorAll('#viewModeGroup .tbtn').forEach(b => b.classList.remove('active'));
    btnElement.classList.add('active');
  }
  setTimeout(() => editor?.layout(), 50);
}

function openPDFInNewTab() {
  if (!currentPdfUrl) {
    alert('Compile the project first to generate the PDF.');
    return;
  }

  externalPdfWindow = window.open(
    '/static/pdfjs/web/viewer.html?file=' + encodeURIComponent(currentPdfUrl) + '#view=FitH',
    'AureaTeX_PDF'
  );
  setViewMode('code', document.querySelector('#viewModeGroup button[data-mode="code"]'));
}

// Ctrl/Cmd+S em qualquer sítio da página compila em vez de abrir o
// "Guardar página" do browser.
window.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 's') {
    e.preventDefault();
    if (typeof compilePDF === 'function') compilePDF();
  }
});

let _errorHighlightDecorations = [];

function goToLine(lineNum) {
  editor.revealLineInCenter(lineNum);
  editor.setPosition({ lineNumber: lineNum, column: 1 });
  editor.focus();
  _errorHighlightDecorations = editor.deltaDecorations(_errorHighlightDecorations, [{
    range: new monaco.Range(lineNum, 1, lineNum, 1),
    options: { isWholeLine: true, className: 'error-highlight-line' },
  }]);
  setTimeout(() => {
    _errorHighlightDecorations = editor.deltaDecorations(_errorHighlightDecorations, []);
  }, 2000);
}