# AureaTeX 🖋️

**AureaTeX** é um editor LaTeX web *self-hosted*, desenhado para privacidade, colaboração em tempo real e integração perfeita com controlo de versões (Git). É a alternativa ideal para quem quer uma experiência semelhante ao Overleaf, mas alojada nos seus próprios servidores (sem depender de nuvens de terceiros).

## ✨ Funcionalidades

- **Compilação LaTeX em Tempo Real:** Edita o código e gera o PDF instantaneamente no browser.
- **Colaboração em Tempo Real:** Múltiplos utilizadores podem editar o mesmo documento em simultâneo (com cursores visíveis), alimentado por WebSockets e *Operational Transformation* (OT).
- **Gestão de Ficheiros Intuitiva:** File Tree interativa com suporte a Drag & Drop (arrastar e largar) para organizar ficheiros e pastas.
- **Integração Nativa com Gitea:** 
  - Login via OAuth2.
  - Sincronização de projetos (Push/Pull) com repositórios Gitea privados de forma transparente.
- **Gestor de Bibliografia:** Integração com Zotero, CrossRef e OpenAlex para pesquisar e injetar referências diretamente no ficheiro `refs.bib`.
- **VS Code Sync Extension:** Uma extensão dedicada para VS Code que te permite trabalhar no teu computador local e sincronizar (Push/Pull) com o servidor AureaTeX com o clique de um botão (F6).

---

## 🛠️ Tecnologias Utilizadas

- **Backend:** Python (FastAPI), WebSockets, JWT.
- **Base de Dados:** MariaDB.
- **Frontend:** Vanilla JS, Monaco Editor, PDF.js (nativo do browser).
- **Controlo de Versões:** Gitea e PostgreSQL.
- **Ambiente:** Docker & Docker Compose.

---

## 🚀 Como Instalar (Self-Hosted)

O projeto está dividido em duas partes (que podem correr na mesma máquina): o **Gitea** (para gestão do Git e Autenticação) e a **Aplicação AureaTeX**.

### 1. Pré-requisitos
- [Docker](https://docs.docker.com/get-docker/) e [Docker Compose](https://docs.docker.com/compose/install/) instalados.

### 2. Iniciar o Gitea
O Gitea será o fornecedor de identidade (OAuth) e o repositório Git dos teus projetos.
1. Navega para a pasta do Gitea (onde está o respetivo `docker-compose.yml`).
2. Corre o comando:
   ```bash
   docker compose up -d
   ```
3. Acede ao Gitea no browser (ex: `http://localhost:3000`) e completa a instalação inicial.
4. Vai às Definições da tua conta Gitea -> **Aplicações** -> **Criar Nova Aplicação OAuth2**.
   - **Nome da Aplicação:** AureaTeX
   - **URI de Redirecionamento:** `http://localhost:8111/auth/callback` *(ajusta o domínio/IP consoante a tua rede)*.
5. Guarda o **Client ID** e o **Client Secret**.

### 3. Configurar e Iniciar o AureaTeX
1. Na pasta principal do AureaTeX, edita o ficheiro `docker-compose.yml`.
2. Altera as seguintes variáveis de ambiente:
   - `GITEA_URL`: O URL do teu Gitea recém-criado.
   - `GITEA_CLIENT_ID`: O Client ID gerado no passo anterior.
   - `GITEA_CLIENT_SECRET`: O Client Secret gerado no passo anterior.
   - `APP_URL`: O URL onde o AureaTeX vai ficar disponível.
   - `JWT_SECRET`: Cria uma chave longa e aleatória.
   - Altera as passwords (`MYSQL_ROOT_PASSWORD` e `DB_PASSWORD`).
3. Inicia o AureaTeX:
   ```bash
   docker compose up -d --build
   ```
4. Acede ao AureaTeX no teu browser (por defeito na porta `8111`).

---

## 💻 Extensão para VS Code (AureaTeX Sync)

Prefires editar LaTeX no teu PC com o VS Code, mas usar a compilação e controlo de versões do servidor AureaTeX? 

A extensão **AureaTeX Sync** permite sincronizar o teu projeto local com o servidor através do atalho `F6`.

### Como Instalar:
1. Certifica-te que tens o [Node.js](https://nodejs.org/) instalado no teu PC.
2. Abre o terminal na pasta da extensão (`pluginVS/aureatex-sync`).
3. Instala as dependências e compila:
   ```bash
   npm install
   npm run compile
   npx @vscode/vsce package
   ```
4. Um ficheiro `aureatex-sync-0.0.1.vsix` será gerado.
5. No VS Code, vai à aba **Extensions**, clica nos `...` no topo e escolhe **"Install from VSIX..."**. Seleciona o ficheiro gerado.
6. Vai às definições do VS Code (`Ctrl+,`), pesquisa por **AureaTeX** e coloca as tuas credenciais (URL do servidor, Username e Password).
7. Abre a tua pasta de projeto LaTeX local e clica **`F6`**!

---

## 🔒 Segurança

**Atenção:** Os ficheiros de configuração base (`docker-compose.yml`) incluídos neste repositório têm senhas genéricas (`change_this_password`). **Muda sempre estas passwords antes de expores os serviços à internet.** É altamente recomendado colocar o AureaTeX e o Gitea atrás de um Reverse Proxy (como Nginx Proxy Manager, Traefik ou Cloudflare Tunnels) com HTTPS/SSL ativo.

---

## 📄 Licença

Este projeto foi criado para simplificar o fluxo de trabalho académico e científico. Sente-te à vontade para o modificar e adaptar às tuas necessidades!

Feito com ☕ e ❤️.
