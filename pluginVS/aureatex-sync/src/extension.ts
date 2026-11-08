import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import axios, { AxiosInstance } from 'axios';
import * as mime from 'mime-types';

const SYNC_FILE = ".aurea_sync.json";
const SECRET_PASSWORD = "aureatex.password";
const SECRET_TOKEN = "aureatex.token";

const EXCLUDE_DIRS = new Set([".git", ".vscode", "__pycache__", "build", "out", "node_modules"]);
const EXCLUDE_EXTS = new Set([".aux", ".log", ".synctex.gz", ".fls", ".fdb_latexmk", ".bbl", ".blg", ".out", ".toc", ".gz", ".zip", ".bcf", ".lof", ".lot", ".nav", ".snm", ".vrb"]);
const TEXT_EXTS = new Set(['.tex', '.bib', '.cls', '.sty', '.txt', '.md', '.m', '.nv', '.gitignore', '.bst', '.bbx', '.cbx',
    '.lco', '.def', '.cfg', '.clo', '.dtx', '.ins', '.tikz', '.csv', '.json', '.yml', '.yaml', '.latexmkrc']);

// O servidor aceita ficheiros até ~10 MB (ficam em base64 na base de dados).
const MAX_FILE_BYTES = 10 * 1024 * 1024;

// O servidor corta a compilação aos 90 s; dá-se margem para a rede.
const COMPILE_TIMEOUT_MS = 120000;

interface SyncData {
    project_id: number;
    main_file: string;
    /** caminho relativo → mtime local na última sincronização */
    files: Record<string, number>;
    /** caminho relativo → updated_at do servidor na última sincronização */
    remote?: Record<string, string>;
    /** projeto acabado de criar pelo plugin (ainda com o main.tex de exemplo) */
    fresh?: boolean;
}

interface RemoteFile {
    filename: string;
    is_main: number | boolean;
    updated_at: string;
}

let output: vscode.OutputChannel;

export function activate(context: vscode.ExtensionContext) {
    output = vscode.window.createOutputChannel('AureaTeX');
    context.subscriptions.push(output);

    context.subscriptions.push(vscode.commands.registerCommand('aureatex.setPassword', async () => {
        const password = await vscode.window.showInputBox({
            prompt: 'Password do AureaTeX (fica guardada de forma segura pelo VS Code)',
            password: true,
            ignoreFocusOut: true,
        });
        if (!password) return;
        await context.secrets.store(SECRET_PASSWORD, password);
        await context.secrets.delete(SECRET_TOKEN);
        vscode.window.showInformationMessage('AureaTeX: password guardada. Podes apagar "aureatex.password" das definições.');
    }));

    context.subscriptions.push(vscode.commands.registerCommand('aureatex.logout', async () => {
        await context.secrets.delete(SECRET_PASSWORD);
        await context.secrets.delete(SECRET_TOKEN);
        vscode.window.showInformationMessage('AureaTeX: credenciais guardadas removidas.');
    }));

    context.subscriptions.push(vscode.commands.registerCommand('aureatex.sync', async () => {
        const workspaceFolders = vscode.workspace.workspaceFolders;
        if (!workspaceFolders) {
            vscode.window.showErrorMessage('Abre uma pasta (projeto) no VS Code primeiro!');
            return;
        }
        const workspacePath = workspaceFolders[0].uri.fsPath;

        const config = vscode.workspace.getConfiguration('aureatex');
        const apiUrl = (config.get<string>('apiUrl') || '').trim().replace(/\/+$/, "");
        const username = (config.get<string>('username') || '').trim();
        // Preferência: password guardada no cofre do VS Code; a das definições
        // (texto simples) continua a funcionar para não partir instalações antigas.
        const password = (await context.secrets.get(SECRET_PASSWORD)) || config.get<string>('password') || '';

        if (!apiUrl || !username) {
            vscode.window.showErrorMessage('Configura o URL e o teu Utilizador nas Definições (procura por AureaTeX).');
            vscode.commands.executeCommand('workbench.action.openSettings', 'aureatex');
            return;
        }
        if (!password) {
            const choice = await vscode.window.showErrorMessage('AureaTeX: falta a password.', 'Definir password');
            if (choice) vscode.commands.executeCommand('aureatex.setPassword');
            return;
        }

        await vscode.window.withProgress({
            location: vscode.ProgressLocation.Notification,
            title: "AureaTeX",
            cancellable: false
        }, async (progress) => {
            try {
                progress.report({ message: "A autenticar..." });
                const api = await createClient(context, apiUrl, username, password);

                const syncPath = path.join(workspacePath, SYNC_FILE);

                if (!fs.existsSync(syncPath)) {
                    await linkProject(api, workspacePath, syncPath, progress);
                    return;
                }

                let syncData: SyncData;
                try {
                    syncData = JSON.parse(fs.readFileSync(syncPath, 'utf8'));
                } catch {
                    throw new Error(`O ficheiro ${SYNC_FILE} está corrompido. Apaga-o e volta a ligar o projeto.`);
                }
                syncData.files = syncData.files || {};
                syncData.remote = syncData.remote || {};

                const action = await vscode.window.showQuickPick(
                    [
                        { label: '$(cloud-upload) Push (Enviar e Compilar)', description: 'Envia alterações locais e gera o PDF', id: 'push' },
                        { label: '$(cloud-download) Pull (Descarregar)', description: 'Descarrega ficheiros do servidor para o teu PC', id: 'pull' }
                    ],
                    { placeHolder: 'O que desejas fazer com este projeto ligado ao AureaTeX?', ignoreFocusOut: true }
                );

                if (!action) return;

                if (action.id === 'push') {
                    await handlePush(api, workspacePath, syncData, progress, syncPath);
                } else {
                    await handlePull(api, workspacePath, syncData, progress, syncPath);
                }

            } catch (err: any) {
                vscode.window.showErrorMessage(`❌ Erro AureaTeX: ${errorMessage(err)}`);
            }
        });
    }));
}

// ---------------------------------------------------------------------------
// HTTP
// ---------------------------------------------------------------------------

/** Mensagem legível a partir de um erro do axios (inclui respostas em arraybuffer). */
function errorMessage(err: any): string {
    const detail = errorDetail(err);
    if (typeof detail === 'string') return detail;
    if (detail?.error) return detail.error;
    if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg;
    if (err?.code === 'ECONNABORTED') return 'O servidor demorou demasiado tempo a responder.';
    if (err?.code === 'ECONNREFUSED' || err?.code === 'ENOTFOUND') return 'Não foi possível ligar ao servidor. Confirma o URL nas definições.';
    return err?.message || String(err);
}

function errorDetail(err: any): any {
    let data = err?.response?.data;
    if (!data) return undefined;

    // O compile é pedido como arraybuffer (PDF); os erros vêm em JSON lá dentro.
    if (data instanceof ArrayBuffer || Buffer.isBuffer(data)) {
        try {
            data = JSON.parse(Buffer.from(data as any).toString('utf8'));
        } catch {
            return undefined;
        }
    }
    return data?.detail;
}

/** Codifica cada segmento do caminho (espaços, #, acentos...). */
function encPath(relPath: string): string {
    return relPath.split('/').map(encodeURIComponent).join('/');
}

/**
 * Cliente autenticado. O token fica guardado no cofre do VS Code e só se volta
 * a fazer login quando expira (antes fazia-se login em todas as sincronizações).
 */
async function createClient(context: vscode.ExtensionContext, apiUrl: string, username: string, password: string): Promise<AxiosInstance> {
    const tokenKey = `${SECRET_TOKEN}:${apiUrl}:${username}`;

    const login = async (): Promise<string> => {
        const res = await axios.post(`${apiUrl}/api/auth/login`, { username, password }, { timeout: 20000 });
        const token = res.data.access_token as string;
        await context.secrets.store(tokenKey, token);
        return token;
    };

    let token = (await context.secrets.get(tokenKey)) || await login();

    const api = axios.create({ baseURL: apiUrl, timeout: 60000, maxBodyLength: Infinity, maxContentLength: Infinity });

    api.interceptors.request.use((cfg) => {
        cfg.headers.set('Authorization', `Bearer ${token}`);
        return cfg;
    });

    api.interceptors.response.use(undefined, async (error) => {
        const original: any = error.config;

        if (error.response?.status === 401 && original && !original._retried) {
            original._retried = true;
            token = await login();
            original.headers.set('Authorization', `Bearer ${token}`);
            return api.request(original);
        }
        throw error;
    });

    return api;
}

// ---------------------------------------------------------------------------
// Ficheiros locais
// ---------------------------------------------------------------------------

function scanLocalFiles(workspacePath: string): Map<string, number> {
    const localFiles = new Map<string, number>();

    const walk = (dir: string) => {
        for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
            if (EXCLUDE_DIRS.has(entry.name)) continue;

            const fullPath = path.join(dir, entry.name);

            if (entry.isDirectory()) {
                walk(fullPath);
            } else if (entry.isFile() && entry.name !== SYNC_FILE && !isExcluded(entry.name)) {
                const relPath = path.relative(workspacePath, fullPath).replace(/\\/g, '/');
                localFiles.set(relPath, fs.statSync(fullPath).mtimeMs);
            }
        }
    };
    walk(workspacePath);

    // Um PDF com o mesmo nome de um .tex ao lado é resultado de compilação;
    // os outros PDFs (figuras) fazem parte do projeto e são sincronizados.
    for (const rel of [...localFiles.keys()]) {
        if (rel.toLowerCase().endsWith('.pdf') && localFiles.has(rel.replace(/\.pdf$/i, '.tex'))) {
            localFiles.delete(rel);
        }
    }

    return localFiles;
}

function isExcluded(name: string): boolean {
    const lower = name.toLowerCase();
    if (lower.endsWith('.synctex.gz') || lower.endsWith('.run.xml')) return true;
    return EXCLUDE_EXTS.has(path.extname(lower));
}

function getFileContentForApi(filepath: string): string {
    const base = path.basename(filepath).toLowerCase();
    const ext = path.extname(base) || base;   // ".gitignore" não tem extensão

    if (TEXT_EXTS.has(ext)) {
        return fs.readFileSync(filepath, 'utf8');
    }

    const buffer = fs.readFileSync(filepath);
    const mimeType = mime.lookup(filepath) || 'application/octet-stream';
    return `data:${mimeType};base64,${buffer.toString('base64')}`;
}

/** Caminho local para um ficheiro do servidor, garantindo que fica dentro do projeto. */
function safeLocalPath(workspacePath: string, relPath: string): string | null {
    const full = path.resolve(workspacePath, relPath);
    const rel = path.relative(workspacePath, full);

    if (!rel || rel.startsWith('..') || path.isAbsolute(rel) || rel.split(/[\\/]/).includes('.git')) {
        return null;
    }
    return full;
}

function saveSync(syncPath: string, syncData: SyncData) {
    fs.writeFileSync(syncPath, JSON.stringify(syncData, null, 4));
}

async function listRemote(api: AxiosInstance, projectId: number): Promise<Map<string, RemoteFile>> {
    const res = await api.get(`/api/projects/${projectId}/files`);
    const map = new Map<string, RemoteFile>();
    for (const f of res.data as RemoteFile[]) map.set(f.filename, f);
    return map;
}

// ---------------------------------------------------------------------------
// Ligar pasta a um projeto
// ---------------------------------------------------------------------------

async function linkProject(api: AxiosInstance, workspacePath: string, syncPath: string, progress: vscode.Progress<{ message?: string }>) {
    const initAction = await vscode.window.showQuickPick(
        [
            { label: '$(repo-create) Criar NOVO projeto no servidor', id: 'create' },
            { label: '$(link) Ligar a um projeto EXISTENTE no servidor', id: 'link' }
        ],
        { placeHolder: `A pasta '${path.basename(workspacePath)}' não está ligada. O que desejas fazer?`, ignoreFocusOut: true }
    );

    if (!initAction) return;

    let projectId: number;
    let fresh = false;

    if (initAction.id === 'create') {
        const projName = await vscode.window.showInputBox({ prompt: 'Nome do Projeto:', value: path.basename(workspacePath), ignoreFocusOut: true });
        if (!projName) return;
        progress.report({ message: "A criar projeto..." });
        const res = await api.post(`/api/projects`, { name: projName, is_public: false });
        projectId = res.data.id;
        fresh = true;
    } else {
        progress.report({ message: "A obter projetos do servidor..." });
        const [mine, shared] = await Promise.all([
            api.get(`/api/projects`),
            api.get(`/api/shared`).catch(() => ({ data: [] })),
        ]);
        const projList = [
            ...mine.data.map((p: any) => ({ label: p.name, description: `ID: ${p.id}`, id: p.id })),
            ...shared.data.filter((p: any) => p.role === 'editor').map((p: any) => ({ label: p.name, description: `ID: ${p.id} · partilhado por ${p.owner_username}`, id: p.id })),
        ];
        if (!projList.length) {
            vscode.window.showWarningMessage('Ainda não tens projetos no servidor.');
            return;
        }
        const chosenProj = await vscode.window.showQuickPick(projList, { placeHolder: 'Escolhe o projeto online:', ignoreFocusOut: true });
        if (!chosenProj) return;
        projectId = chosenProj.id;
    }

    const texFiles = [...scanLocalFiles(workspacePath).keys()].filter(f => f.endsWith('.tex')).sort();

    let mainFile = 'main.tex';
    if (texFiles.length > 0) {
        const chosenMain = await vscode.window.showQuickPick(texFiles, { placeHolder: 'Qual é o ficheiro principal?', ignoreFocusOut: true });
        if (!chosenMain) return;
        mainFile = chosenMain;
    } else if (initAction.id === 'link') {
        // Pasta vazia ligada a um projeto existente: usa o principal do servidor.
        const remote = await listRemote(api, projectId);
        const remoteMain = [...remote.values()].find(f => f.is_main);
        if (remoteMain) mainFile = remoteMain.filename;
    } else {
        const manualMain = await vscode.window.showInputBox({ prompt: 'Qual é o nome do ficheiro principal?', value: 'main.tex', ignoreFocusOut: true });
        if (!manualMain) return;
        mainFile = manualMain;
    }

    const syncData: SyncData = { project_id: projectId, main_file: mainFile, files: {}, remote: {}, fresh };
    saveSync(syncPath, syncData);
    vscode.window.showInformationMessage('✅ Projeto ligado! Agora clica no F6 novamente para fazeres Push ou Pull.');
}

// ---------------------------------------------------------------------------
// Push
// ---------------------------------------------------------------------------

async function handlePush(api: AxiosInstance, workspacePath: string, syncData: SyncData, progress: vscode.Progress<{ message?: string }>, syncPath: string) {
    progress.report({ message: "A verificar ficheiros..." });

    const projectId = syncData.project_id;
    const mainFile = syncData.main_file;
    const remoteKnown = syncData.remote!;

    const localFiles = scanLocalFiles(workspacePath);
    let remote = await listRemote(api, projectId);

    // Ficheiros alterados no servidor (pelo editor web ou por um colaborador)
    // desde a última sincronização.
    const changedOnServer = (rel: string) => {
        const r = remote.get(rel);
        return !!r && remoteKnown[rel] !== undefined && remoteKnown[rel] !== r.updated_at;
    };

    const toSend = [...localFiles.entries()]
        .filter(([rel, mtime]) => !syncData.files[rel] || mtime > syncData.files[rel] || !remote.has(rel))
        .map(([rel]) => rel);

    const conflicts = toSend.filter(changedOnServer);
    let skip = new Set<string>();

    if (conflicts.length > 0) {
        const list = conflicts.slice(0, 6).join(', ') + (conflicts.length > 6 ? '…' : '');
        const choice = await vscode.window.showWarningMessage(
            `${conflicts.length} ficheiro(s) foram alterados no servidor desde a última sincronização: ${list}`,
            { modal: true, detail: 'Se enviares a tua versão, as alterações feitas no servidor a esses ficheiros perdem-se (ficam no histórico do editor web).' },
            'Enviar a minha versão', 'Manter a do servidor'
        );
        if (!choice) return;
        if (choice === 'Manter a do servidor') skip = new Set(conflicts);
    }

    let sent = 0;
    let deleted = 0;
    const tooBig: string[] = [];

    for (const relPath of toSend) {
        if (skip.has(relPath)) continue;

        const fullPath = path.join(workspacePath, relPath);

        if (fs.statSync(fullPath).size > MAX_FILE_BYTES) {
            tooBig.push(relPath);
            continue;
        }

        progress.report({ message: `A enviar: ${relPath}` });
        await api.put(`/api/projects/${projectId}/files/${encPath(relPath)}`, { content: getFileContentForApi(fullPath) });
        syncData.files[relPath] = localFiles.get(relPath)!;
        sent++;
    }

    for (const relPath of Object.keys(syncData.files)) {
        if (!localFiles.has(relPath)) {
            if (remote.has(relPath) && !changedOnServer(relPath)) {
                progress.report({ message: `A apagar no servidor: ${relPath}` });
                await api.delete(`/api/projects/${projectId}/files/${encPath(relPath)}`).catch(() => { });
                deleted++;
            }
            delete syncData.files[relPath];
            delete remoteKnown[relPath];
        }
    }

    // Ficheiro principal: garante que o servidor compila o mesmo que tu escolheste.
    remote = await listRemote(api, projectId);
    const remoteMain = [...remote.values()].find(f => f.is_main)?.filename;

    if (remote.has(mainFile) && remoteMain !== mainFile) {
        await api.put(`/api/projects/${projectId}/set-main/${encPath(mainFile)}`, {});
    }

    // Projeto criado pelo plugin: remove o main.tex de exemplo do servidor.
    if (syncData.fresh) {
        for (const rel of remote.keys()) {
            if (!localFiles.has(rel) && rel !== mainFile) {
                await api.delete(`/api/projects/${projectId}/files/${encPath(rel)}`).catch(() => { });
                remote.delete(rel);
            }
        }
        syncData.fresh = false;
    }

    // Regista o estado do servidor para detetar alterações feitas lá no futuro.
    remote = await listRemote(api, projectId);
    for (const [rel, f] of remote) {
        if (localFiles.has(rel) && !skip.has(rel)) remoteKnown[rel] = f.updated_at;
    }
    saveSync(syncPath, syncData);

    const onlyOnServer = [...remote.keys()].filter(rel => !localFiles.has(rel) && !isExcluded(rel));
    const newerOnServer = [...remote.keys()].filter(rel => localFiles.has(rel) && !toSend.includes(rel) && changedOnServer(rel));

    if (tooBig.length) {
        vscode.window.showWarningMessage(`Não enviados (mais de 10 MB): ${tooBig.join(', ')}`);
    }

    progress.report({ message: "A compilar na nuvem..." });

    const pdfPath = path.join(workspacePath, mainFile.replace(/\.tex$/i, '') + '.pdf');
    let compiled = false;

    try {
        const compileRes = await api.post(`/api/projects/${projectId}/compile`, {}, { responseType: 'arraybuffer', timeout: COMPILE_TIMEOUT_MS });
        fs.mkdirSync(path.dirname(pdfPath), { recursive: true });
        fs.writeFileSync(pdfPath, Buffer.from(compileRes.data));
        compiled = true;
    } catch (err: any) {
        const detail = errorDetail(err);
        const log: string = (detail && typeof detail === 'object' && detail.log) || '';

        if (!log) throw err;

        // Mostra o log do LaTeX em vez de só "Request failed with status code 400".
        output.clear();
        output.appendLine(`AureaTeX — ${detail.error || 'Erro de compilação'}`);
        output.appendLine('');
        output.appendLine(log);
        output.show(true);

        const firstError = log.split('\n').find(l => l.startsWith('! '));
        vscode.window.showErrorMessage(`❌ ${detail.error || 'Erro de compilação LaTeX.'}${firstError ? ' ' + firstError : ''} (ver painel "AureaTeX")`);
    }

    const notes: string[] = [];
    if (deleted) notes.push(`${deleted} apagado(s)`);
    if (skip.size) notes.push(`${skip.size} mantido(s) do servidor`);
    if (newerOnServer.length + onlyOnServer.length + skip.size > 0) {
        notes.push(`${newerOnServer.length + onlyOnServer.length + skip.size} mais recente(s) no servidor — faz Pull`);
    }

    if (compiled) {
        vscode.window.showInformationMessage(`✅ Sucesso! Sincronizados ${sent} ficheiros. PDF atualizado.${notes.length ? ' (' + notes.join('; ') + ')' : ''}`);
    }
}

// ---------------------------------------------------------------------------
// Pull
// ---------------------------------------------------------------------------

async function handlePull(api: AxiosInstance, workspacePath: string, syncData: SyncData, progress: vscode.Progress<{ message?: string }>, syncPath: string) {
    progress.report({ message: "A descarregar ficheiros do servidor..." });

    const projectId = syncData.project_id;
    const remoteKnown = syncData.remote!;

    const remote = await listRemote(api, projectId);
    const localFiles = scanLocalFiles(workspacePath);

    // Só se descarrega o que mudou no servidor ou não existe localmente.
    const toPull = [...remote.values()].filter(rf => {
        if (isExcluded(rf.filename)) return false;
        return !localFiles.has(rf.filename) || remoteKnown[rf.filename] !== rf.updated_at;
    });

    // Ficheiros que também foram alterados localmente desde a última sincronização.
    const locallyModified = toPull
        .map(rf => rf.filename)
        .filter(rel => localFiles.has(rel) && syncData.files[rel] !== undefined && localFiles.get(rel)! > syncData.files[rel]);

    let skip = new Set<string>();

    if (locallyModified.length > 0) {
        const list = locallyModified.slice(0, 6).join(', ') + (locallyModified.length > 6 ? '…' : '');
        const choice = await vscode.window.showWarningMessage(
            `${locallyModified.length} ficheiro(s) têm alterações locais por enviar: ${list}`,
            { modal: true, detail: 'O Pull substitui-os pela versão do servidor.' },
            'Substituir pelos do servidor', 'Manter os meus'
        );
        if (!choice) return;
        if (choice === 'Manter os meus') skip = new Set(locallyModified);
    }

    let pulled = 0;
    const rejected: string[] = [];

    for (const rf of toPull) {
        const relPath = rf.filename;
        if (skip.has(relPath)) continue;

        const fullPath = safeLocalPath(workspacePath, relPath);
        if (!fullPath) {
            rejected.push(relPath);
            continue;
        }

        progress.report({ message: `A baixar: ${relPath}` });

        const fileRes = await api.get(`/api/projects/${projectId}/files/${encPath(relPath)}`);
        const contentData: string = fileRes.data.content || '';

        fs.mkdirSync(path.dirname(fullPath), { recursive: true });

        if (contentData.startsWith('data:') && contentData.includes(';base64,')) {
            fs.writeFileSync(fullPath, Buffer.from(contentData.split(';base64,')[1], 'base64'));
        } else {
            fs.writeFileSync(fullPath, contentData, 'utf8');
        }

        syncData.files[relPath] = fs.statSync(fullPath).mtimeMs;
        // updated_at devolvido com o conteúdo: o ficheiro pode ter mudado desde a listagem.
        remoteKnown[relPath] = fileRes.data.updated_at || rf.updated_at;
        pulled++;
    }

    // Ficheiros iguais nos dois lados passam a estar registados.
    for (const rf of remote.values()) {
        if (localFiles.has(rf.filename) && remoteKnown[rf.filename] === undefined && !skip.has(rf.filename)) {
            remoteKnown[rf.filename] = rf.updated_at;
        }
    }

    // Ficheiros apagados no servidor que continuam cá e não foram alterados.
    const removedOnServer = Object.keys(syncData.files).filter(rel =>
        !remote.has(rel) && localFiles.has(rel) && remoteKnown[rel] !== undefined && localFiles.get(rel)! <= syncData.files[rel]);

    if (removedOnServer.length > 0) {
        const list = removedOnServer.slice(0, 6).join(', ') + (removedOnServer.length > 6 ? '…' : '');
        const choice = await vscode.window.showWarningMessage(
            `${removedOnServer.length} ficheiro(s) foram apagados no servidor: ${list}`,
            { modal: true }, 'Apagar também aqui', 'Manter'
        );
        if (choice === 'Apagar também aqui') {
            for (const rel of removedOnServer) {
                const full = safeLocalPath(workspacePath, rel);
                if (full && fs.existsSync(full)) fs.unlinkSync(full);
                delete syncData.files[rel];
                delete remoteKnown[rel];
            }
        }
    }

    saveSync(syncPath, syncData);

    if (rejected.length) {
        vscode.window.showWarningMessage(`Ignorados por terem um caminho inválido: ${rejected.join(', ')}`);
    }
    vscode.window.showInformationMessage(
        pulled > 0 ? `📥 Pull concluído! ${pulled} ficheiro(s) descarregado(s).` : '📥 Já estava tudo atualizado.'
    );
}

export function deactivate() { }
