// Offline behavior checks: no browser profile, website, or credentials are used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.join(__dirname, '..', 'chrome-extension');
const reader = fs.readFileSync(path.join(root, 'reader.js'), 'utf8');
const billing = 'https://platform.openai.com/settings/organization/billing/overview';
async function read(text, url = billing) {
  return vm.runInNewContext(reader, {
    location: new URL(url), document: {body: {innerText: text}},
    setTimeout: resolve => resolve()
  });
}

(async () => {
  assert.equal((await read('API credit balance\nUS$12.34\n下一笔到期额度：US$5.00')).balance, '12.34');
  assert.equal((await read('Credit balance: −$0.50')).balance, '-0.5');
  assert.equal((await read('Credit balance\n$1,234.56')).balance, '1234.56');
  assert.equal((await read('Credit balance\n$0.00')).balance, '0');
  assert.equal((await read('Usage this month\n$99.00')).balance, null);
  assert.equal((await read('Credit balance\n$12.34\nCredit balance\n$99.00')).balance, null);
  assert.equal((await read('Credit balance\n$12.34', 'https://platform.openai.com/api-keys')).balance, null);
  assert.equal((await read('Credit balance\n$12.34', 'https://platform.openai.com.evil.invalid/settings/organization/billing/overview')).balance, null);

  const store = {};
  const sent = [];
  let listener, onRemoved, alarm;
  const onUpdated = new Set();
  let url = billing;
  const chrome = {
    runtime: {id: 'a'.repeat(32), onMessage: {addListener: callback => { listener = callback; }}},
    storage: {session: {
      get: async key => ({[key]: store[key]}), set: async data => Object.assign(store, data),
      remove: async key => { delete store[key]; }
    }},
    tabs: {get: async id => ({id, url, status: 'complete'}), query: async () => [{id: 7, url}],
      reload: async id => { for (const callback of [...onUpdated]) await callback(id, {status: 'complete'}); },
      onRemoved: {addListener: callback => { onRemoved = callback; }},
      onUpdated: {addListener: callback => onUpdated.add(callback), removeListener: callback => onUpdated.delete(callback)}},
    scripting: {executeScript: async () => [{result: await read('API credit balance\nUS$12.34', url)}]},
    alarms: {create: async (name, options) => { alarm = options; }, clear: async () => { alarm = null; },
      onAlarm: {addListener: () => {}}}
  };
  vm.runInNewContext(fs.readFileSync(path.join(root, 'background.js'), 'utf8'), {
    chrome, URL, AbortSignal, crypto: require('node:crypto').webcrypto, setTimeout, clearTimeout,
    fetch: async (target, options) => {
      assert.equal(target, 'http://127.0.0.1:12345/balance');
      assert.equal(options.credentials, 'omit');
      assert.equal(options.redirect, 'error');
      sent.push(JSON.parse(options.body));
      return {ok: true, json: async () => ({refresh_minutes: 3})};
    }
  });
  const message = data => new Promise(resolve => listener(data, {id: chrome.runtime.id}, resolve));
  const code = '12345:' + 'f'.repeat(64);
  assert.equal((await message({type: 'connect', code, confirmed: false})).ok, false);
  assert.equal(sent.length, 0);
  assert.equal((await message({type: 'connect', code, confirmed: true})).ok, true);
  assert.deepEqual(sent, [{balance: null}, {balance: '12.34'}]);
  assert.equal(alarm.periodInMinutes, 3);
  assert.equal((await message({type: 'sync'})).ok, true);
  url = 'https://auth.openai.com/log-in';
  assert.equal((await message({type: 'sync'})).ok, false);
  assert.equal(sent.at(-1).balance, null);
  assert.equal(alarm, null);
  url = billing;
  assert.equal((await message({type: 'connect', code, confirmed: true})).ok, true);
  await onRemoved(7);
  assert.equal(sent.at(-1).balance, null);
  assert.equal(store.connection, undefined);
  console.log('Extension reader, pairing, sync, login failure and tab-close checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
