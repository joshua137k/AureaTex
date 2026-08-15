# AureaTeX

**AureaTeX** is a self-hosted web-based LaTeX editor designed for privacy, real-time collaboration, and seamless version control integration (Git). It is the ideal alternative for anyone looking for an Overleaf-like experience hosted on their own infrastructure without relying on third-party cloud services.

## Features

- **Real-Time LaTeX Compilation:** Edit code and render PDFs instantly in the browser.
- **Real-Time Collaboration:** Multiple users can edit the same document simultaneously with visible cursors, powered by WebSockets and Operational Transformation (OT).
- **Intuitive File Management:** Interactive file tree with drag-and-drop support to organize files and directories.
- **Native Gitea Integration:** 
  - OAuth2-based authentication.
  - Transparent project synchronization (Push/Pull) with private Gitea repositories.
- **Bibliography Manager:** Integration with Zotero, CrossRef, and OpenAlex to search and insert citations directly into the `refs.bib` file.
- **VS Code Sync Extension:** A dedicated VS Code extension that allows you to work locally and synchronize (Push/Pull) with the AureaTeX server with a single keystroke (F6).

---

## Technologies Used

- **Backend:** Python (FastAPI), WebSockets, JWT.
- **Database:** MariaDB.
- **Frontend:** Vanilla JS, Monaco Editor, PDF.js (browser native).
- **Version Control:** Gitea and PostgreSQL.
- **Environment:** Docker & Docker Compose.

---

## Installation (Self-Hosted)

The project consists of two components (which can run on the same host): **Gitea** (for Git management and authentication) and the **AureaTeX Application**.

### 1. Prerequisites
- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/) installed.

### 2. Start Gitea
Gitea acts as the identity provider (OAuth) and Git repository host for your projects.
1. Navigate to the Gitea directory (where its `docker-compose.yml` is located).
2. Run:
   ```bash
   docker compose up -d
   ```
3. Open Gitea in your browser (e.g., `http://localhost:3000`) and complete the initial setup.
4. Navigate to your Gitea account Settings -> **Applications** -> **Create New OAuth2 Application**.
   - **Application Name:** AureaTeX
   - **Redirect URI:** `http://localhost:8111/auth/callback` *(adjust the domain/IP according to your network setup)*.
5. Save the generated **Client ID** and **Client Secret**.

### 3. Configure and Start AureaTeX
1. In the root AureaTeX directory, open the `docker-compose.yml` file.
2. Update the following environment variables:
   - `GITEA_URL`: The URL of your Gitea instance.
   - `GITEA_CLIENT_ID`: The Client ID generated in the previous step.
   - `GITEA_CLIENT_SECRET`: The Client Secret generated in the previous step.
   - `APP_URL`: The URL where AureaTeX will be accessible.
   - `JWT_SECRET`: A secure, randomly generated string.
   - Update default passwords (`MYSQL_ROOT_PASSWORD` and `DB_PASSWORD`).
3. Start AureaTeX:
   ```bash
   docker compose up -d --build
   ```
4. Access AureaTeX in your browser (default port: `8111`).

---

## VS Code Extension (AureaTeX Sync)

If you prefer editing LaTeX locally using VS Code while taking advantage of the server's compilation and version control, the **AureaTeX Sync** extension allows you to synchronize your local project with the server using the `F6` shortcut.

### Installation Steps:
1. Ensure [Node.js](https://nodejs.org/) is installed on your computer.
2. Open a terminal inside the extension directory (`pluginVS/aureatex-sync`).
3. Install dependencies and build the package:
   ```bash
   npm install
   npm run compile
   npx @vscode/vsce package
   ```
4. A file named `aureatex-sync-0.0.1.vsix` will be generated.
5. In VS Code, open the **Extensions** tab, click the `...` menu at the top right, and select **"Install from VSIX..."**. Choose the generated file.
6. Open VS Code Settings (`Ctrl+,`), search for **AureaTeX**, and configure your credentials (server URL, Username, and Password).
7. Open your local LaTeX project folder and press **`F6`**.

---

## Security

**Important:** The default configuration files (`docker-compose.yml`) contain placeholder passwords (`change_this_password`). **Always update these credentials before exposing services to the network or internet.** It is strongly recommended to run AureaTeX and Gitea behind a Reverse Proxy (such as Nginx Proxy Manager, Traefik, or Cloudflare Tunnels) with SSL/TLS encryption enabled.

---

## License

This project was built to simplify academic and scientific writing workflows. Feel free to modify and adapt it to your requirements.