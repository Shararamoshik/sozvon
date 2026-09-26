let csrf = '';
let unauthorized = () => {};

export class ApiError extends Error {
  constructor(message, status = 0, payload = null) { super(message); this.name = 'ApiError'; this.status = status; this.payload = payload; }
}

export function onUnauthorized(callback) { unauthorized = callback; }

export async function request(path, { method = 'GET', body, bootstrap = false } = {}) {
  const headers = { Accept: 'application/json' };
  if (method !== 'GET' && !bootstrap) headers['X-CSRF-Token'] = csrf;
  const multipart = body instanceof FormData;
  if (body !== undefined && !multipart) headers['Content-Type'] = 'application/json';
  let response;
  try {
    response = await fetch(path, {
      method, headers, credentials: 'same-origin', cache: 'no-store',
      body: body === undefined ? undefined : multipart ? body : JSON.stringify(body),
    });
  } catch {
    throw new ApiError('Нет связи с приложением. Проверьте, что оно запущено, и повторите действие.');
  }
  let payload;
  try { payload = await response.json(); } catch { payload = null; }
  if (!response.ok) {
    if (response.status === 401) {
      unauthorized();
      throw new ApiError('Откройте приложение через ярлык. Эта сессия недоступна.', response.status, payload);
    }
    const detail = payload?.detail;
    const message = typeof detail === 'string' ? detail : Array.isArray(detail)
      ? detail.map(item => item.msg || 'Некорректное поле').join('; ')
      : `Приложение вернуло ошибку ${response.status}.`;
    throw new ApiError(message, response.status, payload);
  }
  if (!payload || typeof payload !== 'object') throw new ApiError('Приложение вернуло некорректный ответ.', response.status, payload);
  return payload;
}

export async function startSession() {
  const fragment = new URLSearchParams(location.hash.slice(1));
  const key = fragment.get('key');
  if (key !== null) history.replaceState(null, '', location.pathname + location.search);
  const session = key !== null
    ? await request('/api/session', { method: 'POST', body: { key }, bootstrap: true })
    : await request('/api/session');
  if (!session.csrf) throw new Error('Не удалось открыть защищённую сессию. Откройте приложение через ярлык.');
  csrf = session.csrf;
}
