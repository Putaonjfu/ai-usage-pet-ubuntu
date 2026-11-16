// Only visible page text is observed. No page storage or credentials are read.
(() => {
  if (globalThis.__usagePetWatcher) return;
  globalThis.__usagePetWatcher = true;
  let last;
  let timer;
  function sample() {
    const balance = globalThis.__usagePetReadBalance?.() ?? null;
    if (balance === last) return;
    last = balance;
    void chrome.runtime.sendMessage({type: 'watch_balance', balance,
      sampled_at: Math.floor(Date.now() / 1000)}).catch(() => {});
  }
  const observer = new MutationObserver(() => {
    clearTimeout(timer);
    timer = setTimeout(sample, 400);
  });
  observer.observe(document.body, {subtree: true, childList: true, characterData: true, attributes: true,
    attributeFilter: ['hidden', 'class', 'style']});
  sample();
})();
