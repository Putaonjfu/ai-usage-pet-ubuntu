const status = document.getElementById('status');
chrome.storage.session.get('status').then(data => { status.textContent = data.status || '尚未连接'; });
for (const type of ['connect', 'sync', 'disconnect', 'use_current', 'open_monitor']) {
  document.getElementById(type).addEventListener('click', async () => {
    const buttons = document.querySelectorAll('button');
    buttons.forEach(button => { button.disabled = true; });
    status.textContent = '正在处理…';
    try {
      const result = await chrome.runtime.sendMessage({type,
        code: document.getElementById('code').value,
        confirmed: document.getElementById('confirmed').checked});
      status.textContent = result?.message || '连接失败';
      if (type === 'connect' && result?.ok) document.getElementById('code').value = '';
    } catch { status.textContent = '扩展已更新，请关闭此窗口后重试'; }
    finally { buttons.forEach(button => { button.disabled = false; }); }
  });
}
