const ALARM = 'refresh-billing';
const BILLING = 'https://platform.openai.com/settings/organization/billing/overview';
const isBilling = url => {
  try {
    const parsed = new URL(url);
    return parsed.origin === 'https://platform.openai.com'
      && parsed.pathname.replace(/\/$/, '') === '/settings/organization/billing/overview';
  } catch { return false; }
};
let inFlight = false;
// Pairing survives browser restarts; OpenAI login credentials are never read.
const ready = (async () => {
  await chrome.storage.local.setAccessLevel({accessLevel: 'TRUSTED_CONTEXTS'});
  const old = (await chrome.storage.session.get('connection')).connection;
  if (old && !(await chrome.storage.local.get('connection')).connection) {
    const {tabId, ...pairing} = old;
    await chrome.storage.local.set({connection: pairing});
    await chrome.storage.session.set({binding: {tabId}});
  }
  await chrome.storage.session.remove('connection');
})();

async function sendPayload(connection, payload) {
  const current = (await chrome.storage.local.get('connection')).connection;
  if (current?.generation !== connection.generation) throw new Error('连接已更换');
  let response;
  try {
    response = await fetch(`http://127.0.0.1:${connection.port}/balance`, {
      method: 'POST', headers: {'Content-Type': 'application/json', Authorization: `Bearer ${connection.token}`},
      body: JSON.stringify({...payload, client_version: chrome.runtime.getManifest().version}), credentials: 'omit', cache: 'no-store', redirect: 'error',
      signal: AbortSignal.timeout(5000)
    });
  } catch { throw new Error('等待浮条启动，将自动重连'); }
  if (response.status === 401) throw new Error('本机配对已更换，请重新复制连接码');
  if (!response.ok) throw new Error('本次余额已过期或读取失败，等待自动更新');
  return response.json();
}

const sendBalance = (connection, balance, sampledAt = Math.floor(Date.now() / 1000)) =>
  sendPayload(connection, {balance, sampled_at: sampledAt});
const sendState = (connection, state) => sendPayload(connection, {state});
function failure(state, message) { return Object.assign(new Error(message), {state}); }

async function ensureAlarm() {
  if (!(await chrome.alarms.get(ALARM))) await chrome.alarms.create(ALARM, {periodInMinutes: 1});
}

async function createMonitorTab(url = BILLING) {
  // Always use an independent window, even while ordinary browser windows are
  // open. Closing an ordinary window must not close the monitor's document.
  const window = await chrome.windows.create({url, state: 'minimized', focused: false});
  const tab = window?.tabs?.[0] || (window && (await chrome.tabs.query({windowId: window.id}))[0]);
  if (!tab) throw failure('page_missing', '后台余额窗口暂不可用，将自动重试');
  return tab;
}

async function boundTab() {
  const {binding} = await chrome.storage.session.get('binding');
  let previous;
  if (binding) {
    try {
      previous = await chrome.tabs.get(binding.tabId);
      if (binding.dedicated && isBilling(previous.url)) return previous;
    } catch {}
  }
  const tabs = (await chrome.tabs.query({url: 'https://platform.openai.com/*'})).filter(tab => isBilling(tab.url));
  let source = isBilling(previous?.url) ? previous : undefined;
  // After a browser restart, reuse a single minimized billing window instead
  // of creating another one. No user tabs are moved or closed.
  if (!source) {
    const monitors = [];
    for (const tab of tabs) {
      const window = await chrome.windows.get(tab.windowId, {populate: true});
      if (window.state === 'minimized' && window.tabs?.length === 1) monitors.push(tab);
    }
    if (monitors.length === 1) {
      const tab = monitors[0];
      await chrome.storage.session.set({binding: {tabId: tab.id, windowId: tab.windowId, dedicated: true}});
      return tab;
    }
    if (tabs.length > 1) throw failure('multiple_pages', '有多个余额页，请选中要同步的标签，在扩展中点击“使用当前页”');
    source = tabs[0];
  }
  // Prefer an already-open billing page after login opens a new tab. A bound
  // tab that navigated to an unrelated site must not block monitoring forever.
  if (!source && previous && binding.dedicated) {
    try {
      const url = new URL(previous.url);
      if (url.protocol === 'https:' && ['auth.openai.com', 'auth0.openai.com', 'accounts.google.com'].includes(url.hostname))
        return previous;
    } catch {}
  }
  // An existing dedicated monitor that finishes login in another tab can be
  // navigated back safely; it is owned by this extension, not the user's tab.
  if (source && previous && binding.dedicated) {
    const tab = await chrome.tabs.update(previous.id, {url: source.url});
    return tab;
  }
  const tab = await createMonitorTab(source?.url || BILLING);
  await chrome.storage.session.set({binding: {tabId: tab.id, windowId: tab.windowId, dedicated: true}});
  return tab;
}

function waitForTab(tabId, reload) {
  return new Promise((resolve, reject) => {
    const cleanup = () => { clearTimeout(timeout); chrome.tabs.onUpdated.removeListener(listener); };
    const listener = (id, change) => {
      if (id === tabId && change.status === 'complete') { cleanup(); resolve(); }
    };
    const timeout = setTimeout(() => {
      cleanup(); reject(failure('network_error', '页面加载超时，网络恢复后自动重试'));
    }, 20000);
    chrome.tabs.onUpdated.addListener(listener);
    const action = reload ? chrome.tabs.reload(tabId) : chrome.tabs.get(tabId).then(tab => {
      if (tab.status === 'complete') { cleanup(); resolve(); }
    });
    action.catch(error => { cleanup(); reject(error); });
  });
}

async function readTab(tab, reload) {
  if (tab.status !== 'complete') {
    await waitForTab(tab.id, false);
    tab = await chrome.tabs.get(tab.id);
  }
  if (!isBilling(tab.url)) throw failure('login_required', '请在后台余额标签页完成登录；返回余额页后自动恢复');
  if (reload) await waitForTab(tab.id, true);
  if (!isBilling((await chrome.tabs.get(tab.id)).url))
    throw failure('login_required', '网页已跳转，请完成登录并返回余额页；无需重新配对');
  const results = await chrome.scripting.executeScript({target: {tabId: tab.id}, files: ['reader.js']});
  await chrome.scripting.executeScript({target: {tabId: tab.id}, files: ['watcher.js']});
  return results[0]?.result?.balance;
}

async function syncedStatus(balance) {
  const message = `已同步 $${balance} · ${new Date().toLocaleTimeString()} · 自动更新`;
  await chrome.storage.session.set({status: message});
  return {ok: true, message};
}

async function sync(reload = false) {
  await ready;
  if (inFlight) return {ok: false, message: '正在自动更新，请稍候'};
  inFlight = true;
  let connection;
  try {
    ({connection} = await chrome.storage.local.get('connection'));
    if (!connection) return {ok: false, message: '请先配对一次，之后自动连接'};
    await ensureAlarm();
    await sendState(connection, 'refreshing');
    const tab = await boundTab();
    const balance = await readTab(tab, reload);
    if (typeof balance !== 'string') throw failure('read_failed', '等待网页余额，登录或页面恢复后自动更新');
    await sendBalance(connection, balance);
    return await syncedStatus(balance);
  } catch (error) {
    if (connection) {
      try { await sendState(connection, error.state || 'read_failed'); } catch {}
    }
    const message = error.message || '等待自动重试';
    await chrome.storage.session.set({status: message});
    return {ok: false, message};
  } finally { inFlight = false; }
}

async function watchBalance(message, sender) {
  await ready;
  if (inFlight || sender.frameId !== 0 || !isBilling(sender.url)) return {ok: false};
  const {binding} = await chrome.storage.session.get('binding');
  const {connection} = await chrome.storage.local.get('connection');
  if (!connection || sender.tab.id !== binding?.tabId) return {ok: false};
  const tab = await chrome.tabs.get(binding.tabId);
  if (!isBilling(tab.url)) return {ok: false};
  const {balance, sampled_at: sampledAt} = message;
  if ((balance !== null && (typeof balance !== 'string' || !/^-?\d{1,12}(?:\.\d{1,8})?$/.test(balance)))
      || !Number.isInteger(sampledAt) || Date.now() / 1000 - sampledAt > 145
      || sampledAt > Date.now() / 1000) return {ok: false};
  try {
    await sendBalance(connection, balance, sampledAt);
    if (balance !== null) return await syncedStatus(balance);
    await sendState(connection, 'read_failed');
    return {ok: true};
  } catch { return {ok: false}; }
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id) return;
  if (sender.tab) {
    if (message.type !== 'watch_balance') return;
    watchBalance(message, sender).then(respond).catch(() => respond({ok: false}));
    return true;
  }
  const task = async () => {
    await ready;
    if (inFlight) return {ok: false, message: '正在更新，请稍候'};
    if (message.type === 'connect' || message.type === 'use_current') {
      const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
      if (!tab || !isBilling(tab.url)) return {ok: false, message: '请先选中 OpenAI 余额页标签'};
      if (message.confirmed !== true) return {ok: false, message: '请确认将同步浏览器当前选择的组织'};
      if (message.type === 'connect') {
        const match = String(message.code || '').trim().match(/^(\d{1,5}):([a-f0-9]{64})$/);
        if (!match || +match[1] < 1 || +match[1] > 65535) return {ok: false, message: '请粘贴完整连接码'};
        await chrome.storage.local.set({connection: {port: +match[1], token: match[2], generation: crypto.randomUUID()}});
      }
      await chrome.storage.session.set({binding: {tabId: tab.id}});
      return sync();
    }
    if (message.type === 'sync') return sync(true);
    if (message.type === 'open_monitor') {
      if (!(await chrome.storage.local.get('connection')).connection) return {ok: false, message: '请先配对一次'};
      const tab = await boundTab();
      await chrome.windows.update(tab.windowId, {state: 'normal', focused: true});
      await chrome.tabs.update(tab.id, {active: true});
      return {ok: true, message: '已打开独立余额窗口；完成登录后可最小化，让它继续查询'};
    }
    if (message.type === 'disconnect') {
      const {connection} = await chrome.storage.local.get('connection');
      if (connection) { try { await sendState(connection, 'disconnected'); } catch {} }
      await chrome.storage.local.remove('connection');
      await chrome.storage.session.remove('binding');
      await chrome.alarms.clear(ALARM);
      await chrome.storage.session.set({status: '已取消配对，自动更新已停止'});
      return {ok: true, message: '已取消配对，自动更新已停止'};
    }
    return {ok: false, message: '未知操作'};
  };
  task().then(respond).catch(() => respond({ok: false, message: '暂时无法连接，将自动重试'}));
  return true;
});
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === ALARM) void sync(true); });
chrome.tabs.onRemoved.addListener(async tabId => {
  await ready;
  const {binding} = await chrome.storage.session.get('binding');
  if (binding?.tabId !== tabId) return;
  const {connection} = await chrome.storage.local.get('connection');
  if (connection) { try { await sendState(connection, 'page_missing'); } catch {} }
  await chrome.storage.session.remove('binding');
  await chrome.storage.session.set({status: '余额页已关闭，将在下次查询自动恢复后台标签页'});
});
chrome.tabs.onUpdated.addListener(async (tabId, change) => {
  await ready;
  const {binding} = await chrome.storage.session.get('binding');
  if (binding?.tabId !== tabId) return;
  if (change.url && !isBilling(change.url)) {
    const {connection} = await chrome.storage.local.get('connection');
    if (connection) { try { await sendState(connection, 'login_required'); } catch {} }
    await chrome.storage.session.set({status: '等待余额页重新登录；配对已保留'});
  }
  // Refreshing preserves a still-fresh value; it never advances its timestamp.
  if (change.status === 'loading' && !inFlight) {
    const {connection} = await chrome.storage.local.get('connection');
    if (connection) { try { await sendState(connection, 'refreshing'); } catch {} }
  }
  if (change.status === 'complete' && !inFlight) void sync();
});
chrome.runtime.onStartup.addListener(() => { void sync(); });
chrome.runtime.onInstalled.addListener(() => { void sync(); });
void ready.then(async () => {
  if ((await chrome.storage.local.get('connection')).connection) await ensureAlarm();
});
