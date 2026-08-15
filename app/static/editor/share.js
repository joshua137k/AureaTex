async function openShareModal() {
  const modal = document.getElementById('shareModal');
  const input = document.getElementById('shareLinkInput');
  input.value = 'Generating link...';
  modal.style.display = 'flex';

  try {
    const res = await fetch(`/api/projects/${projectId}/invite`, {
      method : 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body   : JSON.stringify({ role: 'editor' })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Error generating invite.');
    input.value = data.url;
  } catch (e) {
    input.value = 'No permission or network error.';
    if (e.message.includes('No permission')) {
      alert('Only the project owner can generate invite links.');
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
  btn.textContent = 'Copied ✓';
  setTimeout(() => btn.textContent = old, 2000);
}