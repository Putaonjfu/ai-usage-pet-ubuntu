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
  let alarm, available = true, allowed = true;
  const actions = [];
  const sent = [];
  const windowInfo = id => {
    if (!windows.has(id)) throw Error('No window');
    return {...windows.get(id), tabs: [...tabs.values()].filter(tab => tab.windowId === id)};
  };
  const chrome = {
    runtime: {id: 'a'.repeat(32), getManifest: () => ({version: '1.4.0'}), onMessage: event('message'),
      onStartup: event('startup'), onInstalled: event('installed')},
    storage: {local: storage(local), session: storage(session)},
    windows: {get: async id => windowInfo(id), create: async () => {
      throw Error('Automatic window creation is forbidden');
    }, update: async (id, options) => {
      actions.push('focus'); Object.assign(windows.get(id), options); return windowInfo(id);
    }},
    tabs: {create: async options => {
      actions.push('create');
      const tab = {id: nextTab++, windowId: 1, status: 'complete', ...options};
      tabs.set(tab.id, tab); return tab;
    }, get: async id => { if (!tabs.has(id)) throw Error('No tab'); return tabs.get(id); },
      query: async options => [...tabs.values()].filter(tab =>
        (!options.url || tab.url.startsWith('https://platform.openai.com/'))
        && (!options.windowId || tab.windowId === options.windowId)
        && (!options.active || windows.get(tab.windowId)?.focused)),
      update: async (id, options) => {
        actions.push('update'); Object.assign(tabs.get(id), options);
        if (options.url) await emit('updated', id, {status: 'complete'});
        return tabs.get(id);
      },
      reload: async id => { actions.push('reload'); await emit('updated', id, {status: 'complete'}); },
      onRemoved: event('removed'), onUpdated: event('updated')},
    scripting: {executeScript: async ({target, files}) => {
      actions.push('read');
      return files[0] === 'reader.js'
        ? [{result: await read('API credit balance\nUS$12.34', tabs.get(target.tabId).url)}] : [];
    }},
    alarms: {get: async () => alarm, create: async (name, options) => { alarm = options; },
      clear: async () => { alarm = null; }, onAlarm: event('alarm')}
  };
  const context = vm.createContext({chrome, URL, AbortSignal, crypto: require('node:crypto').webcrypto,
    setTimeout, clearTimeout,
    fetch: async (target, options) => {
      if (!available) throw Error('offline');
      if (target.endsWith('/status')) {
        assert.equal(target, 'http://127.0.0.1:12345/status');
        return {ok: true, status: 200, json: async () => ({monitoring_allowed: allowed, monitoring_mode: allowed ? 'api' : 'chatgpt'})};
      }
      assert.equal(target, 'http://127.0.0.1:12345/balance');
      assert.equal(options.credentials, 'omit'); assert.equal(options.redirect, 'error');
      const payload = JSON.parse(options.body);
      assert.equal(payload.client_version, '1.4.0');
      sent.push(payload);
      return {ok: true, status: 200, json: async () => ({refresh_minutes: 1})};
    }});
  vm.runInContext(background, context);
  const settle = () => new Promise(resolve => setImmediate(resolve));
  const message = (data, sender = {id: chrome.runtime.id}) => new Promise(resolve => {
    const handled = [...events.message][0](data, sender, resolve);
    if (!handled) resolve({ok: false});
  });
  return {local, session, tabs, windows, sent, actions, emit, message, settle, chrome,
    pause: value => { allowed = !value; },
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
  assert.equal(app.session.binding.tabId, 7);
  assert.equal(app.tabs.size, 1);
  assert.equal(app.windows.size, 1);
  assert.deepEqual(app.actions, ['read', 'read']); // No focus, tab or window creation.
  const sender = {id: app.chrome.runtime.id, tab: {id: 7}, url: billing, frameId: 0};
  assert.equal((await app.message({type: 'watch_balance', balance: '11.25', sampled_at: Math.floor(Date.now()/1000)}, sender)).ok, true);
  assert.equal(app.sent.at(-1).balance, '11.25');
  assert.equal((await app.message({type: 'watch_balance', balance: '99', sampled_at: 1}, sender)).ok, false);
  // Account login stops all reloads, scripts and watcher deliveries.
  app.pause(true);
  app.actions.length = 0;
  const receiptCount = app.sent.length;
  assert.equal((await app.message({type: 'sync'})).ok, false);
  assert.equal((await app.message({type: 'watch_balance', balance: '11.25', sampled_at: Math.floor(Date.now()/1000)}, sender)).ok, false);
  await app.emit('alarm', {name: 'refresh-billing'}); await app.settle();
  await app.emit('startup'); await app.settle();
  assert.equal(app.sent.length, receiptCount);
  assert.deepEqual(app.actions, []);
  assert.match(app.session.status, /账号登录/);
  app.pause(false);
  assert.equal((await app.message({type: 'sync'})).ok, true);
  assert.ok(app.actions.includes('reload'));
  // Offline host never triggers browser actions, and pairing survives.
  app.actions.length = 0;
  app.offline(true);
  assert.equal((await app.message({type: 'sync'})).ok, false);
  assert.deepEqual(app.actions, []);
  assert.ok(app.local.connection); assert.ok(app.alarm());
  app.offline(false);
  // Login navigation does not redirect or create a replacement tab/window.
  app.tabs.get(7).url = 'https://auth.openai.com/log-in';
  await app.emit('updated', 7, {url: 'https://auth.openai.com/log-in'});
  assert.equal((await app.message({type: 'sync'})).ok, false);
  assert.deepEqual(app.actions, []);
  assert.equal(app.tabs.get(7).url, 'https://auth.openai.com/log-in');
  app.tabs.get(7).url = billing;
  assert.equal((await app.message({type: 'sync'})).ok, true);
  // Closing the last page leaves it closed across alarms, startup and updates.
  app.tabs.delete(7); app.windows.delete(1);
  await app.emit('removed', 7);
  assert.match(app.session.status, /不会自动/);
  app.actions.length = 0;
  assert.equal((await app.message({type: 'sync'})).ok, false);
  await app.emit('alarm', {name: 'refresh-billing'}); await app.settle();
  await app.emit('startup'); await app.settle();
  await app.emit('installed'); await app.settle();
  assert.deepEqual(app.actions, []);
  assert.equal(app.tabs.size, 0); assert.equal(app.windows.size, 0);
  assert.ok(app.local.connection);
  // Legacy dedicated binding and restart also cannot bring a closed page back.
  const restored = harness(app.local, {binding: {tabId: 7, windowId: 1, dedicated: true}}, {tabs: app.tabs, windows: app.windows});
  await restored.settle(); await restored.emit('startup'); await restored.settle();
  assert.deepEqual(restored.actions, []);
  assert.equal(restored.tabs.size, 0);
  // A page reopened by the user is reused without focus or cloning.
  restored.windows.set(3, {id: 3, focused: true, state: 'normal'});
  restored.tabs.set(9, {id: 9, windowId: 3, status: 'complete', url: billing});
  assert.equal((await restored.message({type: 'sync'})).ok, true);
  assert.equal(restored.session.binding.tabId, 9);
  assert.ok(!restored.actions.some(item => ['focus', 'create', 'update'].includes(item)));
  // Only an explicit popup click can focus an existing page or open a new one.
  assert.equal((await restored.message({type: 'open_billing'})).ok, true);
  assert.equal(restored.actions.at(-2), 'focus');
  restored.tabs.delete(9);
  assert.equal((await restored.message({type: 'open_billing'})).ok, true);
  assert.equal(restored.actions.at(-1), 'create');
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
  console.log('No automatic windows, account pause, closed-page/startup recovery, explicit open, pairing migration and watcher checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
