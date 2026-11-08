"""Modelos iniciais de projeto."""

TEMPLATES = {
    "blank": r"""\documentclass[12pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[portuguese]{babel}
\usepackage{amsmath}

\title{Novo Documento}
\author{Autor}
\date{\today}

\begin{document}

\maketitle

\section{Introdução}
Escreve aqui o teu texto.

\end{document}""",

    "article": r"""\documentclass[12pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[portuguese]{babel}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage{hyperref}
\usepackage{geometry}
\geometry{margin=2.5cm}

\title{Título do Artigo}
\author{Primeiro Autor \and Segundo Autor}
\date{\today}

\begin{document}

\maketitle

\begin{abstract}
Resumo do artigo.
\end{abstract}

\section{Introdução}

\section{Metodologia}

\section{Resultados}

\section{Conclusão}

\bibliographystyle{plain}
\bibliography{refs}

\end{document}""",

    "thesis": r"""\documentclass[12pt,a4paper]{report}
\usepackage[utf8]{inputenc}
\usepackage[portuguese]{babel}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage{hyperref}
\usepackage{geometry}
\geometry{margin=3cm}

\title{Título da Tese}
\author{Nome do Autor}
\date{2025}

\begin{document}

\maketitle
\tableofcontents

\chapter{Introdução}

\chapter{Estado da Arte}

\chapter{Metodologia}

\chapter{Resultados e Discussão}

\chapter{Conclusão}

\bibliographystyle{plain}
\bibliography{refs}

\end{document}""",

    "beamer": r"""\documentclass{beamer}
\usepackage[utf8]{inputenc}
\usepackage[portuguese]{babel}

\title{Título da Apresentação}
\author{Autor}
\date{\today}

\begin{document}

\begin{frame}
\titlepage
\end{frame}

\begin{frame}{Introdução}
\begin{itemize}
\item Ponto 1
\item Ponto 2
\end{itemize}
\end{frame}

\end{document}""",
}
