
const token     = localStorage.getItem('Joshuatex_token');
const urlParams = new URLSearchParams(window.location.search);
const projectId = urlParams.get('id');

if (!token)     window.location.href = '/';
if (!projectId) window.location.href = '/app/projects';

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