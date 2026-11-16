// Lifecycle checks use fake browser APIs, DOM text and local storage only.
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
function harness(local = {}, session = {}) {
  const events = {};
  const event = name => {
    events[name] = new Set();
    return {addListener: fn => events[name].add(fn), removeListener: fn => events[name].delete(fn)};
  };
  const emit = async (name, ...args) => { for (const fn of [...events[name]]) await fn(...args); };
  const storage = store => ({get: async key => ({[key]: store[key]}), set: async data => Object.assign(store, data),
    remove: async key => { delete store[key]; }, setAccessLevel: async data => assert.equal(data.accessLevel, 'TRUSTED_CONTEXTS')});
  const tabs = new Map([[7, {id: 7, url: billing, status: 'complete'}]]);
  let alarm, available = true;
  const sent = [];
  const chrome = {
    runtime: {id: 'a'.repeat(32), onMessage: event('message'), onStartup: event('startup'), onInstalled: event('installed')},
    storage: {local: storage(local), session: storage(session)},
    windows: {getAll: async () => tabs.size ? [{id: 1}] : [], create: async options => {
      assert.equal(options.state, 'minimized'); assert.equal(options.focused, false);
      const tab = {id: 8, url: options.url, status: 'complete'};
      tabs.set(8, tab); return {id: 2, tabs: [tab]};
    }},
    tabs: {get: async id => { if (!tabs.has(id)) throw Error('No tab'); return tabs.get(id); },
      query: async options => [...tabs.values()].filter(tab => !options.url || tab.url.startsWith('https://platform.openai.com/')),
      create: async options => { const tab = {id: 8, url: options.url, status: 'complete'}; tabs.set(8, tab); return tab; },
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
      sent.push(JSON.parse(options.body));
      return {ok: true, status: 200, json: async () => ({refresh_minutes: 1})};
    }});
  vm.runInContext(background, context);
  const settle = () => new Promise(resolve => setImmediate(resolve));
  const message = (data, sender = {id: chrome.runtime.id}) => new Promise(resolve => {
    const handled = [...events.message][0](data, sender, resolve);
    if (!handled) resolve({ok: false});
  });
  return {local, session, tabs, sent, emit, message, settle, chrome,
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
  // Page changes reach the widget without waiting for the refresh alarm.
  const sender = {id: app.chrome.runtime.id, tab: {id: 7}, url: billing, frameId: 0};
  assert.equal((await app.message({type: 'watch_balance', balance: '11.25', sampled_at: Math.floor(Date.now()/1000)}, sender)).ok, true);
  assert.equal(app.sent.at(-1).balance, '11.25');
  assert.equal((await app.message({type: 'watch_balance', balance: '99', sampled_at: 1}, sender)).ok, false);
  // Temporary widget shutdown keeps pairing and the retry alarm.
  app.offline(true);
  assert.equal((await app.message({type: 'sync'})).ok, false);
  assert.ok(app.local.connection); assert.ok(app.alarm());
  app.offline(false);
  assert.equal((await app.message({type: 'sync'})).ok, true);
  // Login expiry retains pairing; returning to the same page resumes automatically.
  app.tabs.get(7).url = 'https://auth.openai.com/log-in';
  await app.emit('updated', 7, {url: 'https://auth.openai.com/log-in'});
  assert.equal(app.sent.at(-1).state, 'login_required');
  assert.equal((await app.message({type: 'sync'})).ok, false);
  assert.ok(app.local.connection); assert.ok(app.alarm());
  app.tabs.get(7).url = billing;
  await app.emit('updated', 7, {status: 'complete'});
  await app.settle();
  assert.equal(app.sent.at(-1).balance, '12.34');
  // A bound tab can navigate to another site, while login finishes in a new tab.
  app.tabs.get(7).url = 'https://chatgpt.com/';
  app.tabs.set(9, {id: 9, url: billing, status: 'complete'});
  assert.equal((await app.message({type: 'sync'})).ok, true);
  assert.equal(app.session.binding.tabId, 9);
  assert.equal(app.sent.at(-1).balance, '12.34');
  // With no billing tab, an unrelated old tab is preserved and a monitor is created.
  app.tabs.get(9).url = 'https://chatgpt.com/';
  assert.equal((await app.message({type: 'sync'})).ok, true);
  assert.equal(app.session.binding.tabId, 8);
  assert.equal(app.tabs.get(9).url, 'https://chatgpt.com/');
  // Browser restart clears session IDs, but keeps pairing and discovers the page.
  const restarted = harness(app.local);
  await restarted.settle();
  await restarted.emit('startup'); await restarted.settle();
  assert.equal(restarted.sent.at(-1).balance, '12.34');
  assert.equal(restarted.session.binding.tabId, 7);
  // Closing a page no longer unpairs; a later alarm restores one background tab.
  restarted.tabs.delete(7);
  await restarted.emit('removed', 7);
  assert.ok(restarted.local.connection);
  assert.equal((await restarted.message({type: 'sync'})).ok, true);
  assert.equal(restarted.session.binding.tabId, 8);
  assert.equal(restarted.tabs.size, 1);
  // When the last browser window closes, the background extension can restore
  // a minimized monitor rather than waiting for a manual browser launch.
  restarted.tabs.clear();
  await restarted.emit('removed', 8);
  assert.equal((await restarted.message({type: 'sync'})).ok, true);
  assert.equal(restarted.tabs.size, 1);
  assert.equal(restarted.sent.at(-1).balance, '12.34');
  assert.equal((await restarted.message({type: 'disconnect'})).ok, true);
  assert.equal(restarted.local.connection, undefined);
  assert.equal(restarted.alarm(), null);
  // Upgrade migrates an existing session pairing without discarding it.
  const migrated = harness({}, {connection: {port: 12345, token: 'f'.repeat(64), tabId: 7, generation: 'old'}});
  await migrated.settle();
  assert.equal(migrated.local.connection.generation, 'old');
  assert.equal(migrated.session.binding.tabId, 7);
  // The actual watcher debounces DOM changes and emits only changed money.
  let changed, scheduled, value = '12.34';
  const pushes = [];
  const watchContext = vm.createContext({
    __usagePetReadBalance: () => value, document: {body: {}},
    MutationObserver: class { constructor(fn) { changed = fn; } observe() {} },
    setTimeout: fn => { scheduled = fn; }, clearTimeout: () => {},
    chrome: {runtime: {sendMessage: async data => { pushes.push(data); }}}
  });
  const watchScript = fs.readFileSync(path.join(root, 'watcher.js'), 'utf8');
  vm.runInContext(watchScript, watchContext);
  assert.equal(pushes[0].balance, '12.34');
  value = '10.25'; changed(); scheduled();
  assert.equal(pushes.at(-1).balance, '10.25');
  changed(); scheduled(); assert.equal(pushes.length, 2);
  value = null; changed(); scheduled(); assert.equal(pushes.at(-1).balance, null);
  vm.runInContext(watchScript, watchContext); assert.equal(pushes.length, 3);
  console.log('Reader, page-change push, restart, outage recovery, login recovery, tab restore and unpair checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
