const openModels = new Map();
let historyEditor = null;
let selectedHistoryId = null;



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
    if (!res.ok) throw new Error('Project not found');
    const p = await res.json();
    document.getElementById('projectTitle').value = p.name;
  } catch (e) {
    alert(e.message);
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
  document.getElementById('historyList').innerHTML = '<div style="text-align:center;padding:2rem;color:#888;">Searching through time...</div>';
  document.getElementById('btnRestoreHistory').disabled = true;
  selectedHistoryId = null;
  
  // Create the Preview editor if it doesn't exist yet
  if (!historyEditor) {
    historyEditor = monaco.editor.create(document.getElementById('historyMonaco'), {
      value: '// Choose a version from the sidebar...',
      language: languageForFile(f.name),
      theme: 'joshuatex-light',
      readOnly: true,
      minimap: { enabled: false },
      fontFamily: "'JetBrains Mono', monospace",
    });
  } else {
    historyEditor.setValue('// Choose a version from the sidebar...');
    monaco.editor.setModelLanguage(historyEditor.getModel(), languageForFile(f.name));
  }
  
  try {
    const res = await fetch(`/api/projects/${projectId}/files/${encodeURIComponent(f.name)}/history`, {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    const data = await res.json();
    
    if (!res.ok) {
      throw new Error(data.detail || 'Error loading history from the server.');
    }
    
    if (!data || data.length === 0) {
      document.getElementById('historyList').innerHTML = '<div style="padding:1rem;color:#666;font-size:0.85rem;text-align:center;font-weight:bold;">No saved versions for this file yet. Wait for the next autosaves.</div>';
      return;
    }
    
    document.getElementById('historyList').innerHTML = data.map(h => {
      const d = new Date(h.saved_at + 'Z');
      const timeStr = d.toLocaleTimeString('en-US', {hour:'2-digit', minute:'2-digit'});
      const dateStr = d.toLocaleDateString('en-US', {day:'2-digit', month:'short'});
      const name = h.display_name || h.username;
      
      return `
        <div class="history-item" onclick="loadHistoryPreview(${h.id}, this)" style="padding:0.8rem;border-bottom:1px solid #eee;cursor:pointer;border-left:3px solid transparent;transition:all 0.1s;">
          <div style="font-weight:700;font-size:0.85rem;color:#111;">${dateStr} at ${timeStr}</div>
          <!-- Note: escapeHtml(name) is used here to prevent XSS -->
          <div style="font-size:0.75rem;color:#666;margin-top:2px;">By <strong>${escapeHtml(name)}</strong></div>
        </div>
      `;
    }).join('');
  } catch(err) {
    document.getElementById('historyList').innerHTML = `<div style="color:red;padding:1rem;">Error: ${err.message}</div>`;
  }
}

function closeHistoryModal() {
  document.getElementById('historyModal').style.display = 'none';
}

async function loadHistoryPreview(id, element) {
  document.querySelectorAll('.history-item').forEach(el => {
    el.style.background = 'transparent';
    el.style.borderLeftColor = 'transparent';
  });
  
  element.style.background = '#FFE8E8';
  element.style.borderLeftColor = '#E03030';
  
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
  
  if(!confirm('🚨 WARNING: This will replace the current text of this file with the version shown on screen. Do you really want to go back in time?')) return;
  
  const btn = document.getElementById('btnRestoreHistory');
  btn.disabled = true;
  btn.textContent = 'Restoring...';
  
  try {
    const res = await fetch(`/api/projects/${projectId}/files/${encodeURIComponent(f.name)}/restore/${selectedHistoryId}`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${token}` }
    });
    
    if(!res.ok) throw new Error('Error restoring the version.');
    
    closeHistoryModal();
    alert('Version restored successfully!');
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
    await fetch(`/api/projects/${projectId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ name: newName })
    });
    document.getElementById('statusText').textContent = 'Saved';
  } catch {
    document.getElementById('statusText').textContent = 'Error saving';
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

  document.getElementById('statusText').textContent = 'Loading...';
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
    document.getElementById('statusText').textContent = 'Syncing...';
    await collab.joinFile(f.name);
    document.getElementById('statusText').textContent = 'Ready';
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
      `Binary file: ${f.name} (Cannot be previewed)`;
  }
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
    await fetch(`/api/projects/${projectId}/files/${f.name}`, {
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
  let name = prompt('File name (e.g.: new.tex, script.nv):', defaultName);
  if (!name) return;
  
  name = name.replace(/\/+/g, '/').replace(/^\/+/, '');
  
  const defaultContent = name.endsWith('.m') || name.endsWith('.nv')
    ? '% new script\n'
    : '% New file';
    
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
    alert('Error creating file on server.');
  }
}

async function newFolder(basePath = '', event = null) {
  if (event) event.stopPropagation();
  
  const defaultName = basePath ? `${basePath}/new_folder` : 'new_folder';
  let name = prompt('Folder name (e.g.: images, chapters):', defaultName);
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
    document.getElementById('statusText').textContent = 'Folder created';
  } catch {
    alert('Error creating folder on server.');
  }
}

async function deleteFolder(folderPath, event) {
  if (event) event.stopPropagation();
  if (!confirm(`Are you sure you want to delete the folder '${folderPath}' and ALL its files?`)) return;

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
    document.getElementById('statusText').textContent = 'Folder deleted';
  } catch (err) {
    alert('Error deleting the folder.');
  }
}

async function handleFileUpload(event) {
  const uploaded = event.target.files;
  if (!uploaded?.length) return;
  document.getElementById('statusText').textContent = 'Uploading...';

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
      document.getElementById('statusText').textContent = 'Upload complete';
    } catch {
      alert('Error uploading file: ' + file.name);
      document.getElementById('statusText').textContent = 'Error';
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
  const newName = prompt(`Rename "${f.name}" to:`, f.name);
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
  if (!confirm(`Are you sure you want to permanently delete the file '${f.name}'?`)) return;

  document.getElementById('statusText').textContent = 'Deleting...';

  try {
    const res = await fetch(`/api/projects/${projectId}/files/${f.name}`, {
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

async function setMainFile() {
  const f = files[activeFile];
  if (!f || f.binary || !f.name.endsWith('.tex')) {
    alert('Only .tex files can be set as the main file.');
    return;
  }
  try {
    const res = await fetch(`/api/projects/${projectId}/set-main/${f.name}`, {
      method: 'PUT',
      headers: { 'Authorization': `Bearer ${token}` }
    });
    if (!res.ok) throw new Error('Error setting main file');
    files.forEach(file => file.is_main = false);
    f.is_main = true;
    renderFileTree();
    document.getElementById('statusText').textContent = `${f.name} is now the main file`;
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
            <span onclick="newFile('${fullPath}', event)" title="New file here" class="ftree-action">📄+</span>
            <span onclick="newFolder('${fullPath}', event)" title="New folder here" class="ftree-action">📁+</span>
            <span onclick="deleteFolder('${fullPath}', event)" title="Delete folder" class="ftree-action" style="font-size:1.1rem" onmouseover="this.style.color='#E03030'" onmouseout="this.style.color='#aaa'">×</span>
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
          ? `<span title="Main File" style="color:var(--red);font-size:1rem;margin-left:0.3rem;line-height:0;">★</span>`
          : '';

        const renameIcon = `
          <span onclick="promptRenameFile(${i}, event)" title="Rename" style="color:#aaa;font-size:0.85rem;margin-left:0.4rem;cursor:pointer;line-height:0;" onmouseover="this.style.color='#2563EB'" onmouseout="this.style.color='#aaa'">✎</span>`;

        const deleteIcon = !f.is_main
          ? `<span onclick="deleteFile(${i}, event)" title="Delete file" style="color:#aaa;font-size:1.1rem;margin-left:0.4rem;cursor:pointer;line-height:0;" onmouseover="this.style.color='#E03030'" onmouseout="this.style.color='#aaa'">×</span>`
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