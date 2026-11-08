
const token     = localStorage.getItem('Joshuatex_token');
const urlParams = new URLSearchParams(window.location.search);
const projectId = urlParams.get('id');

if (!token)     window.location.href = '/';
if (!projectId) window.location.href = '/app/projects';

// ---------------------------------------------------------------------------
// Helpers partilhados
// ---------------------------------------------------------------------------

// Escapa texto para HTML (nomes de ficheiros/projetos vêm de outros utilizadores).
function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Valor seguro para usar como argumento JS dentro de um atributo onclick="...".
function jsAttr(value) {
  return escapeHtml(JSON.stringify(String(value ?? '')));
}

// Caminho de ficheiro para URL: codifica cada segmento (espaços, #, ?, %...).
function encPath(name) {
  return String(name).split('/').map(encodeURIComponent).join('/');
}

function authHeaders(extra = {}) {
  return { 'Authorization': `Bearer ${token}`, ...extra };
}

// Lê a mensagem de erro de uma resposta da API.
async function apiError(res, fallback) {
  try {
    const data = await res.json();
    const d = data?.detail;
    if (typeof d === 'string') return d;
    if (d?.error) return d.error;
    if (Array.isArray(d) && d[0]?.msg) return d[0].msg;
  } catch { }
  return fallback;
}

// Sessão expirada em qualquer pedido → volta ao login.
function handleAuthFailure(res) {
  if (res.status === 401) {
    localStorage.removeItem('Joshuatex_token');
    window.location.href = '/app/login';
    return true;
  }
  return false;
}

// Papel no projeto: 'owner' | 'editor' | 'viewer'.
let projectRole = null;

function isViewer() {
  return projectRole === 'viewer';
}

function applyRoleToUI(role) {
  projectRole = role || projectRole;
  document.body.classList.toggle('role-viewer', isViewer());
  if (editor && isViewer()) editor.updateOptions({ readOnly: true });
}

let files       = [];
let editor      = null;
let activeFile  = -1;
let openTabs    = [];
let collab      = null;

let saveTimeout      = null;
let compiling        = false;
let compileLogDetails= '';
let currentPdfUrl    = null;
let externalPdfWindow= null;

let currentErrors   = [];
let currentWarnings = [];

let refsSelected  = [];
let zoteroAllRefs = [];
let zoteroFiltered= [];
let doiRawItem    = null;

const storageKey  = `joshuatex_folders_proj_${projectId}`;
let folderStates  = JSON.parse(localStorage.getItem(storageKey) || '{}');