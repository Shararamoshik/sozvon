export const $ = id => document.getElementById(id);
export function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}
export function message(id, text = '') {
  const target = $(id);
  target.textContent = text;
  target.hidden = !text;
}
export function time(ms) {
  if (ms === null || ms === undefined || !Number.isFinite(Number(ms))) return '—';
  const seconds = Math.max(0, Math.floor(Number(ms) / 1000));
  const mins = Math.floor(seconds / 60);
  return `${mins}:${String(seconds % 60).padStart(2, '0')}`;
}
export function date(value, withTime = false) {
  const parsed = new Date(value);
  if (!value || Number.isNaN(parsed.getTime())) return 'Дата не указана';
  return new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric', month: 'short', ...(withTime ? { hour: '2-digit', minute: '2-digit' } : {}),
  }).format(parsed);
}
export function activateTabs(attribute, value, panelPrefix) {
  document.querySelectorAll(`[${attribute}]`).forEach(button => {
    const selected = button.getAttribute(attribute) === value;
    button.setAttribute('aria-selected', String(selected));
    button.tabIndex = selected ? 0 : -1;
    const panel = panelPrefix ? $(panelPrefix + button.getAttribute(attribute))
      : $(button.getAttribute('aria-controls'));
    if (panel) panel.hidden = !selected;
  });
}
export function wireTabs(attribute, onChange) {
  const buttons = [...document.querySelectorAll(`[${attribute}]`)];
  buttons.forEach((button, index) => {
    button.addEventListener('click', () => onChange(button.getAttribute(attribute)));
    button.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
        : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
      buttons[next].focus();
      onChange(buttons[next].getAttribute(attribute));
    });
  });
}
export function emptyState(title, description, action) {
  const box = node('div', 'empty-state');
  box.append(node('h2', '', title), node('p', '', description));
  if (action) {
    const button = node('button', 'button primary', action.label);
    button.type = 'button';
    button.addEventListener('click', action.run);
    box.append(button);
  }
  return box;
}
export function bindDialog(dialog, canClose = () => true) {
  let returnFocus;
  dialog.addEventListener('close', () => {
    if (returnFocus?.isConnected) returnFocus.focus();
  });
  dialog.addEventListener('cancel', event => {
    if (!canClose()) event.preventDefault();
  });
  dialog.addEventListener('keydown', event => {
    if (event.key !== 'Tab' || !dialog.open) return;
    const focusable = [...dialog.querySelectorAll('button, a[href], input, select, textarea, [tabindex]')]
      .filter(element => !element.matches(':disabled') && element.tabIndex >= 0 && element.getClientRects().length);
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (!first) { event.preventDefault(); dialog.focus(); return; }
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  return {
    open() {
      returnFocus = document.activeElement;
      if (!dialog.open) dialog.showModal();
    },
    close() { if (canClose()) dialog.close(); },
  };
}
