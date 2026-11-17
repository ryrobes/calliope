/* Shared by full-page apps and the stage sandbox. No model invocation. */
(() => {
  const pending = new Map();
  const root = window.RVBBIT_DASHBOARD;
  const version = root.version;
  const slug = root.slug;
  function requestKey() {
    if (crypto.randomUUID) return crypto.randomUUID();
    // Local HTTP appliances and opaque sandbox frames may lack randomUUID.
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  }
  async function direct(body) {
    const response = await fetch(`/api/d/${encodeURIComponent(slug)}/actions/${encodeURIComponent(body.action)}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({version, inputs: body.inputs, request_key: body.request_key}),
    });
    const data = await response.json();
    if (!response.ok || data.error) {
      throw Object.assign(new Error(data.error?.message || `Action failed (${response.status})`), {
        code: data.error?.code, status: response.status,
      });
    }
    return data;
  }
  window.rvbbitAction = (action, inputs = {}, options = {}) => {
    if (window.RVBBIT_ACTIONS_DISABLED || window.RVBBIT_DASHBOARD?.as_of) {
      return Promise.reject(Object.assign(new Error('Actions are disabled in captures and historical data views'), {code:'READ_ONLY_VIEW'}));
    }
    if (!Object.hasOwn(root.manifest?.actions || {}, action)) {
      return Promise.reject(Object.assign(new Error('No such action in this app version'), {code:'ACTION_NOT_FOUND'}));
    }
    // Coalesce identical clicks while a request is pending. Deliberate repeats
    // after completion get a new key; uncertain outcomes reuse error.requestKey.
    const payload = JSON.stringify(inputs);
    const identity = JSON.stringify([action, payload, options.requestKey || null]);
    if (pending.has(identity)) return pending.get(identity);
    const key = options.requestKey || requestKey();
    const transport = window.__rvbbitActionTransport || direct;
    const promise = Promise.resolve().then(() => transport({action, inputs:JSON.parse(payload), version, request_key:key}))
      .then(result => {
        window.dispatchEvent(new CustomEvent('rvbbit:action', {detail:result}));
        return result;
      }).catch(error => {
        error.requestKey = key;
        throw error;
      }).finally(() => pending.delete(identity));
    pending.set(identity, promise);
    return promise;
  };
})();
