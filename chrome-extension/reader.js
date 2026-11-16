// Runs in the isolated world. Reads rendered text, never page storage or cookies.
(async () => {
  const validPage = () => location.protocol === 'https:' && location.hostname === 'platform.openai.com'
    && (!location.port || location.port === '443')
    && location.pathname.replace(/\/$/, '') === '/settings/organization/billing/overview';
  function parse(text) {
    const lines = text.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
    const values = new Set();
    for (let i = 0; i < lines.length; i++) {
      const label = lines[i].match(/^(?:(?:api\s+)?credit balance|available credit balance|available credits|信用余额|可用余额|剩余余额)\s*[:：]?\s*(.*)$/i);
      if (!label) continue;
      const value = (label[1] || lines[i + 1] || '').match(/^([-−])?\s*(?:US\$|\$|USD)\s*([-−])?\s*((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,8})?)\s*(?:USD)?$/i);
      if (!value || (value[1] && value[2])) continue;
      const number = value[3].replaceAll(',', '');
      const normalized = String(Number((value[1] || value[2] ? '-' : '') + number));
      if (/^-?\d{1,12}(?:\.\d{1,8})?$/.test(normalized)) values.add(normalized);
    }
    return values.size === 1 ? [...values][0] : null;
  }
  // SPA content can arrive after the document's load event.
  for (let attempt = 0; attempt < 20; attempt++) {
    if (!validPage()) return {balance: null};
    const balance = parse(document.body?.innerText || '');
    if (balance !== null) return {balance};
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  return {balance: null};
})();
