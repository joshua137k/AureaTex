const openModels = new Map();
let historyEditor = null;
let selectedHistoryId = null;



const TEXT_EXTENSIONS = ['.tex', '.bib', '.cls', '.sty', '.txt', '.md', '.m', '.nv', '.bst', '.bbx', '.cbx',
  '.lco', '.def', '.cfg', '.clo', '.dtx', '.ins', '.tikz', '.csv', '.json', '.yml', '.yaml', '.gitignore', '.latexmkrc'];

function isTextFileName(filename) {
  const name = filename.toLowerCase();
  const base = name.split('/').pop();
  return TEXT_EXTENSIONS.some(ext => name.endsWith(ext)) ||
         base.includes('license') || base.includes('makefile') || base === 'readme';
}

async function loadProject() {
  try {
    const res = await fetch(`/api/projects/${projectId}`, {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    if (!res.ok) throw new Error('Project not found');
    const p = await res.json();
    document.getElementById('projectTitle').value = p.name;
    document.title = `${p.name} - AureaTeX`;
    applyRoleToUI(p.role);

    // Só o dono pode mudar o nome do projeto.
    document.getElementById('projectTitle').readOnly = p.role !== 'owner';
  } catch (e) {
    await uiAlert(e.message, { title: 'Could not open the project' });
    window.location.href = '/app/projects';
  }
}


async function openHistoryModal() {
  if (activeFile < 0) return;
  const f = files[activeFile];
  
  if (f.binary) {
    alert("History is not available for images/binary files.");
    return;
  }
  
  document.getElementById('historyModal').style.display = 'flex';
  document.getElementById('historyFileName').textContent = f.name;
  document.getElementById('historyList').innerHTML = '<div class="history-placeholder">Loading versions…</div>';
  document.getElementById('btnRestoreHistory').disabled = true;
  selectedHistoryId = null;
  
  // Create the Preview editor if it doesn't exist yet
  if (!historyEditor) {
    historyEditor = monaco.editor.create(document.getElementById('historyMonaco'), {
      value: '// Choose a version from the sidebar...',
      language: languageForFile(f.name),
      theme: monacoThemeName(),
      readOnly: true,
      minimap: { enabled: false },
      fontFamily: "'JetBrains Mono', monospace",
    });
  } else {
    historyEditor.setValue('// Choose a version from the sidebar...');
    monaco.editor.setModelLanguage(historyEditor.getModel(), languageForFile(f.name));
  }
  
  try {
    const res = await fetch(`/api/projects/${projectId}/files/${encPath(f.name)}/history`, {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    const data = await res.json();
    
    if (!res.ok) {
      throw new Error(data.detail || 'Error loading history from the server.');
    }
    
    if (!data || data.length === 0) {
      document.getElementById('historyList').innerHTML = '<div class="history-placeholder">No saved versions for this file yet. A version is saved every few minutes while you edit.</div>';
      return;
    }
    
    document.getElementById('historyList').innerHTML = data.map(h => {
      const d = new Date(h.saved_at + 'Z');
      const timeStr = d.toLocaleTimeString('en-US', {hour:'2-digit', minute:'2-digit'});
      const dateStr = d.toLocaleDateString('en-US', {day:'2-digit', month:'short'});
      const name = h.display_name || h.username;
      
      return `
        <div class="history-item" onclick="loadHistoryPreview(${Number(h.id)}, this)">
          <div class="history-item-date">${dateStr} at ${timeStr}</div>
          <div class="history-item-author">By <strong>${escapeHtml(name)}</strong></div>
        </div>
      `;
    }).join('');
  } catch(err) {
    document.getElementById('historyList').innerHTML = `<div class="history-placeholder error">Error: ${escapeHtml(err.message)}</div>`;
  }
}

function closeHistoryModal() {
  document.getElementById('historyModal').style.display = 'none';
}

async function loadHistoryPreview(id, element) {
  document.querySelectorAll('.history-item').forEach(el => el.classList.remove('selected'));
  element.classList.add('selected');
  
  selectedHistoryId = id;
  document.getElementById('btnRestoreHistory').disabled = false;
  historyEditor.setValue('% Loading version from the past...');
  
  try {
    const res = await fetch(`/api/projects/${projectId}/history/${id}`, {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    const data = await res.json();
    historyEditor.setValue(data.content || '');
  } catch(err) {
    historyEditor.setValue('% Error loading content.');
  }
}

async function restoreSelectedHistory() {
  if (!selectedHistoryId || activeFile < 0) return;
  const f = files[activeFile];
  
  if(!await uiConfirm('The current text of this file will be replaced by the version shown on screen.', { title: 'Restore this version?', okText: 'Restore', danger: true })) return;
  
  const btn = document.getElementById('btnRestoreHistory');
  btn.disabled = true;
  btn.textContent = 'Restoring...';
  
  try {
    const res = await fetch(`/api/projects/${projectId}/files/${encPath(f.name)}/restore/${selectedHistoryId}`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${token}` }
    });
    
    if(!res.ok) throw new Error(await apiError(res, 'Error restoring the version.'));

    closeHistoryModal();
    document.getElementById('statusText').textContent = 'Version restored';
  } catch(err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Restore this Version';
  }
}


async function updateProjectName() {
  const newName = document.getElementById('projectTitle').value.trim();
  if (!newName) return;
  document.getElementById('statusText').textContent = 'Saving title...';
  try {
    const res = await fetch(`/api/projects/${projectId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ name: newName })
    });
    if (!res.ok) throw new Error(await apiError(res, 'Error saving title.'));
    document.title = `${newName} - AureaTeX`;
    document.getElementById('statusText').textContent = 'Saved';
  } catch (err) {
    document.getElementById('statusText').textContent = 'Error saving';
    alert(err.message);
    loadProject();
  }
}

async function loadFiles() {
  const res = await fetch(`/api/projects/${projectId}/files`, {
    headers: { 'Authorization': `Bearer ${token}` }
  });
  if (handleAuthFailure(res)) return;
  if (!res.ok) throw new Error(await apiError(res, 'Error loading files.'));
  const data = await res.json();

  // Os separadores guardam índices do array `files`: preserva-os pelo nome.
  const oldFiles = files;
  const openNames = openTabs.map(i => oldFiles[i]?.name).filter(Boolean);
  const activeName = activeFile >= 0 ? oldFiles[activeFile]?.name : null;

  files = data.map(f => {
    const isText = isTextFileName(f.filename);
                                      
    const oldF = oldFiles.find(x => x.name === f.filename);
    const preservedContent = (oldF && oldF.content !== null) ? oldF.content : null;

    return { name: f.filename, content: preservedContent, is_main: f.is_main, binary: !isText };
  });

  files.sort((a, b) => (b.is_main ? 1 : 0) - (a.is_main ? 1 : 0));

  if (oldFiles.length) {
    openTabs = openNames.map(n => files.findIndex(f => f.name === n)).filter(i => i >= 0);
    activeFile = activeName ? files.findIndex(f => f.name === activeName) : -1;
  }

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

  document.getElementById('statusText').textContent = 'Loading...';
  let content = '';

  const wsReady = collab && collab.ws && collab.ws.readyState === WebSocket.OPEN;
  
  if (!wsReady) {
    try {
      const res = await fetch(`/api/projects/${projectId}/files/${encPath(filename)}?t=${Date.now()}`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      const data = await res.json();
      content = data.content || '';
      if (f) f.content = content;
    } catch {
      content = '';
    }
  }

  document.getElementById('statusText').textContent = 'Ready';

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
        const res = await fetch(`/api/projects/${projectId}/files/${encPath(f.name)}?t=${Date.now()}`, {
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
    document.getElementById('statusText').textContent = 'Syncing...';
    const synced = await collab.joinFile(f.name);

    // Só se pode escrever depois de o servidor enviar o documento; sem isso
    // o editor fica só de leitura até a ligação voltar.
    if (editor) editor.updateOptions({ readOnly: !synced || isViewer() });
    document.getElementById('statusText').textContent = synced ? 'Ready' : 'Reconnecting...';
  } else if (editor) {
    editor.updateOptions({ readOnly: isViewer() });
  }

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

  // Visualizador com zoom, recorte e rotação (image.js).
  openImageViewer(f, isImg);
}

function scheduleSave() {
  if (activeFile < 0 || files[activeFile]?.binary) return;
  if (collab) return; 

  clearTimeout(saveTimeout);
  document.getElementById('statusText').textContent = 'Saving...';
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
    document.getElementById('statusText').textContent = 'Saved';
    return;
  }

  const content = editor.getValue();
  try {
    await fetch(`/api/projects/${projectId}/files/${encPath(f.name)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ content })
    });
    document.getElementById('statusText').textContent = 'Saved';
  } catch {
    document.getElementById('statusText').textContent = 'Error saving';
  }
}

async function newFile(basePath = '', event = null) {
  if (event) event.stopPropagation();
  
  const defaultName = basePath ? `${basePath}/new.tex` : 'new.tex';
  let name = await uiPrompt('New file', defaultName, { title: 'New file', okText: 'Create', placeholder: 'e.g. chapter1.tex' });
  if (!name) return;
  
  name = name.replace(/\/+/g, '/').replace(/^\/+/, '');
  
  const defaultContent = name.endsWith('.m') || name.endsWith('.nv')
    ? '% new script\n'
    : '% New file';
    
  if (files.some(f => f.name === name)) {
    alert(`A file named '${name}' already exists.`);
    return;
  }

  try {
    const res = await fetch(`/api/projects/${projectId}/files/${encPath(name)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ content: defaultContent })
    });
    if (!res.ok) throw new Error(await apiError(res, 'Error creating file on server.'));

    // O aviso "file_added" do servidor pode já ter acrescentado a entrada.
    let idx = files.findIndex(f => f.name === name);
    if (idx === -1) {
      files.push({ name, content: null, binary: !isTextFileName(name), is_main: false });
      idx = files.length - 1;
    }

    if (basePath) folderStates[basePath] = true;
    saveFolderStates();

    await switchFile(idx);
  } catch (err) {
    alert(err.message);
  }
}

async function newFolder(basePath = '', event = null) {
  if (event) event.stopPropagation();
  
  const defaultName = basePath ? `${basePath}/new_folder` : 'new_folder';
  let name = await uiPrompt('New folder', defaultName, { title: 'New folder', okText: 'Create', placeholder: 'e.g. figures' });
  if (!name) return;
  
  name = name.replace(/\/+/g, '/').replace(/^\/+/, '').replace(/\/+$/, '');
  const dummyFile = `${name}/.keep`;

  try {
    const res = await fetch(`/api/projects/${projectId}/files/${encPath(dummyFile)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ content: '' })
    });
    if (!res.ok) throw new Error(await apiError(res, 'Error creating folder on server.'));

    if (!files.some(f => f.name === dummyFile)) {
      files.push({ name: dummyFile, content: '', binary: false, is_main: false });
    }
    
    folderStates[name] = true;
    if (basePath) folderStates[basePath] = true;
    saveFolderStates();
    
    renderTabs();
    renderFileTree();
    document.getElementById('statusText').textContent = 'Folder created';
  } catch (err) {
    alert(err.message);
  }
}

async function deleteFolder(folderPath, event) {
  if (event) event.stopPropagation();
  if (!await uiConfirm(`The folder '${folderPath}' and ALL its files will be deleted.`, { title: 'Delete folder?', danger: true })) return;

  document.getElementById('statusText').textContent = 'Deleting folder...';

  const filesToDelete = files.filter(f => f.name.startsWith(folderPath + '/'));

  for (const f of filesToDelete) {
    if (f.is_main) {
      alert(`You can't delete the folder because it contains the main file (${f.name}).`);
      document.getElementById('statusText').textContent = 'Cancelled';
      return;
    }
  }

  try {
    for (const f of filesToDelete) {
      const res = await fetch(`/api/projects/${projectId}/files/${encPath(f.name)}`, {
        method: 'DELETE',
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (!res.ok && res.status !== 404) throw new Error(await apiError(res, 'Error deleting the folder.'));

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
    document.getElementById('statusText').textContent = 'Folder deleted';
  } catch (err) {
    alert(err.message || 'Error deleting the folder.');
    renderTabs();
    renderFileTree();
  }
}

async function handleFileUpload(event) {
  const uploaded = event.target.files;
  if (!uploaded?.length) return;
  document.getElementById('statusText').textContent = 'Uploading...';

  for (const file of uploaded) {
    const isTextFile = isTextFileName(file.name);

    if (file.size > 10 * 1024 * 1024) {
      alert(`'${file.name}' is larger than 10 MB and cannot be uploaded.`);
      continue;
    }

    const content = await new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = e => resolve(e.target.result);
      r.onerror = reject;
      if (isTextFile) r.readAsText(file);
      else r.readAsDataURL(file);
    });

    try {
      const res = await fetch(`/api/projects/${projectId}/files/${encPath(file.name)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify({ content })
      });
      if (!res.ok) throw new Error(await apiError(res, 'Error uploading file: ' + file.name));
      const isBinary = !isTextFile;
      const idx = files.findIndex(f => f.name === file.name);
      if (idx >= 0) { files[idx].content = content; files[idx].binary = isBinary; openModels.delete(file.name); }
      else files.push({ name: file.name, content, binary: isBinary, is_main: false });
      renderTabs();
      renderFileTree();
      document.getElementById('statusText').textContent = 'Upload complete';
    } catch (err) {
      alert(err.message);
      document.getElementById('statusText').textContent = 'Error';
    }
  }
  event.target.value = '';
}

async function moveOrRenameFile(oldName, newName, reRender = true) {
  try {
    const res = await fetch(`/api/projects/${projectId}/files/${encPath(oldName)}/rename`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ new_name: newName })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Error moving/renaming.');
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
  const newName = await uiPrompt(`Rename or move "${f.name}". Use / for folders.`, f.name, { title: 'Rename file', okText: 'Rename' });
  if (!newName || newName === f.name) return;

  await moveOrRenameFile(f.name, newName, true);
  document.getElementById('statusText').textContent = 'File renamed';
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
    alert("You can't delete the project's main file.");
    return;
  }
  if (!await uiConfirm(`'${f.name}' will be permanently deleted.`, { title: 'Delete file?', danger: true })) return;

  document.getElementById('statusText').textContent = 'Deleting...';

  try {
    const res = await fetch(`/api/projects/${projectId}/files/${encPath(f.name)}`, {
      method: 'DELETE',
      headers: { 'Authorization': `Bearer ${token}` }
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Error deleting the file.');
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

    document.getElementById('statusText').textContent = 'File deleted';
  } catch (err) {
    alert(err.message);
    document.getElementById('statusText').textContent = 'Error deleting';
  }
}

async function setMainFile(index = activeFile) {
  const f = files[index];
  if (!f || f.binary || !f.name.endsWith('.tex')) {
    alert('Only .tex files can be set as the main file.');
    return;
  }
  try {
    const res = await fetch(`/api/projects/${projectId}/set-main/${encPath(f.name)}`, {
      method: 'PUT',
      headers: { 'Authorization': `Bearer ${token}` }
    });
    if (!res.ok) throw new Error('Error setting main file');
    files.forEach(file => file.is_main = false);
    f.is_main = true;
    renderFileTree();
    renderTabs();
    document.getElementById('statusText').textContent = `${f.name} is now the main file`;
  } catch (err) {
    alert(err.message);
  }
}

function renderTabs() {
  if (typeof updateCompileTarget === 'function') updateCompileTarget();
  const tabs = document.getElementById('editorTabs');
  if (!openTabs.length) {
    tabs.innerHTML = '';
    if (collab) collab.leaveFile();
    editor?.setModel(null);
    return;
  }
  tabs.innerHTML = '';
  openTabs = openTabs.filter(i => files[i]);
  openTabs.forEach(i => {
    const f = files[i];
    const tab = document.createElement('div');
    tab.className = 'tab' + (i === activeFile ? ' active' : '');
    tab.draggable = true;
    tab.dataset.index = i;
    tab.innerHTML = `
      ${fileIcon(f)}
      <span class="tab-name" title="${escapeHtml(f.name)}">${escapeHtml(f.name.split('/').pop())}</span>
      <span class="tab-close" title="Close">×</span>
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
  if (!el) return;
  const open = el.style.display === 'none';
  el.style.display = open ? 'block' : 'none';
  if (icon) icon.classList.toggle('open', open);
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

    document.getElementById('statusText').textContent = 'Moving...';

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
    document.getElementById('statusText').textContent = 'Moved successfully';
  } catch (e) {
    console.error(e);
  }
};


// ---------------------------------------------------------------------------
// Ícones
// ---------------------------------------------------------------------------
const ICONS = {
  chevron: '<polyline points="9 6 15 12 9 18"/>',
  folder:  '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
  file:    '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>',
  tex:     '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="13" y2="17"/>',
  bib:     '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
  image:   '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>',
  code:    '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>',
  style:   '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>',
  newFile: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="12" x2="12" y2="18"/><line x1="9" y1="15" x2="15" y2="15"/>',
  newFolder: '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/>',
  rename:  '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  ai:      '<path d="M12 3l1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9z"/><path d="M19 14l.9 2.1L22 17l-2.1.9L19 20l-.9-2.1L16 17l2.1-.9z"/>',
  trash:   '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>',
};

function svgIcon(name, cls = '') {
  return `<svg class="${cls}" viewBox="0 0 24 24">${ICONS[name]}</svg>`;
}

// Ícone (e cor) conforme o tipo de ficheiro.
function fileKind(f) {
  const name = f.name.toLowerCase();
  if (name.endsWith('.tex')) return 'tex';
  if (name.endsWith('.bib')) return 'bib';
  if (/\.(png|jpe?g|gif|svg|webp|bmp|eps)$/.test(name)) return 'image';
  if (name.endsWith('.pdf')) return 'pdf';
  if (/\.(cls|sty|bst|bbx|cbx|def|clo)$/.test(name)) return 'style';
  if (/\.(m|nv|py|js|json|yml|yaml|sh)$/.test(name)) return 'code';
  return 'file';
}

function fileIcon(f, extraClass = '') {
  const kind = fileKind(f);
  const shape = { tex: 'tex', bib: 'bib', image: 'image', pdf: 'file', style: 'style', code: 'code', file: 'file' }[kind];
  return svgIcon(shape, `ftree-icon is-${kind} ${extraClass}`.trim());
}

// ---------------------------------------------------------------------------
// Árvore de ficheiros
// ---------------------------------------------------------------------------
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

  const INDENT = 14;
  const BASE = 8;
  const byName = (a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' });

  function createHTML(node, pathName, level) {
    let html = '';
    const pad = BASE + level * INDENT;

    Object.keys(node.folders).sort(byName).forEach(folderName => {
      const fullPath = pathName + folderName;
      const folderId = 'folder-' + btoa(unescape(encodeURIComponent(fullPath))).replace(/[^a-zA-Z0-9]/g, '');
      const isOpen = folderStates[fullPath] !== false;

      html += `
        <div class="ftree-row ftree-folder-row" style="padding-left:${pad}px"
          title="${escapeHtml(fullPath)}" data-folder="${escapeHtml(fullPath)}"
          draggable="true"
          onclick="toggleFolder('${folderId}',${jsAttr(fullPath)})"
          ondragstart="onDragStartTreeItem(event, ${jsAttr(fullPath)}, true)"
          ondragover="onDragOverTreeFolder(event)"
          ondragleave="onDragLeaveTreeFolder(event)"
          ondrop="onDropToTreeFolder(event, ${jsAttr(fullPath)})">
          <svg id="icon-${folderId}" class="ftree-chevron ${isOpen ? 'open' : ''}" viewBox="0 0 24 24">${ICONS.chevron}</svg>
          ${svgIcon('folder', 'ftree-icon is-folder')}
          <span class="ftree-name">${escapeHtml(folderName)}</span>
          <span class="ftree-actions">
            <button class="ftree-act ai-act" title="Copy this folder for AI" onclick="event.stopPropagation(); copyForAI('full', ${jsAttr(fullPath)})">${svgIcon('ai')}</button>
            <button class="ftree-act" title="New file in this folder" onclick="newFile(${jsAttr(fullPath)}, event)">${svgIcon('newFile')}</button>
            <button class="ftree-act" title="New folder in this folder" onclick="newFolder(${jsAttr(fullPath)}, event)">${svgIcon('newFolder')}</button>
            <button class="ftree-act danger" title="Delete folder" onclick="deleteFolder(${jsAttr(fullPath)}, event)">${svgIcon('trash')}</button>
          </span>
        </div>
        <div id="${folderId}" class="ftree-children" style="display:${isOpen ? 'block' : 'none'};--guide:${pad + 7}">
          ${createHTML(node.folders[folderName], fullPath + '/', level + 1)}
        </div>
      `;
    });

    node.files
      .sort((a, b) => {
        if (a.file.is_main && !b.file.is_main) return -1;
        if (!a.file.is_main && b.file.is_main) return 1;
        return byName(a.file.name, b.file.name);
      })
      .forEach(({ file: f, originalIndex: i }) => {
        const fileName = f.name.split('/').pop();

        // Marcador de pasta vazia.
        if (fileName === '.keep') return;

        const isActive = i === activeFile ? 'active' : '';

        html += `
          <div class="ftree-row ftree-file-row ${isActive}" style="padding-left:${pad}px" data-index="${i}"
            title="${escapeHtml(f.name)}${f.is_main ? ' (main file)' : ''}"
            draggable="true"
            onclick="switchFile(${i})"
            ondragstart="onDragStartTreeItem(event, ${jsAttr(f.name)}, false)">
            <span class="ftree-chevron-spacer"></span>
            ${fileIcon(f)}
            <span class="ftree-name">${escapeHtml(fileName)}</span>
            ${f.is_main ? '<span class="ftree-main-badge">main</span>' : ''}
            <span class="ftree-actions">
              ${f.binary ? '' : `<button class="ftree-act ai-act" title="Copy this file for AI" onclick="event.stopPropagation(); copyForAI('full', ${jsAttr(f.name)})">${svgIcon('ai')}</button>`}
              <button class="ftree-act" title="Rename / move" onclick="promptRenameFile(${i}, event)">${svgIcon('rename')}</button>
              ${f.is_main ? '' : `<button class="ftree-act danger" title="Delete file" onclick="deleteFile(${i}, event)">${svgIcon('trash')}</button>`}
            </span>
          </div>
        `;
      });
    return html;
  }

  const content = createHTML(tree, '', 0);

  body.innerHTML = `
    <div class="ftree-root"
         ondragover="onDragOverTreeFolder(event)"
         ondragleave="onDragLeaveTreeFolder(event)"
         ondrop="onDropToTreeFolder(event, '')">
      ${content.trim() ? content : '<div class="ftree-empty">No files yet.<br>Create or upload one above.</div>'}
    </div>
  `;
}
