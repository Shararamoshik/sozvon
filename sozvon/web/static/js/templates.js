import { request } from './api.js';
import { $, node, message, bindDialog } from './dom.js';

const core = [['summary', 'Кратко'], ['decisions', 'Решения'], ['proposals', 'Предложения'], ['tasks', 'Задачи'], ['questions', 'Открытые вопросы'], ['risks', 'Риски']];
const blank = () => ({ name: '', description: '', language: 'ru', detail: 'normal', instructions: '', sections: core.map(([key, title]) => ({ key, title, kind: key === 'tasks' ? 'tasks' : 'statements', enabled: true, instructions: '' })) });

export function createTemplates({ guard, getDefault }) {
  let selected = null, spec = null, baseline = '', busy = false, selectionSerial = 0;
  const previewDialog = bindDialog($('template-preview-dialog'));
  const archiveDialog = bindDialog($('template-archive-dialog'), () => !busy);
  const dirty = () => spec && JSON.stringify(spec) !== baseline;
  const path = id => `/api/templates/${encodeURIComponent(id)}`;
  function controls() {
    const locked = busy || Boolean(selected?.builtin || selected?.archived);
    $('template-fields').disabled = locked;
    $('save-template').disabled = locked || !dirty();
    $('template-add-section').disabled = locked || spec?.sections.length >= 12;
  }
  function status(text) { $('template-status').textContent = text; controls(); }
  function changed() { status(dirty() ? 'Есть несохранённые изменения' : 'Без изменений'); }
  function sections() {
    const box = $('template-sections'); box.replaceChildren();
    spec.sections.forEach((section, index) => {
      const row = node('section', 'template-section'); row.dataset.sectionKey = section.key;
      const enabledLabel = node('label', 'checkbox-label', 'Включён');
      const enabled = node('input'); enabled.type = 'checkbox'; enabled.checked = section.enabled;
      enabled.dataset.sectionField = 'enabled'; enabled.addEventListener('change', () => { section.enabled = enabled.checked; changed(); }); enabledLabel.prepend(enabled);
      const titleLabel = node('label', '', 'Название раздела');
      const title = node('input'); title.value = section.title; title.required = true; title.maxLength = 80; title.dataset.sectionField = 'title';
      title.addEventListener('input', () => { section.title = title.value; changed(); }); titleLabel.append(title);
      const instructionsLabel = node('label', '', 'Назначение раздела');
      const instructions = node('textarea'); instructions.rows = 2; instructions.maxLength = 600; instructions.value = section.instructions; instructions.dataset.sectionField = 'instructions';
      instructions.addEventListener('input', () => { section.instructions = instructions.value; changed(); }); instructionsLabel.append(instructions);
      const toolbar = node('div', 'editor-toolbar');
      toolbar.append(node('span', 'muted', section.kind === 'tasks' ? 'Задачи' : 'Текстовые пункты'));
      for (const [direction, delta, label] of [['up', -1, '↑ Выше'], ['down', 1, '↓ Ниже']]) {
        const button = node('button', 'button small', label); button.type = 'button'; button.dataset.move = direction;
        button.setAttribute('aria-label', `${label}: ${section.title}`);
        button.disabled = index + delta < 0 || index + delta >= spec.sections.length;
        button.addEventListener('click', () => {
          [spec.sections[index], spec.sections[index + delta]] = [spec.sections[index + delta], spec.sections[index]];
          sections(); changed();
          const moved = box.children[index + delta];
          (moved.querySelector(`[data-move="${direction}"]:not(:disabled)`) || moved.querySelector('[data-section-field="title"]'))?.focus();
        }); toolbar.append(button);
      }
      if (section.key.startsWith('custom_')) {
        const remove = node('button', 'button small', 'Удалить раздел'); remove.type = 'button';
        remove.addEventListener('click', () => {
          spec.sections.splice(index, 1); sections(); changed();
          (box.children[Math.min(index, spec.sections.length - 1)]?.querySelector('[data-section-field="title"]') || $('template-add-section')).focus();
        }); toolbar.append(remove);
      }
      row.append(titleLabel, enabledLabel, instructionsLabel, toolbar); box.append(row);
    });
  }
  function show(item) {
    selectionSerial += 1;
    selected = item; spec = structuredClone(item ? item.spec : blank()); baseline = item ? JSON.stringify(spec) : '';
    $('template-form').hidden = false;
    for (const key of ['name', 'description', 'language', 'detail', 'instructions']) $('template-' + key).value = spec[key];
    $('template-fields').disabled = Boolean(item?.builtin || item?.archived);
    $('template-copy').hidden = !item;
    $('template-copy').textContent = item?.builtin ? 'Редактировать копию' : 'Дублировать';
    $('archive-template').hidden = !item || item.builtin;
    $('archive-template').disabled = item?.id === getDefault();
    $('archive-template').title = item?.id === getDefault() ? 'Сначала смените шаблон по умолчанию в настройках' : '';
    sections(); changed(); message('templates-error');
  }
  async function load() {
    selectionSerial += 1;
    try {
      const result = await request('/api/templates');
      const box = $('template-list'); box.replaceChildren();
      result.items.filter(item => !item.archived).forEach(item => {
        const button = node('button', 'button', `${item.spec.name} · ${item.builtin ? 'Встроенный' : item.id.slice(0, 8)} · v${item.revision}`);
        button.type = 'button'; button.dataset.templateId = item.id;
        button.addEventListener('click', () => guard(async () => {
          const serial = ++selectionSerial;
          try {
            const result = await request(path(item.id));
            if (serial !== selectionSerial || $('templates-view').hidden || dirty()) return;
            show(result);
          } catch (error) { if (serial === selectionSerial && !$('templates-view').hidden) message('templates-error', error.message); }
        })); box.append(button);
      });
    } catch (error) { message('templates-error', error.message); }
  }
  for (const key of ['name', 'description', 'language', 'detail', 'instructions']) $('template-' + key).addEventListener('input', event => { spec[key] = event.target.value; changed(); });
  $('template-preview-button').addEventListener('click', () => {
    const box = $('template-preview'); box.replaceChildren();
    spec.sections.filter(section => section.enabled).forEach(section => {
      box.append(node('h3', '', section.title), node('p', 'muted', section.kind === 'tasks' ? 'Задача · Исполнитель: не указан · Срок: не указан · Источник' : 'Текстовый пункт · Источник'));
    }); previewDialog.open();
  });
  $('close-template-preview').addEventListener('click', () => previewDialog.close());
  $('new-template').addEventListener('click', () => guard(() => show(null)));
  $('template-add-section').addEventListener('click', () => {
    if (spec.sections.length >= 12) return;
    spec.sections.push({ key: 'custom_' + crypto.randomUUID().replaceAll('-', ''), title: 'Новый раздел', kind: $('template-custom-kind').value, enabled: true, instructions: '' });
    sections(); changed(); $('template-sections').lastElementChild.querySelector('input').focus();
  });
  document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's' && !$('templates-view').hidden && dirty() && !document.querySelector('dialog[open]')) {
      event.preventDefault(); if (!busy) $('template-form').requestSubmit();
    }
  });
  $('template-form').addEventListener('submit', async event => {
    event.preventDefault(); if (!dirty() || busy || selected?.builtin) return;
    if (!spec.sections.some(section => section.enabled)) { message('templates-error', 'Включите хотя бы один раздел.'); return; }
    busy = true; $('template-fields').disabled = true; status('Сохранение…'); message('templates-error');
    try {
      const result = await request(selected ? path(selected.id) : '/api/templates', { method: selected ? 'PUT' : 'POST', body: selected ? { base_revision: selected.revision, spec } : { spec } });
      const verified = await request(path(result.id));
      if (JSON.stringify(verified.spec) !== JSON.stringify(spec) || verified.revision !== result.revision) throw new Error('Сохранение не подтверждено. Поля оставлены для сверки.');
      show(verified); await load(); status(`Сохранена версия ${verified.revision}`);
    } catch (error) { message('templates-error', error.message); }
    finally { busy = false; controls(); }
  });
  $('template-copy').addEventListener('click', () => guard(async () => {
    if (busy || !selected) return;
    busy = true;
    try { const result = await request(path(selected.id) + '/copy', { method: 'POST', body: {} }); show(await request(path(result.id))); await load(); }
    catch (error) { message('templates-error', error.message); }
    finally { busy = false; changed(); }
  }));
  $('archive-template').addEventListener('click', () => guard(() => archiveDialog.open()));
  $('cancel-template-archive').addEventListener('click', () => archiveDialog.close());
  $('confirm-template-archive').addEventListener('click', async () => {
    if (busy || !selected) return;
    busy = true;
    try {
      await request(path(selected.id) + '/archive', { method: 'POST', body: { base_revision: selected.revision } });
      const verified = await request(path(selected.id));
      if (!verified.archived) throw new Error('Архивирование не подтверждено.');
      selected = spec = null; baseline = ''; $('template-form').hidden = true; await load();
    } catch (error) { message('templates-error', error.message); }
    finally { busy = false; archiveDialog.close(); }
  });
  return { load, dirty, busy: () => busy, discard() { selectionSerial += 1; if (selected) show(selected); else { spec = null; baseline = ''; $('template-form').hidden = true; } } };
}
