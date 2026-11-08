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
    if (res.status === 404 || res.status === 403) {
      closeShareModal();
      alert('Only the project owner can generate invite links.');
      return;
    }
    if (!res.ok) throw new Error(await apiError(res, 'Error generating invite.'));
    const data = await res.json();
    input.value = data.url;
  } catch (e) {
    input.value = e.message || 'Network error.';
  }
}

function closeShareModal() {
  document.getElementById('shareModal').style.display = 'none';
}

function copyShareLink() {
  const input = document.getElementById('shareLinkInput');
  input.select();
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(input.value).catch(() => document.execCommand('copy'));
  } else {
    document.execCommand('copy');
  }
  const btn = document.querySelector('.modal-actions .btn-black');
  const old = btn.textContent;
  btn.textContent = 'Copied ✓';
  setTimeout(() => btn.textContent = old, 2000);
}