import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import axios from 'axios';
import * as mime from 'mime-types';

const SYNC_FILE = ".aurea_sync.json";
const EXCLUDE_DIRS = new Set([".git", ".vscode", "__pycache__", "build", "out"]);
const EXCLUDE_EXTS = new Set([".pdf", ".aux", ".log", ".synctex.gz", ".fls", ".fdb_latexmk", ".bbl", ".blg", ".out", ".toc", ".gz", ".zip"]);

export function activate(context: vscode.ExtensionContext) {
    let disposable = vscode.commands.registerCommand('aureatex.sync', async () => {
        
        const workspaceFolders = vscode.workspace.workspaceFolders;
        if (!workspaceFolders) {
            vscode.window.showErrorMessage('Abre uma pasta (projeto) no VS Code primeiro!');
            return;
        }
        const workspacePath = workspaceFolders[0].uri.fsPath;

        const config = vscode.workspace.getConfiguration('aureatex');
        const apiUrl = config.get<string>('apiUrl')?.replace(/\/$/, "");
        const username = config.get<string>('username');
        const password = config.get<string>('password');

        if (!apiUrl || !username || !password) {
            vscode.window.showErrorMessage('Configura o teu Utilizador e Password nas Definições (Procura por AureaTeX).');
            vscode.commands.executeCommand('workbench.action.openSettings', 'aureatex');
            return;
        }

        vscode.window.withProgress({
            location: vscode.ProgressLocation.Notification,
            title: "AureaTeX",
            cancellable: false
        }, async (progress) => {
            try {
                progress.report({ message: "A autenticar..." });
                const authRes = await axios.post(`${apiUrl}/api/auth/login`, { username, password });
                const token = authRes.data.access_token;
                const headers = { 'Authorization': `Bearer ${token}` };

                const syncPath = path.join(workspacePath, SYNC_FILE);
                let syncData: any = null;

                if (!fs.existsSync(syncPath)) {
                    const initAction = await vscode.window.showQuickPick(
                        [
                            { label: '$(repo-create) Criar NOVO projeto no servidor', id: 'create' },
                            { label: '$(link) Ligar a um projeto EXISTENTE no servidor', id: 'link' }
                        ], 
                        { placeHolder: `A pasta '${path.basename(workspacePath)}' não está ligada. O que desejas fazer?`, ignoreFocusOut: true }
                    );

                    if (!initAction) return;

                    let projectId: number;

                    if (initAction.id === 'create') {
                        const projName = await vscode.window.showInputBox({ prompt: 'Nome do Projeto:', value: path.basename(workspacePath), ignoreFocusOut: true });
                        if (!projName) return;
                        progress.report({ message: "A criar projeto..." });
                        const res = await axios.post(`${apiUrl}/api/projects`, { name: projName, is_public: false }, { headers });
                        projectId = res.data.id;
                    } else {
                        progress.report({ message: "A obter projetos do servidor..." });
                        const res = await axios.get(`${apiUrl}/api/projects`, { headers });
                        const projList = res.data.map((p: any) => ({ label: p.name, description: `ID: ${p.id}`, id: p.id }));
                        const chosenProj = await vscode.window.showQuickPick(projList as vscode.QuickPickItem[], { placeHolder: 'Escolhe o projeto online:', ignoreFocusOut: true });
                        if (!chosenProj) return;
                        projectId = (chosenProj as any).id;
                    }

                    const texFiles: string[] = [];
                    const findTex = (dir: string) => {
                        if (!fs.existsSync(dir)) return;
                        const entries = fs.readdirSync(dir, { withFileTypes: true });
                        for (const entry of entries) {
                            if (EXCLUDE_DIRS.has(entry.name)) continue;
                            const full = path.join(dir, entry.name);
                            if (entry.isDirectory()) findTex(full);
                            else if (entry.name.endsWith('.tex')) texFiles.push(path.relative(workspacePath, full).replace(/\\/g, '/'));
                        }
                    };
                    findTex(workspacePath);

                    let mainFile = 'main.tex';
                    if (texFiles.length > 0) {
                        const chosenMain = await vscode.window.showQuickPick(texFiles, { placeHolder: 'Qual é o ficheiro principal?', ignoreFocusOut: true });
                        if (!chosenMain) return;
                        mainFile = chosenMain;
                    } else {
                        const manualMain = await vscode.window.showInputBox({ prompt: 'Qual é o nome do ficheiro principal?', value: 'main.tex', ignoreFocusOut: true });
                        if (!manualMain) return;
                        mainFile = manualMain;
                    }

                    await axios.put(`${apiUrl}/api/projects/${projectId}/set-main/${encodeURIComponent(mainFile)}`, {}, { headers }).catch(() => {});
                    
                    syncData = { project_id: projectId, main_file: mainFile, files: {} };
                    fs.writeFileSync(syncPath, JSON.stringify(syncData, null, 4));
                    vscode.window.showInformationMessage('✅ Projeto ligado! Agora clica no F6 novamente para fazeres Push ou Pull.');
                    return; 
                }

                syncData = JSON.parse(fs.readFileSync(syncPath, 'utf8'));
                const projectId = syncData.project_id;
                const mainFile = syncData.main_file;

                const action = await vscode.window.showQuickPick(
                    [
                        { label: '$(cloud-upload) Push (Enviar e Compilar)', description: 'Envia alterações locais e gera o PDF' },
                        { label: '$(cloud-download) Pull (Descarregar)', description: 'Descarrega ficheiros do servidor para o teu PC' }
                    ], 
                    { placeHolder: 'O que desejas fazer com este projeto ligado ao AureaTeX?', ignoreFocusOut: true }
                );

                if (!action) return;

                if (action.label.includes('Push')) {
                    await handlePush(workspacePath, projectId, mainFile, syncData, apiUrl, headers, progress, syncPath);
                } else {
                    await handlePull(workspacePath, projectId, syncData, apiUrl, headers, progress, syncPath);
                }

            } catch (err: any) {
                const msg = err.response?.data?.detail?.error || err.response?.data?.detail || err.message;
                vscode.window.showErrorMessage(`❌ Erro AureaTeX: ${msg}`);
            }
        });
    });

    context.subscriptions.push(disposable);
}

async function handlePush(workspacePath: string, projectId: number, mainFile: string, syncData: any, apiUrl: string, headers: any, progress: vscode.Progress<{ message?: string }>, syncPath: string) {
    progress.report({ message: "A verificar ficheiros..." });

    const localFiles = new Map<string, number>();
    const readDirRecursive = (dir: string) => {
        const entries = fs.readdirSync(dir, { withFileTypes: true });
        for (const entry of entries) {
            if (EXCLUDE_DIRS.has(entry.name)) continue;
            
            const fullPath = path.join(dir, entry.name);
            const ext = path.extname(entry.name);
            
            if (entry.isDirectory()) {
                readDirRecursive(fullPath);
            } else if (entry.isFile() && entry.name !== SYNC_FILE && !EXCLUDE_EXTS.has(ext)) {
                const relPath = path.relative(workspacePath, fullPath).replace(/\\/g, '/');
                localFiles.set(relPath, fs.statSync(fullPath).mtimeMs);
            }
        }
    };
    readDirRecursive(workspacePath);

    let sent = 0;
    let deleted = 0;

    for (const [relPath, mtime] of localFiles.entries()) {
        if (!syncData.files[relPath] || mtime > syncData.files[relPath]) {
            progress.report({ message: `A enviar: ${relPath}` });
            const content = getFileContentForApi(path.join(workspacePath, relPath));
            const safeName = encodeURIComponent(relPath);
            await axios.put(`${apiUrl}/api/projects/${projectId}/files/${safeName}`, { content }, { headers });
            syncData.files[relPath] = mtime;
            sent++;
        }
    }

    for (const relPath of Object.keys(syncData.files)) {
        if (!localFiles.has(relPath)) {
            progress.report({ message: `A apagar no servidor: ${relPath}` });
            const safeName = encodeURIComponent(relPath);
            await axios.delete(`${apiUrl}/api/projects/${projectId}/files/${safeName}`, { headers }).catch(() => {});
            delete syncData.files[relPath];
            deleted++;
        }
    }

    if (sent > 0 || deleted > 0) {
        fs.writeFileSync(syncPath, JSON.stringify(syncData, null, 4));
    }

    progress.report({ message: "A compilar na nuvem..." });
    const compileRes = await axios.post(`${apiUrl}/api/projects/${projectId}/compile`, {}, { headers, responseType: 'arraybuffer', timeout: 75000 });
    
    const pdfName = mainFile.replace('.tex', '.pdf');
    const pdfPath = path.join(workspacePath, pdfName);
    fs.writeFileSync(pdfPath, compileRes.data);

    vscode.window.showInformationMessage(`✅ Sucesso! Sincronizados ${sent} ficheiros. PDF atualizado.`);
}

async function handlePull(workspacePath: string, projectId: number, syncData: any, apiUrl: string, headers: any, progress: vscode.Progress<{ message?: string }>, syncPath: string) {
    progress.report({ message: "A descarregar ficheiros do servidor..." });

    const listRes = await axios.get(`${apiUrl}/api/projects/${projectId}/files`, { headers });
    const remoteFiles: any[] = listRes.data;

    let pulled = 0;

    for (const rf of remoteFiles) {
        const relPath = rf.filename;
        progress.report({ message: `A baixar: ${relPath}` });
        
        const safeName = encodeURIComponent(relPath);
        const fileRes = await axios.get(`${apiUrl}/api/projects/${projectId}/files/${safeName}`, { headers });
        const contentData = fileRes.data.content;
        
        const fullPath = path.join(workspacePath, relPath);
        fs.mkdirSync(path.dirname(fullPath), { recursive: true });

        if (contentData && contentData.startsWith('data:') && contentData.includes(';base64,')) {
            const base64Data = contentData.split(';base64,')[1];
            fs.writeFileSync(fullPath, Buffer.from(base64Data, 'base64'));
        } else {
            fs.writeFileSync(fullPath, contentData || '', 'utf8');
        }

        syncData.files[relPath] = fs.statSync(fullPath).mtimeMs;
        pulled++;
    }

    fs.writeFileSync(syncPath, JSON.stringify(syncData, null, 4));
    vscode.window.showInformationMessage(`📥 Pull concluído! ${pulled} ficheiros descarregados.`);
}

function getFileContentForApi(filepath: string): string {
    const ext = path.extname(filepath).toLowerCase();
    const textExtensions = ['.tex', '.bib', '.cls', '.sty', '.txt', '.md', '.m', '.nv', '.gitignore'];
    
    if (textExtensions.includes(ext)) {
        return fs.readFileSync(filepath, 'utf8');
    } else {
        const buffer = fs.readFileSync(filepath);
        const mimeType = mime.lookup(filepath) || 'application/octet-stream';
        return `data:${mimeType};base64,${buffer.toString('base64')}`;
    }
}

export function deactivate() {}