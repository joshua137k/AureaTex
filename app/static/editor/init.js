

require.config({ paths: { vs: 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.47.0/min/vs' } });

require(['vs/editor/editor.main'], async () => {
  initEditor();

  collab = new CollabClient(editor, projectId, token);
  collab.connect();

  await loadProject();
  await loadFiles();

  if (files.length > 0) {
    const mainIdx = files.findIndex(f => f.is_main);
    await switchFile(mainIdx >= 0 ? mainIdx : 0);
    compilePDF();
  }
});