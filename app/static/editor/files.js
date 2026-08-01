const openModels = new Map();

if (!document.getElementById('ftree-styles')) {
  const s = document.createElement('style');
  s.id = 'ftree-styles';
  s.textContent = `
    .drag-over-folder { background: rgba(37, 99, 235, 0.1) !important; outline: 1px dashed #2563EB; border-radius: 4px; }
    .ftree-action { color: #aaa; font-size: 0.85rem; margin-left: 0.4rem; cursor: pointer; line-height: 0; }
    .ftree-action:hover { color: #16A34A; }
    .ftree-item, .ftree-folder-title { transition: background 0.1s; }
  `;
  document.head.appendChild(s);
}

async function loadProject() {
  try {
    const res = await fetch(`/api/projects/${projectId}`, {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    if (!res.ok) throw new Error('Projeto não encontrado');
    const p = await res.json();
    document.getElementById('projectTitle').value = p.name;
  } catch (e) {
    alert(e.message);
    window.location.href = '/app/projects';
  }
}

async function updateProjectName() {
  const newName = document.getElementById('projectTitle').value.trim();
  if (!newName) return;
  document.getElementById('statusText').textContent = 'A guardar título...';
  try {
    await fetch(`/api/projects/${projectId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ name: newName })
    });
    document.getElementById('statusText').textContent = 'Guardado';
  } catch {
    document.getElementById('statusText').textContent = 'Erro ao guardar';
  }
}

async function loadFiles() {
  const res = await fetch(`/api/projects/${projectId}/files`, {
    headers: { 'Authorization': `Bearer ${token}` }
  });
  const data = await res.json();

  const oldFiles = files;

  files = data.map(f => {
    const name = f.filename.toLowerCase();
    const isText = name.endsWith('.tex') || name.endsWith('.bib') ||
                   name.endsWith('.cls') || name.endsWith('.sty') ||
                   name.endsWith('.txt') || name.endsWith('.md') ||
                   name.endsWith('.m') || name.endsWith('.nv') ||
                   name.includes('license') || name.includes('makefile');
                   
    const oldF = oldFiles.find(x => x.name === f.filename);
    const preservedContent = (oldF && oldF.content !== null) ? oldF.content : null;

    return { name: f.filename, content: preservedContent, is_main: f.is_main, binary: !isText };
  });

  files.sort((a, b) => (b.is_main ? 1 : 0) - (a.is_main ? 1 : 0));
  renderTabs();
  renderFileTree();
}

async function getOrCreateModel(filename) {
  const f = files.find(x => x.name === filename);

  if (openModels.has(filename)) {
    if (f && f.content === null) {
      openModels.get(filename).dispose();
      openModels.delete(filename);
    } else {
      return openModels.get(filename);
    }
  }

  document.getElementById('statusText').textContent = 'A carregar...';
  let content = '';

  const wsReady = collab && collab.ws && collab.ws.readyState === WebSocket.OPEN;
  
  if (!wsReady) {
    try {
      const res = await fetch(`/api/projects/${projectId}/files/${filename}?t=${Date.now()}`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      const data = await res.json();
      content = data.content || '';
      if (f) f.content = content;
    } catch {
      content = '';
    }
  }

  document.getElementById('statusText').textContent = 'Pronto';

  const model = monaco.editor.createModel(content, languageForFile(filename));
  openModels.set(filename, model);
  return model;
}

async function switchFile(index) {
  const f = files[index];

  if (index === activeFile) {
    if (f && f.content === null) {
    } else {
      return;
    }
  }

  clearTimeout(saveTimeout);
  activeFile = index;
  if (!openTabs.includes(index)) openTabs.push(index);

  const monacoEl = document.getElementById('monaco-container');
  const imgEl = document.getElementById('imagePreview');

  if (f.binary) {
    if (collab) collab.leaveFile();
    if (f.content === null) {
      try {
        const res = await fetch(`/api/projects/${projectId}/files/${f.name}?t=${Date.now()}`, {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        const data = await res.json();
        f.content = data.content || '';
      } catch { f.content = ''; }
    }
    _showBinary(f);
    renderTabs();
    renderFileTree();
    return;
  }

  imgEl.style.display = 'none';
  monacoEl.style.display = 'block';

  if (editor) editor.updateOptions({ readOnly: true });

  const model = await getOrCreateModel(f.name);
  editor.setModel(model);

  if (collab) {
    document.getElementById('statusText').textContent = 'A sincronizar...';
    await collab.joinFile(f.name);
    document.getElementById('statusText').textContent = 'Pronto';
  }

  if (editor) editor.updateOptions({ readOnly: false });

  setTimeout(() => editor.layout(), 10);
  renderTabs();
  renderFileTree();
}

const selectFile = switchFile;

function _showBinary(f) {
  const monacoEl = document.getElementById('monaco-container');
  const imgEl = document.getElementById('imagePreview');
  const isImg = typeof f.content === 'string' && f.content.startsWith('data:image/');

  monacoEl.style.display = 'none';
  imgEl.style.display = 'flex';

  if (isImg) {
    document.getElementById('previewImg').src = f.content;
    document.getElementById('imageInfo').textContent = f.name;
  } else {
    document.getElementById('previewImg').src = '';
    document.getElementById('imageInfo').textContent =
      `Ficheiro binário: ${f.name} (Não pode ser pré-visualizado)`;
  }
}

function scheduleSave() {
  if (activeFile < 0 || files[activeFile]?.binary) return;
  if (collab) return; 

  clearTimeout(saveTimeout);
  document.getElementById('statusText').textContent = 'A guardar...';
  saveTimeout = setTimeout(saveFile, 1500);
}

function saveFileManual() {
  clearTimeout(saveTimeout);
  saveFile();
}

async function saveFile() {
  if (activeFile < 0) return;
  const f = files[activeFile];
  if (!f || f.binary) return;
  
  if (collab) {
    document.getElementById('statusText').textContent = 'Guardado';
    return;
  }

  const content = editor.getValue();
  try {
    await fetch(`/api/projects/${projectId}/files/${f.name}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ content })
    });
    document.getElementById('statusText').textContent = 'Guardado';
  } catch {
    document.getElementById('statusText').textContent = 'Erro ao guardar';
  }
}

async function newFile(basePath = '', event = null) {
  if (event) event.stopPropagation();
  
  const defaultName = basePath ? `${basePath}/novo.tex` : 'novo.tex';
  let name = prompt('Nome do ficheiro (ex: novo.tex, script.nv):', defaultName);
  if (!name) return;
  
  name = name.replace(/\/+/g, '/').replace(/^\/+/, '');
  
  const defaultContent = name.endsWith('.m') || name.endsWith('.nv')
    ? '% novo script\n'
    : '% Novo ficheiro';
    
  try {
    await fetch(`/api/projects/${projectId}/files/${name}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ content: defaultContent })
    });
    files.push({ name, content: defaultContent, binary: false, is_main: false });
    
    if (basePath) folderStates[basePath] = true;
    saveFolderStates();
    
    await switchFile(files.length - 1);
  } catch {
    alert('Erro ao criar ficheiro no servidor.');
  }
}

async function newFolder(basePath = '', event = null) {
  if (event) event.stopPropagation();
  
  const defaultName = basePath ? `${basePath}/nova_pasta` : 'nova_pasta';
  let name = prompt('Nome da pasta (ex: imagens, capitulos):', defaultName);
  if (!name) return;
  
  name = name.replace(/\/+/g, '/').replace(/^\/+/, '').replace(/\/+$/, '');
  const dummyFile = `${name}/.keep`;

  try {
    await fetch(`/api/projects/${projectId}/files/${dummyFile}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ content: '' })
    });
    files.push({ name: dummyFile, content: '', binary: false, is_main: false });
    
    folderStates[name] = true;
    if (basePath) folderStates[basePath] = true;
    saveFolderStates();
    
    renderTabs();
    renderFileTree();
    document.getElementById('statusText').textContent = 'Pasta criada';
  } catch {
    alert('Erro ao criar pasta no servidor.');
  }
}

async function deleteFolder(folderPath, event) {
  if (event) event.stopPropagation();
  if (!confirm(`Tens a certeza que queres apagar a pasta '${folderPath}' e TODOS os seus ficheiros?`)) return;

  document.getElementById('statusText').textContent = 'A apagar pasta...';

  const filesToDelete = files.filter(f => f.name.startsWith(folderPath + '/'));

  for (const f of filesToDelete) {
    if (f.is_main) {
      alert(`Não podes apagar a pasta porque contém o ficheiro principal (${f.name}).`);
      document.getElementById('statusText').textContent = 'Cancelado';
      return;
    }
  }

  try {
    for (const f of filesToDelete) {
      await fetch(`/api/projects/${projectId}/files/${f.name}`, {
        method: 'DELETE',
        headers: { 'Authorization': `Bearer ${token}` }
      });
      
      const idx = files.findIndex(x => x.name === f.name);
      if (idx !== -1) {
        const wasActive = (activeFile === idx);
        if (openModels.has(f.name)) {
          openModels.get(f.name).dispose();
          openModels.delete(f.name);
        }
        if (wasActive && collab) collab.leaveFile();

        files.splice(idx, 1);
        openTabs = openTabs.filter(i => i !== idx).map(i => i > idx ? i - 1 : i);

        if (wasActive) {
          activeFile = -1;
        } else if (activeFile > idx) {
          activeFile--;
        }
      }
    }

    if (activeFile === -1 && openTabs.length > 0) {
      await switchFile(openTabs[openTabs.length - 1]);
    } else if (activeFile === -1) {
      editor?.setModel(null);
      const mainIdx = files.findIndex(x => x.is_main);
      if (files.length) await switchFile(mainIdx >= 0 ? mainIdx : 0);
    }

    renderTabs();
    renderFileTree();
    document.getElementById('statusText').textContent = 'Pasta apagada';
  } catch (err) {
    alert('Erro ao apagar a pasta.');
  }
}

async function handleFileUpload(event) {
  const uploaded = event.target.files;
  if (!uploaded?.length) return;
  document.getElementById('statusText').textContent = 'A fazer upload...';

  for (const file of uploaded) {
    const isTextFile = file.name.endsWith('.tex') || file.name.endsWith('.bib') ||
                        file.name.endsWith('.sty') || file.name.endsWith('.cls') ||
                        file.name.endsWith('.m') || file.name.endsWith('.nv');

    const content = await new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = e => resolve(e.target.result);
      r.onerror = reject;
      if (isTextFile) r.readAsText(file);
      else r.readAsDataURL(file);
    });

    try {
      await fetch(`/api/projects/${projectId}/files/${file.name}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify({ content })
      });
      const isBinary = !isTextFile;
      const idx = files.findIndex(f => f.name === file.name);
      if (idx >= 0) { files[idx].content = content; files[idx].binary = isBinary; openModels.delete(file.name); }
      else files.push({ name: file.name, content, binary: isBinary, is_main: false });
      renderTabs();
      renderFileTree();
      document.getElementById('statusText').textContent = 'Upload concluído';
    } catch {
      alert('Erro ao enviar ficheiro: ' + file.name);
      document.getElementById('statusText').textContent = 'Erro';
    }
  }
  event.target.value = '';
}

async function moveOrRenameFile(oldName, newName, reRender = true) {
  try {
    const res = await fetch(`/api/projects/${projectId}/files/${encodeURIComponent(oldName)}/rename`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ new_name: newName })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Erro ao mover/renomear.');
    }
    applyRename(oldName, newName, reRender);
  } catch (err) {
    alert(err.message);
    throw err;
  }
}

async function promptRenameFile(index, event) {
  if (event) event.stopPropagation();
  const f = files[index];
  const newName = prompt(`Renomear "${f.name}" para:`, f.name);
  if (!newName || newName === f.name) return;

  await moveOrRenameFile(f.name, newName, true);
  document.getElementById('statusText').textContent = 'Ficheiro renomeado';
}

function applyRename(oldName, newName, reRender = true) {
  const idx = files.findIndex(x => x.name === oldName);
  if (idx === -1) return;
  files[idx].name = newName;

  if (openModels.has(oldName)) {
    const model = openModels.get(oldName);
    openModels.delete(oldName);
    openModels.set(newName, model);
  }
  if (collab && collab.currentFile === oldName) {
    collab.currentFile = newName;
  }
  if (reRender) {
    renderTabs();
    renderFileTree();
  }
}
window.__applyRemoteRename = applyRename;

async function deleteFile(index, event) {
  event.stopPropagation();
  const f = files[index];

  if (f.is_main) {
    alert("Não podes apagar o ficheiro principal do projeto.");
    return;
  }
  if (!confirm(`Tens a certeza que queres apagar permanentemente o ficheiro '${f.name}'?`)) return;

  document.getElementById('statusText').textContent = 'A apagar...';

  try {
    const res = await fetch(`/api/projects/${projectId}/files/${f.name}`, {
      method: 'DELETE',
      headers: { 'Authorization': `Bearer ${token}` }
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Erro ao apagar o ficheiro.');
    }

    const wasActive = (activeFile === index);
    if (openModels.has(f.name)) {
      openModels.get(f.name).dispose();
      openModels.delete(f.name);
    }
    if (wasActive && collab) collab.leaveFile();

    files.splice(index, 1);
    openTabs = openTabs.filter(i => i !== index).map(i => i > index ? i - 1 : i);

    if (wasActive) {
      if (openTabs.length > 0) {
        activeFile = -1;
        await switchFile(openTabs[openTabs.length - 1]);
      } else {
        const mainIdx = files.findIndex(x => x.is_main);
        activeFile = -1;
        if (files.length) await switchFile(mainIdx >= 0 ? mainIdx : 0);
        else { renderTabs(); renderFileTree(); }
      }
    } else {
      if (activeFile > index) activeFile--;
      renderTabs();
      renderFileTree();
    }

    document.getElementById('statusText').textContent = 'Ficheiro apagado';
  } catch (err) {
    alert(err.message);
    document.getElementById('statusText').textContent = 'Erro ao apagar';
  }
}

async function setMainFile() {
  const f = files[activeFile];
  if (!f || f.binary || !f.name.endsWith('.tex')) {
    alert('Apenas ficheiros .tex podem ser definidos como ficheiro principal.');
    return;
  }
  try {
    const res = await fetch(`/api/projects/${projectId}/set-main/${f.name}`, {
      method: 'PUT',
      headers: { 'Authorization': `Bearer ${token}` }
    });
    if (!res.ok) throw new Error('Erro ao definir ficheiro principal');
    files.forEach(file => file.is_main = false);
    f.is_main = true;
    renderFileTree();
    document.getElementById('statusText').textContent = `${f.name} é agora o principal`;
  } catch (err) {
    alert(err.message);
  }
}

function renderTabs() {
  const tabs = document.getElementById('editorTabs');
  if (!openTabs.length) {
    tabs.innerHTML = '';
    if (collab) collab.leaveFile();
    editor?.setModel(null);
    return;
  }
  tabs.innerHTML = '';
  openTabs.forEach(i => {
    const f = files[i];
    const tab = document.createElement('div');
    tab.className = 'tab' + (i === activeFile ? ' active' : '');
    tab.draggable = true;
    tab.dataset.index = i;
    tab.innerHTML = `
      <svg width="12" height="12" viewBox="0 0 24 24" stroke="currentColor" fill="none" stroke-width="2.5">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
        <polyline points="14,2 14,8 20,8"/>
      </svg>
      <span class="tab-name">${f.name.split('/').pop()}</span>
      <span class="tab-close">×</span>
    `;
    tab.addEventListener('click', (e) => {
      if (e.target.closest('.tab-close')) return;
      switchFile(i);
    });
    tab.querySelector('.tab-close').addEventListener('click', (e) => closeTab(i, e));

    tab.addEventListener('dragstart', (e) => {
      e.dataTransfer.setData('text/tab-index', String(i));
      e.dataTransfer.effectAllowed = 'move';
    });
    tab.addEventListener('dragover', (e) => { e.preventDefault(); tab.classList.add('drag-over'); });
    tab.addEventListener('dragleave', () => tab.classList.remove('drag-over'));
    tab.addEventListener('drop', (e) => {
      e.preventDefault();
      tab.classList.remove('drag-over');
      const draggedIndex = e.dataTransfer.getData('text/tab-index');
      if (draggedIndex) {
        reorderTabs(parseInt(draggedIndex, 10), i);
      }
    });

    tabs.appendChild(tab);
  });
}

function reorderTabs(draggedIndex, targetIndex) {
  if (draggedIndex === targetIndex) return;
  const from = openTabs.indexOf(draggedIndex);
  const to = openTabs.indexOf(targetIndex);
  if (from < 0 || to < 0) return;
  openTabs.splice(from, 1);
  openTabs.splice(to, 0, draggedIndex);
  renderTabs();
}

function closeTab(index, event) {
  event.stopPropagation();
  openTabs = openTabs.filter(i => i !== index);
  if (activeFile === index) {
    if (openTabs.length > 0) {
      activeFile = -1;
      switchFile(openTabs[openTabs.length - 1]);
    } else {
      activeFile = -1;
      if (collab) collab.leaveFile();
      editor?.setModel(null);
      renderTabs();
      renderFileTree();
    }
  } else {
    renderTabs();
  }
}

function saveFolderStates() {
  localStorage.setItem(storageKey, JSON.stringify(folderStates));
}

function toggleFolder(folderId, fullPath) {
  const el = document.getElementById(folderId);
  const icon = document.getElementById('icon-' + folderId);
  const open = el.style.display === 'none';
  el.style.display = open ? 'block' : 'none';
  icon.textContent = open ? '▼' : '▶';
  folderStates[fullPath] = open;
  saveFolderStates();
}


window.onDragStartTreeItem = function(event, path, isFolder) {
  event.dataTransfer.setData('text/ftree-path', JSON.stringify({path, isFolder}));
  event.dataTransfer.effectAllowed = 'move';
  event.stopPropagation();
};

window.onDragOverTreeFolder = function(event) {
  event.preventDefault(); 
  event.stopPropagation();
  event.currentTarget.classList.add('drag-over-folder');
};

window.onDragLeaveTreeFolder = function(event) {
  event.currentTarget.classList.remove('drag-over-folder');
};

window.onDropToTreeFolder = async function(event, targetFolder) {
  event.preventDefault();
  event.stopPropagation();
  event.currentTarget.classList.remove('drag-over-folder');
  
  try {
    const rawData = event.dataTransfer.getData('text/ftree-path');
    if (!rawData) return;
    
    const data = JSON.parse(rawData);
    const sourcePath = data.path;
    const isFolder = data.isFolder;

    if (sourcePath === targetFolder || (targetFolder && targetFolder.startsWith(sourcePath + '/'))) return;

    document.getElementById('statusText').textContent = 'A mover...';

    if (isFolder) {
      const filesToMove = files.filter(f => f.name.startsWith(sourcePath + '/'));
      const folderName = sourcePath.split('/').pop();
      for (const f of filesToMove) {
        const relative = f.name.substring(sourcePath.length); 
        const newTarget = targetFolder ? `${targetFolder}/${folderName}${relative}` : `${folderName}${relative}`;
        await moveOrRenameFile(f.name, newTarget, false);
      }
    } else {
      const fileName = sourcePath.split('/').pop();
      const newTarget = targetFolder ? `${targetFolder}/${fileName}` : fileName;
      if (sourcePath !== newTarget) {
        await moveOrRenameFile(sourcePath, newTarget, false);
      }
    }
    
    if (targetFolder) folderStates[targetFolder] = true;
    saveFolderStates();
    
    renderTabs();
    renderFileTree();
    document.getElementById('statusText').textContent = 'Movido com sucesso';
  } catch (e) {
    console.error(e);
  }
};


function renderFileTree() {
  const body = document.getElementById('filetreeBody');
  if (!body) return;

  const tree = { folders: {}, files: [] };
  files.forEach((f, index) => {
    const parts = f.name.split('/');
    let node = tree;
    for (let i = 0; i < parts.length - 1; i++) {
      const fn = parts[i];
      if (!node.folders[fn]) node.folders[fn] = { folders: {}, files: [] };
      node = node.folders[fn];
    }
    node.files.push({ file: f, originalIndex: index });
  });

  function createHTML(node, pathName, level) {
    let html = '';
    const pad = level * 14;

    Object.keys(node.folders).sort().forEach(folderName => {
      const fullPath = pathName + folderName;
      const folderId = 'folder-' + btoa(unescape(encodeURIComponent(fullPath))).replace(/[^a-zA-Z0-9]/g, '');
      const isOpen = folderStates[fullPath] !== false;
      
      html += `
        <div class="ftree-folder"
          draggable="true"
          ondragstart="onDragStartTreeItem(event, '${fullPath}', true)"
          ondragover="onDragOverTreeFolder(event)"
          ondragleave="onDragLeaveTreeFolder(event)"
          ondrop="onDropToTreeFolder(event, '${fullPath}')"
          style="padding-left:${pad + 16}px;cursor:pointer;user-select:none;margin-top:0.2rem;display:flex;align-items:center;padding-right:0.5rem;"
          title="${folderName}">
          
          <div onclick="toggleFolder('${folderId}','${fullPath}')" class="ftree-folder-title" style="display:flex;align-items:center;flex:1;overflow:hidden;padding: 2px 0;">
            <span id="icon-${folderId}" style="margin-right:6px;font-size:10px;width:10px;display:inline-block;text-align:center;">${isOpen ? '▼' : '▶'}</span>
            <svg width="14" height="14" viewBox="0 0 24 24" stroke="#999" fill="none" stroke-width="2.5" style="margin-right:6px;flex-shrink:0;">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
            </svg>
            <span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${folderName}</span>
          </div>
          
          <div style="display:flex; align-items:center; opacity: 0.8;">
            <span onclick="newFile('${fullPath}', event)" title="Novo ficheiro aqui" class="ftree-action">📄+</span>
            <span onclick="newFolder('${fullPath}', event)" title="Nova pasta aqui" class="ftree-action">📁+</span>
            <span onclick="deleteFolder('${fullPath}', event)" title="Apagar pasta" class="ftree-action" style="font-size:1.1rem" onmouseover="this.style.color='#E03030'" onmouseout="this.style.color='#aaa'">×</span>
          </div>
          
        </div>
        <div id="${folderId}" style="display:${isOpen ? 'block' : 'none'};">
          ${createHTML(node.folders[folderName], fullPath + '/', level + 1)}
        </div>
      `;
    });

    node.files
      .sort((a, b) => {
        if (a.file.is_main && !b.file.is_main) return -1;
        if (!a.file.is_main && b.file.is_main) return 1;
        return a.file.name.localeCompare(b.file.name);
      })
      .forEach(({ file: f, originalIndex: i }) => {
        const fileName = f.name.split('/').pop();
        
        if (fileName === '.keep') return;
        
        const isActive = i === activeFile ? 'active' : '';

        const starIcon = f.is_main
          ? `<span title="Ficheiro Principal" style="color:var(--red);font-size:1rem;margin-left:0.3rem;line-height:0;">★</span>`
          : '';

        const renameIcon = `
          <span onclick="promptRenameFile(${i}, event)" title="Renomear" style="color:#aaa;font-size:0.85rem;margin-left:0.4rem;cursor:pointer;line-height:0;" onmouseover="this.style.color='#2563EB'" onmouseout="this.style.color='#aaa'">✎</span>`;

        const deleteIcon = !f.is_main
          ? `<span onclick="deleteFile(${i}, event)" title="Apagar ficheiro" style="color:#aaa;font-size:1.1rem;margin-left:0.4rem;cursor:pointer;line-height:0;" onmouseover="this.style.color='#E03030'" onmouseout="this.style.color='#aaa'">×</span>`
          : '';

        const icon = f.binary
          ? `<svg viewBox="0 0 24 24" style="width:14px;height:14px;margin-right:0.4rem;stroke:#111;fill:none;stroke-width:2.5;flex-shrink:0"><rect x="3" y="3" width="18" height="18"/><circle cx="8.5" cy="8.5" r="1.5" fill="currentColor"/><polyline points="21,15 16,10 5,21"/></svg>`
          : fileName.endsWith('.bib')
          ? `<svg viewBox="0 0 24 24" style="width:14px;height:14px;margin-right:0.4rem;stroke:#111;fill:none;stroke-width:2.5;flex-shrink:0"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>`
          : (fileName.endsWith('.m') || fileName.endsWith('.nv'))
          ? `<svg viewBox="0 0 24 24" style="width:14px;height:14px;margin-right:0.4rem;stroke:#16A34A;fill:none;stroke-width:2.5;flex-shrink:0"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>`
          : `<svg viewBox="0 0 24 24" style="width:14px;height:14px;margin-right:0.4rem;stroke:#111;fill:none;stroke-width:2.5;flex-shrink:0"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14,2 14,8 20,8"/></svg>`;

        html += `
          <div class="ftree-item ${isActive}" style="padding-left:${pad + 16}px;" title="${f.name}"
            draggable="true" ondragstart="onDragStartTreeItem(event, '${f.name}', false)">
            <div style="display:flex;align-items:center;flex:1;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" onclick="switchFile(${i})">
              ${icon}<span style="overflow:hidden;text-overflow:ellipsis;flex:1">${fileName}</span>
            </div>
            <div style="display:flex;align-items:center;margin-left:auto;">
              ${starIcon}${renameIcon}${deleteIcon}
            </div>
          </div>
        `;
      });
    return html;
  }

  body.innerHTML = `
    <div style="min-height:100%; padding-bottom: 2rem;"
         ondragover="onDragOverTreeFolder(event)"
         ondragleave="onDragLeaveTreeFolder(event)"
         ondrop="onDropToTreeFolder(event, '')">
      ${createHTML(tree, '', 0)}
    </div>
  `;
}