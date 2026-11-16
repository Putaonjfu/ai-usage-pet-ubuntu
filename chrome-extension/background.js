const ALARM = 'refresh-billing';
const isBilling = url => {
  try {
    const parsed = new URL(url);
    return parsed.origin === 'https://platform.openai.com'
      && parsed.pathname.replace(/\/$/, '') === '/settings/organization/billing/overview';
  } catch { return false; }
};
let inFlight = false;

async function sendBalance(connection, balance) {
  const current = (await chrome.storage.session.get('connection')).connection;
  if (current?.generation !== connection.generation) throw new Error('连接已更换');
  let response;
  try {
    response = await fetch(`http://127.0.0.1:${connection.port}/balance`, {
      method: 'POST', headers: {'Content-Type': 'application/json', Authorization: `Bearer ${connection.token}`},
      body: JSON.stringify({balance}), credentials: 'omit', cache: 'no-store', redirect: 'error',
      signal: AbortSignal.timeout(5000)
    });
  } catch { throw new Error('无法连接浮条，请确认小工具已启动，并重新复制连接码'); }
  if (!response.ok) throw new Error('连接码失效，请从小工具重新复制');
  return response.json();
}

async function sync(reload = false) {
  if (inFlight) return {ok: false, message: '正在读取，请稍候'};
  inFlight = true;
  const {connection} = await chrome.storage.session.get('connection');
  try {
    if (!connection) throw new Error('请先连接桌面浮条');
    // Remove the old value before any reload, redirect, login failure or parse failure.
    const config = await sendBalance(connection, null);
    const tab = await chrome.tabs.get(connection.tabId);
    if (!isBilling(tab.url)) throw new Error('请在原标签页登录并返回 OpenAI 余额页，再重新确认连接');
    if (reload) {
      await reloadBalanceTab(connection.tabId);
    }
    const result = await chrome.scripting.executeScript({target: {tabId: connection.tabId}, files: ['reader.js']});
    const balance = result[0]?.result?.balance;
    if (typeof balance !== 'string') throw new Error('未识别到余额，请检查登录、组织和页面是否加载完成');
    await sendBalance(connection, balance);
    const minutes = Math.max(1, Math.min(60, config.refresh_minutes || 5));
    await chrome.alarms.create(ALARM, {periodInMinutes: minutes});
    const message = `已同步 $${balance} · ${new Date().toLocaleTimeString()}`;
    await chrome.storage.session.set({status: message});
    return {ok: true, message};
  } catch (error) {
    const message = error.message || '余额读取失败';
    await chrome.storage.session.set({status: message});
    // Do not keep reloading a tab after it navigates away or needs login.
    await chrome.alarms.clear(ALARM);
    return {ok: false, message};
  } finally { inFlight = false; }
}

function reloadBalanceTab(tabId) {
  // Subscribe before reload so an old document's complete status cannot pass.
  return new Promise((resolve, reject) => {
    const cleanup = () => { clearTimeout(timeout); chrome.tabs.onUpdated.removeListener(listener); };
    const listener = (id, change) => {
      if (id === tabId && change.status === 'complete') { cleanup(); resolve(); }
    };
    const timeout = setTimeout(() => {
      cleanup(); reject(new Error('页面加载超时，请在浏览器中检查网络后重新连接'));
    }, 20000);
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.reload(tabId).catch(error => { cleanup(); reject(error); });
  });
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || sender.tab) return;
  const task = async () => {
    if (message.type === 'connect') {
      if (inFlight) return {ok: false, message: '正在更新，请稍候再连接'};
      const match = String(message.code || '').trim().match(/^(\d{1,5}):([a-f0-9]{64})$/);
      if (!match || +match[1] < 1 || +match[1] > 65535 || message.confirmed !== true)
        return {ok: false, message: '请粘贴完整连接码，并确认当前组织'};
      const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
      if (!tab || !isBilling(tab.url)) return {ok: false, message: '请先选中 OpenAI 余额页标签'};
      await chrome.alarms.clear(ALARM);
      await chrome.storage.session.set({connection: {port: +match[1], token: match[2], tabId: tab.id,
        generation: crypto.randomUUID()}});
      return sync();
    }
    if (message.type === 'sync') return sync(true);
    if (message.type === 'disconnect') {
      if (inFlight) return {ok: false, message: '正在更新，请稍候再断开'};
      const {connection} = await chrome.storage.session.get('connection');
      if (connection) { try { await sendBalance(connection, null); } catch {} }
      await chrome.storage.session.remove('connection');
      await chrome.alarms.clear(ALARM);
      await chrome.storage.session.set({status: '已断开连接'});
      return {ok: true, message: '已断开连接'};
    }
    return {ok: false, message: '未知操作'};
  };
  task().then(respond).catch(() => respond({ok: false, message: '连接失败，请重试'}));
  return true;
});

chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === ALARM) void sync(true); });
chrome.tabs.onRemoved.addListener(async tabId => {
  const {connection} = await chrome.storage.session.get('connection');
  if (connection?.tabId !== tabId) return;
  try { await sendBalance(connection, null); } catch {}
  await chrome.storage.session.remove('connection');
  await chrome.alarms.clear(ALARM);
  await chrome.storage.session.set({status: '余额标签页已关闭，请重新连接'});
});
chrome.tabs.onUpdated.addListener(async (tabId, change) => {
  if (!change.url || isBilling(change.url)) return;
  const {connection} = await chrome.storage.session.get('connection');
  if (connection?.tabId !== tabId) return;
  try { await sendBalance(connection, null); } catch {}
  await chrome.storage.session.remove('connection');
  await chrome.alarms.clear(ALARM);
  await chrome.storage.session.set({status: '已离开余额页或需要登录，请返回余额页后重新连接'});
});
