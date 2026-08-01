

async function openShareModal() {
  const modal = document.getElementById('shareModal');
  const input = document.getElementById('shareLinkInput');
  input.value = 'A gerar link...';
  modal.style.display = 'flex';

  try {
    const res = await fetch(`/api/projects/${projectId}/invite`, {
      method : 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body   : JSON.stringify({ role: 'editor' })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Erro ao gerar convite.');
    input.value = data.url;
  } catch (e) {
    input.value = 'Sem permissão ou erro de rede.';
    if (e.message.includes('Sem permissão')) {
      alert('Apenas o dono do projeto pode gerar links de convite.');
      closeShareModal();
    }
  }
}

function closeShareModal() {
  document.getElementById('shareModal').style.display = 'none';
}

function copyShareLink() {
  const input = document.getElementById('shareLinkInput');
  input.select();
  document.execCommand('copy');
  const btn = document.querySelector('.modal-actions .btn-black');
  const old = btn.textContent;
  btn.textContent = 'Copiado ✓';
  setTimeout(() => btn.textContent = old, 2000);
}
