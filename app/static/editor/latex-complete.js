// ---------------------------------------------------------------------------
// Autocomplete LaTeX dentro de chavetas:
//   \usepackage{…}      packages (com descrição)
//   \documentclass{…}   classes
//   \begin{…}           ambientes (insere também o \end)
//   \cite{…} etc.       chaves dos ficheiros .bib do projeto
//   \ref{…}  etc.       \label{…} de todos os .tex do projeto
// ---------------------------------------------------------------------------

const LATEX_PACKAGES = [
  ['amsmath', 'Advanced math: align, gather, cases, \\text…'], ['amssymb', 'Extra math symbols (ℝ, ⊊, …)'],
  ['amsthm', 'Theorem environments (\\newtheorem, proof)'], ['mathtools', 'Fixes and extras for amsmath'],
  ['bm', 'Bold math symbols (\\bm)'], ['siunitx', 'Numbers and SI units (\\SI, \\num, \\qty)'],
  ['physics', 'Physics notation (\\dv, \\abs, \\bra, \\ket)'], ['mathrsfs', 'Script letters (\\mathscr)'],
  ['esint', 'Extra integral signs'], ['cancel', 'Strike-out terms in equations (\\cancel)'],
  ['nicefrac', 'Diagonal fractions'], ['xfrac', 'Split-level fractions (\\sfrac)'],
  ['graphicx', 'Images: \\includegraphics'], ['float', 'Force float placement with [H]'],
  ['subcaption', 'Sub-figures / sub-tables (subfigure)'], ['caption', 'Customise captions'],
  ['wrapfig', 'Text wrapping around figures'], ['tikz', 'Drawing and diagrams (TikZ/PGF)'],
  ['pgfplots', 'Plots from data or functions'], ['circuitikz', 'Electrical circuits with TikZ'],
  ['xcolor', 'Colours (\\textcolor, \\colorbox)'], ['svg', 'Include SVG images'],
  ['epstopdf', 'Convert EPS images on the fly'], ['pdfpages', 'Insert pages of external PDFs'],
  ['booktabs', 'Professional tables (\\toprule, \\midrule)'], ['tabularx', 'Tables with flexible X columns'],
  ['longtable', 'Tables that span pages'], ['multirow', 'Cells spanning rows (\\multirow)'],
  ['array', 'Extended column types for tables'], ['makecell', 'Line breaks inside table cells'],
  ['colortbl', 'Coloured table rows/cells'], ['tabulary', 'Tables with balanced column widths'],
  ['threeparttable', 'Tables with notes'], ['siunitx', 'Align numbers in tables (S columns)'],
  ['hyperref', 'Clickable links and PDF bookmarks'], ['url', 'Typeset URLs (\\url)'],
  ['cleveref', 'Smart references (\\cref → “Figure 3”)'], ['nameref', 'Reference by section name'],
  ['biblatex', 'Modern bibliography (use with biber)'], ['natbib', 'Author–year citations (\\citep, \\citet)'],
  ['cite', 'Compressed numeric citations'], ['csquotes', 'Context-sensitive quotes (\\enquote)'],
  ['babel', 'Language support (e.g. [portuguese])'], ['polyglossia', 'Language support for XeLaTeX/LuaLaTeX'],
  ['inputenc', 'Input encoding (e.g. [utf8])'], ['fontenc', 'Font encoding (e.g. [T1])'],
  ['lmodern', 'Latin Modern fonts'], ['microtype', 'Better spacing and justification'],
  ['times', 'Times font'], ['newtxtext', 'Times-like text font'], ['newtxmath', 'Times-like math font'],
  ['mathptmx', 'Times for text and math'], ['palatino', 'Palatino font'], ['helvet', 'Helvetica font'],
  ['libertine', 'Linux Libertine font'], ['sourcesanspro', 'Source Sans Pro font'], ['fontawesome5', 'Font Awesome icons'],
  ['geometry', 'Page margins and size'], ['fancyhdr', 'Custom headers and footers'],
  ['setspace', 'Line spacing (\\onehalfspacing)'], ['parskip', 'Space between paragraphs, no indent'],
  ['titlesec', 'Customise section titles'], ['titling', 'Customise \\maketitle'],
  ['tocloft', 'Customise table of contents'], ['appendix', 'Appendix environments'],
  ['multicol', 'Multiple text columns'], ['lipsum', 'Dummy text (\\lipsum)'], ['blindtext', 'Dummy text (\\blindtext)'],
  ['enumitem', 'Customise lists (itemize/enumerate)'], ['changepage', 'Adjust page width locally'],
  ['pdflscape', 'Landscape pages'], ['afterpage', 'Defer content to the next page'],
  ['placeins', '\\FloatBarrier to keep floats in a section'], ['footmisc', 'Footnote styles'],
  ['listings', 'Source code listings'], ['minted', 'Highlighted code (needs shell-escape — not available here)'],
  ['verbatim', 'Verbatim environments and comment'], ['fancyvrb', 'Fancy verbatim'],
  ['algorithm', 'Floating algorithm environment'], ['algpseudocode', 'Pseudocode (algorithmicx)'],
  ['algorithm2e', 'Algorithms with keywords'], ['tcolorbox', 'Coloured and framed boxes'],
  ['mdframed', 'Framed environments across pages'], ['framed', 'Framed/shaded text'],
  ['todonotes', 'TODO notes in the margin'], ['comment', 'Comment out large blocks'],
  ['xspace', 'Smart spaces after macros'], ['ifthen', 'Conditionals'], ['etoolbox', 'Programming tools for macros'],
  ['xparse', 'Define commands with flexible arguments'], ['calc', 'Arithmetic in lengths'],
  ['glossaries', 'Glossaries and acronyms'], ['acro', 'Acronyms'], ['nomencl', 'Nomenclature lists'],
  ['makeidx', 'Index (\\index, \\printindex)'], ['imakeidx', 'Index with automatic makeindex'],
  ['chemfig', 'Chemical structures'], ['mhchem', 'Chemical formulae (\\ce)'],
  ['qrcode', 'QR codes'], ['soul', 'Underline / highlight / strike (\\hl, \\st)'], ['ulem', 'Underline styles (\\uline)'],
  ['textcomp', 'Extra text symbols'], ['gensymb', 'Degree and other symbols (\\degree)'], ['eurosym', 'Euro sign'],
  ['marvosym', 'Assorted symbols'], ['pifont', 'Dingbats (\\ding)'], ['dirtree', 'Directory trees'],
  ['forest', 'Linguistic / generic trees'], ['smartdiagram', 'Quick diagrams'], ['standalone', 'Standalone figures'],
  ['import', 'Include files with their own relative paths'], ['subfiles', 'Compile chapters on their own'],
  ['refcount', 'Use reference numbers in computations'], ['lastpage', 'Total page count (\\pageref{LastPage})'],
  ['datetime2', 'Dates and times'], ['background', 'Page backgrounds / watermarks'], ['draftwatermark', 'Watermark text'],
  ['adjustbox', 'Scale/clip/frame boxes'], ['rotating', 'Rotate floats (sidewaysfigure)'],
  ['beamerthemesplit', 'Beamer split theme'], ['pgfgantt', 'Gantt charts'], ['csvsimple', 'Read CSV into tables'],
  ['pgfplotstable', 'Tables from data files'], ['xurl', 'Break URLs anywhere'], ['bookmark', 'Better PDF bookmarks'],
  ['authblk', 'Authors with affiliations'], ['abstract', 'Customise the abstract'], ['epigraph', 'Quotes at chapter starts'],
];

const LATEX_CLASSES = [
  ['article', 'Articles, short reports'], ['report', 'Longer reports with chapters'], ['book', 'Books'],
  ['memoir', 'Flexible book/report class'], ['beamer', 'Slides / presentations'], ['letter', 'Letters'],
  ['scrartcl', 'KOMA-Script article'], ['scrreprt', 'KOMA-Script report'], ['scrbook', 'KOMA-Script book'],
  ['amsart', 'AMS journal article'], ['IEEEtran', 'IEEE papers'], ['elsarticle', 'Elsevier journals'],
  ['revtex4-2', 'APS / AIP physics journals'], ['llncs', 'Springer LNCS'], ['acmart', 'ACM papers'],
  ['moderncv', 'CV / résumé'], ['standalone', 'Single figure / diagram'], ['tufte-handout', 'Tufte-style handout'],
  ['exam', 'Exams and quizzes'], ['minimal', 'Minimal class for testing'],
];

const LATEX_ENVS = [
  'document', 'abstract', 'itemize', 'enumerate', 'description', 'figure', 'figure*', 'table', 'table*',
  'tabular', 'tabularx', 'longtable', 'center', 'flushleft', 'flushright', 'quote', 'quotation', 'verbatim',
  'equation', 'equation*', 'align', 'align*', 'gather', 'gather*', 'multline', 'multline*', 'split', 'cases',
  'matrix', 'pmatrix', 'bmatrix', 'vmatrix', 'array', 'theorem', 'lemma', 'proof', 'definition', 'corollary',
  'minipage', 'subfigure', 'tikzpicture', 'axis', 'lstlisting', 'algorithm', 'algorithmic', 'frame', 'columns',
  'column', 'block', 'thebibliography', 'appendices', 'titlepage', 'multicols', 'tcolorbox', 'landscape',
];

const CITE_CMDS = /\\(?:cite|citep|citet|citeauthor|citeyear|parencite|textcite|autocite|footcite|nocite|fullcite|Cite|Parencite|Textcite)\*?(?:\[[^\]]*\]){0,2}\{([^}]*)$/;
const REF_CMDS = /\\(?:ref|eqref|pageref|autoref|cref|Cref|nameref|vref)\*?\{([^}]*)$/;

// Lê ficheiros do projeto para chaves/labels (com cache curta).
const _projCache = { at: 0, data: null };

async function _projectSymbols() {
  if (_projCache.data && Date.now() - _projCache.at < 20000) return _projCache.data;

  const keys = new Map(), labels = new Set();
  const wanted = files.filter(f => !f.binary && /\.(bib|tex)$/i.test(f.name)).slice(0, 80);

  await Promise.all(wanted.map(async f => {
    let text = openModels.get(f.name)?.getValue();
    if (text === undefined) {
      try {
        const res = await fetch(`/api/projects/${projectId}/files/${encPath(f.name)}`, { headers: authHeaders() });
        text = res.ok ? ((await res.json()).content || '') : '';
      } catch { text = ''; }
    }
    if (/\.bib$/i.test(f.name)) {
      for (const m of text.matchAll(/@(\w+)\s*\{\s*([^,\s{}]+)\s*,([\s\S]*?)(?=\n\s*@|$)/g)) {
        if (/^(string|comment|preamble)$/i.test(m[1])) continue;
        const title = (m[3].match(/title\s*=\s*[{"]+([^}"]+)/i) || [])[1] || '';
        const author = (m[3].match(/author\s*=\s*[{"]+([^}"]+)/i) || [])[1] || '';
        keys.set(m[2], `${author.split(' and ')[0]}${author ? ' — ' : ''}${title}`.trim());
      }
    } else {
      for (const m of text.matchAll(/\\label\{([^}]+)\}/g)) labels.add(m[1]);
    }
  }));

  _projCache.data = { keys, labels };
  _projCache.at = Date.now();
  return _projCache.data;
}

function registerLatexArgCompletion(langId) {
  const K = monaco.languages.CompletionItemKind;

  monaco.languages.registerCompletionItemProvider(langId, {
    triggerCharacters: ['{', ','],

    async provideCompletionItems(model, position) {
      const before = model.getLineContent(position.lineNumber).substring(0, position.column - 1);

      // Palavra atual dentro das chavetas (depois de { ou ,).
      const word = (before.match(/[^{,\s]*$/) || [''])[0];
      const range = new monaco.Range(position.lineNumber, position.column - word.length, position.lineNumber, position.column);

      if (/\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\{[^}]*$/.test(before)) {
        const seen = new Set();
        return {
          suggestions: LATEX_PACKAGES.filter(([n]) => !seen.has(n) && seen.add(n)).map(([name, doc], i) => ({
            label: { label: name, description: doc },
            kind: K.Module, insertText: name, documentation: doc, range, sortText: String(i).padStart(4, '0'),
          })),
        };
      }

      if (/\\documentclass(?:\[[^\]]*\])?\{[^}]*$/.test(before)) {
        return {
          suggestions: LATEX_CLASSES.map(([name, doc]) => ({
            label: { label: name, description: doc }, kind: K.Class, insertText: name, documentation: doc, range,
          })),
        };
      }

      if (/\\begin\{[^}]*$/.test(before)) {
        // Fecha a chaveta (se já não estiver fechada) e acrescenta o \end.
        const after = model.getLineContent(position.lineNumber).substring(position.column - 1);
        const closed = after.startsWith('}');
        const indent = (model.getLineContent(position.lineNumber).match(/^\s*/) || [''])[0];
        return {
          suggestions: LATEX_ENVS.map(env => ({
            label: env, kind: K.Snippet,
            insertText: closed ? env : `${env}}\n${indent}\t$0\n${indent}\\end{${env}}`,
            insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
            documentation: `\\begin{${env}} … \\end{${env}}`, range,
          })),
        };
      }

      if (CITE_CMDS.test(before)) {
        const { keys } = await _projectSymbols();
        return {
          suggestions: [...keys].map(([key, doc]) => ({
            label: { label: key, description: doc }, kind: K.Reference, insertText: key, documentation: doc, range,
          })),
        };
      }

      if (REF_CMDS.test(before)) {
        const { labels } = await _projectSymbols();
        return {
          suggestions: [...labels].map(l => ({ label: l, kind: K.Reference, insertText: l, range })),
        };
      }

      return { suggestions: [] };
    },
  });
}
