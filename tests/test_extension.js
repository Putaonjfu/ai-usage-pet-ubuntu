// Offline lifecycle checks; no browser profile, credentials or website are used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.join(__dirname, '..', 'chrome-extension');
const reader = fs.readFileSync(path.join(root, 'reader.js'), 'utf8');
const background = fs.readFileSync(path.join(root, 'background.js'), 'utf8');
const billing = 'https://platform.openai.com/settings/organization/billing/overview';
async function read(text, url = billing) {
  return vm.runInNewContext(reader, {
    location: new URL(url), document: {body: {innerText: text}}, setTimeout: resolve => resolve()
  });
}
function harness(local = {}, session = {}, restore) {
  const events = {};
  const event = name => {
    events[name] = new Set();
    return {addListener: fn => events[name].add(fn), removeListener: fn => events[name].delete(fn)};
  };
  const emit = async (name, ...args) => { for (const fn of [...events[name]]) await fn(...args); };
  const storage = store => ({get: async key => ({[key]: store[key]}), set: async data => Object.assign(store, data),
    remove: async key => { delete store[key]; }, setAccessLevel: async data => assert.equal(data.accessLevel, 'TRUSTED_CONTEXTS')});
  const tabs = restore?.tabs || new Map([[7, {id: 7, url: billing, status: 'complete', windowId: 1}]]);
  const windows = restore?.windows || new Map([[1, {id: 1, state: 'normal', focused: true}]]);
  let nextTab = Math.max(7, ...tabs.keys()) + 1;
  let nextWindow = Math.max(1, ...windows.keys()) + 1;
  let alarm, available = true;
  const sent = [];
  const windowInfo = id => {
    if (!windows.has(id)) throw Error('No window');
    return {...windows.get(id), tabs: [...tabs.values()].filter(tab => tab.windowId === id)};
  };
  const chrome = {
    runtime: {id: 'a'.repeat(32), getManifest: () => ({version: '1.3.0'}), onMessage: event('message'),
      onStartup: event('startup'), onInstalled: event('installed')},
    storage: {local: storage(local), session: storage(session)},
    windows: {get: async id => windowInfo(id), create: async options => {
      assert.equal(options.state, 'minimized'); assert.equal(options.focused, false);
      const windowId = nextWindow++;
      windows.set(windowId, {id: windowId, state: options.state, focused: options.focused});
      const tab = {id: nextTab++, url: options.url, status: 'complete', windowId};
      tabs.set(tab.id, tab); return windowInfo(windowId);
    }, update: async (id, options) => { Object.assign(windows.get(id), options); return windowInfo(id); }},
    tabs: {get: async id => { if (!tabs.has(id)) throw Error('No tab'); return tabs.get(id); },
      query: async options => [...tabs.values()].filter(tab =>
        (!options.url || tab.url.startsWith('https://platform.openai.com/'))
        && (!options.windowId || tab.windowId === options.windowId)
        && (!options.active || windows.get(tab.windowId)?.focused)),
      update: async (id, options) => {
        Object.assign(tabs.get(id), options);
        if (options.url) await emit('updated', id, {status: 'complete'});
        return tabs.get(id);
      },
      reload: async id => { await emit('updated', id, {status: 'complete'}); },
      onRemoved: event('removed'), onUpdated: event('updated')},
    scripting: {executeScript: async ({target, files}) => files[0] === 'reader.js'
      ? [{result: await read('API credit balance\nUS$12.34', tabs.get(target.tabId).url)}] : []},
    alarms: {get: async () => alarm, create: async (name, options) => { alarm = options; },
      clear: async () => { alarm = null; }, onAlarm: event('alarm')}
  };
  const context = vm.createContext({chrome, URL, AbortSignal, crypto: require('node:crypto').webcrypto,
    setTimeout, clearTimeout,
    fetch: async (target, options) => {
      if (!available) throw Error('offline');
      assert.equal(target, 'http://127.0.0.1:12345/balance');
      assert.equal(options.credentials, 'omit'); assert.equal(options.redirect, 'error');
      const payload = JSON.parse(options.body);
      assert.equal(payload.client_version, '1.3.0');
      sent.push(payload);
      return {ok: true, status: 200, json: async () => ({refresh_minutes: 1})};
    }});
  vm.runInContext(background, context);
  const settle = () => new Promise(resolve => setImmediate(resolve));
  const message = (data, sender = {id: chrome.runtime.id}) => new Promise(resolve => {
    const handled = [...events.message][0](data, sender, resolve);
    if (!handled) resolve({ok: false});
  });
  return {local, session, tabs, windows, sent, emit, message, settle, chrome,
    alarm: () => alarm, offline: value => { available = !value; }};
}
(async () => {
  for (const [text, expected] of [
    ['API credit balance\nUS$12.34\n下一笔到期额度：US$5.00', '12.34'],
    ['Credit balance: −$0.50', '-0.5'], ['Credit balance\n$1,234.56', '1234.56'],
    ['Credit balance\n$0.00', '0'], ['Usage this month\n$99.00', null],
    ['Credit balance\n$12.34\nCredit balance\n$99.00', null]]) {
    assert.equal((await read(text)).balance, expected);
  }
  assert.equal((await read('Credit balance\n$12.34', 'https://platform.openai.com/api-keys')).balance, null);
  const app = harness();
  const code = '12345:' + 'f'.repeat(64);
  await app.settle();
  assert.equal((await app.message({type: 'connect', code, confirmed: false})).ok, false);
  assert.equal(app.sent.length, 0);
  assert.equal((await app.message({type: 'connect', code, confirmed: true})).ok, true);
  assert.deepEqual(app.sent.map(item => item.state || item.balance), ['refreshing', '12.34']);
  assert.equal(app.alarm().periodInMinutes, 1);
  let monitorId = app.session.binding.tabId;
  assert.notEqual(monitorId, 7);
  assert.equal(app.session.binding.dedicated, true);
  assert.equal(app.windows.get(app.session.binding.windowId).state, 'minimized');
  assert.equal(app.tabs.get(7).windowId, 1); // Original tab is neither moved nor closed.
  const sender = {id: app.chrome.runtime.id, tab: {id: monitorId}, url: billing, frameId: 0};
  assert.equal((await app.message({type: 'watch_balance', balance: '11.25', sampled_at: Math.floor(Date.now()/1000)}, sender)).ok, true);
  assert.equal(app.sent.at(-1).balance, '11.25');
  assert.equal((await app.message({type: 'watch_balance', balance: '99', sampled_at: 1}, sender)).ok, false);
  // Closing the ordinary browser window cannot close the independent monitor.
  app.tabs.delete(7); app.windows.delete(1);
  await app.emit('removed', 7);
  assert.equal(app.session.binding.tabId, monitorId);
  assert.equal((await app.message({type: 'sync'})).ok, true);
  assert.equal(app.session.binding.tabId, monitorId);
  assert.equal(app.sent.at(-1).balance, '12.34');
  // Widget restarts/network failures retain pairing and the retry alarm.
  app.offline(true);
  assert.equal((await app.message({type: 'sync'})).ok, false);
  assert.ok(app.local.connection); assert.ok(app.alarm());
  app.offline(false);
  assert.equal((await app.message({type: 'sync'})).ok, true);
  // Login expiry preserves the dedicated window and pairing.
  app.tabs.get(monitorId).url = 'https://auth.openai.com/log-in';
  await app.emit('updated', monitorId, {url: 'https://auth.openai.com/log-in'});
  assert.equal(app.sent.at(-1).state, 'login_required');
  assert.equal((await app.message({type: 'sync'})).ok, false);
  assert.ok(app.local.connection); assert.ok(app.alarm());
  app.tabs.get(monitorId).url = billing;
  await app.emit('updated', monitorId, {status: 'complete'}); await app.settle();
  assert.equal(app.sent.at(-1).balance, '12.34');
  // If login completes in a second tab, return the owned monitor to billing.
  app.tabs.get(monitorId).url = 'https://auth.openai.com/log-in';
  app.windows.set(30, {id: 30, state: 'normal', focused: true});
  app.tabs.set(99, {id: 99, url: billing, status: 'complete', windowId: 30});
  assert.equal((await app.message({type: 'sync'})).ok, true);
  assert.equal(app.session.binding.tabId, monitorId);
  assert.equal(app.tabs.get(monitorId).url, billing);
  // Session IDs can be lost on restart; an existing single-page minimized
  // monitor is reused without cloning additional windows.
  const restored = harness(app.local, {}, {tabs: app.tabs, windows: app.windows});
  await restored.settle(); await restored.emit('startup'); await restored.settle();
  assert.equal(restored.session.binding.tabId, monitorId);
  assert.equal(restored.windows.size, 2);
  // Closed monitor is recreated minimized on a later retry.
  restored.windows.delete(restored.session.binding.windowId);
  restored.tabs.delete(monitorId); await restored.emit('removed', monitorId);
  assert.equal((await restored.message({type: 'sync'})).ok, true);
  monitorId = restored.session.binding.tabId;
  assert.notEqual(monitorId, 99);
  assert.equal(restored.windows.get(restored.session.binding.windowId).state, 'minimized');
  assert.equal((await restored.message({type: 'open_monitor'})).ok, true);
  assert.equal(restored.windows.get(restored.session.binding.windowId).state, 'normal');
  assert.equal((await restored.message({type: 'disconnect'})).ok, true);
  assert.equal(restored.local.connection, undefined); assert.equal(restored.alarm(), null);
  const migrated = harness({}, {connection: {port: 12345, token: 'f'.repeat(64), tabId: 7, generation: 'old'}});
  await migrated.settle(); assert.equal(migrated.local.connection.generation, 'old');
  // Actual DOM watcher debounces changes and emits changed money only.
  let changed, scheduled, value = '12.34';
  const pushes = [];
  const watchContext = vm.createContext({__usagePetReadBalance: () => value, document: {body: {}},
    MutationObserver: class { constructor(fn) { changed = fn; } observe() {} },
    setTimeout: fn => { scheduled = fn; }, clearTimeout: () => {},
    chrome: {runtime: {sendMessage: async data => { pushes.push(data); }}}});
  const watchScript = fs.readFileSync(path.join(root, 'watcher.js'), 'utf8');
  vm.runInContext(watchScript, watchContext); assert.equal(pushes[0].balance, '12.34');
  value = '10.25'; changed(); scheduled(); assert.equal(pushes.at(-1).balance, '10.25');
  changed(); scheduled(); assert.equal(pushes.length, 2);
  value = null; changed(); scheduled(); assert.equal(pushes.at(-1).balance, null);
  vm.runInContext(watchScript, watchContext); assert.equal(pushes.length, 3);
  console.log('Independent window, ordinary-window close, restart reuse, login recovery, diagnostics version and watcher checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
