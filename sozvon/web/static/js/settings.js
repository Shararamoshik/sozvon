import { request } from './api.js';
import { $, node, message, activateTabs } from './dom.js';

export function createSettings({ changed, reportError }) {
  let saved = null;
  let dirty = false;
  let loading = false;
  let saving = false;
  let devicesLoading = false;
  const form = $('settings-form');
  const fieldIds = ['stt-engine', 'stt-cloud-base', 'stt-cloud-model', 'stt-cloud-format', 'stt-cloud-timeout', 'stt-cloud-limit', 'llm-base', 'llm-protocol', 'llm-model', 'llm-max-output-tokens', 'llm-timeout', 'report-template', 'stt-path', 'stt-device', 'stt-language', 'input-device', 'output-device'];

  function updateStatus(text) {
    $('settings-status').textContent = text;
    $('save-settings').disabled = !saved || saving || !dirty;
  }
  function tab(value) { activateTabs('data-settings-tab', value); }
  function markDirty() {
    dirty = true;
    updateStatus('Есть несохранённые изменения');
  }
  function keepOption(id, value, label) {
    const select = $(id);
    const normalized = value === null || value === undefined ? '' : String(value);
    if (![...select.options].some(option => option.value === normalized)) {
      const option = node('option', '', label || normalized);
      option.value = normalized;
      select.append(option);
    }
    select.value = normalized;
  }
  function protocolHint() {
    $('base-hint').textContent = $('llm-protocol').value === 'openai'
      ? 'Для OpenAI укажите базовый URL с /v1.'
      : 'Для Anthropic укажите базовый URL без /v1/messages.';
  }
  function enginePanel() {
    const cloud = $('stt-engine').value === 'cloud';
    $('stt-local-fields').hidden = cloud;
    $('stt-local-fields').disabled = cloud;
    $('stt-cloud-fields').hidden = !cloud;
    $('stt-cloud-fields').disabled = !cloud;
  }
  function apply(data) {
    const cloud = data.stt?.cloud || {};
    $('stt-engine').value = data.stt?.engine || 'local';
    $('stt-cloud-base').value = cloud.base_url || 'https://api.openai.com/v1';
    $('stt-cloud-model').value = cloud.model || 'whisper-1';
    $('stt-cloud-format').value = cloud.response_format || 'verbose_json';
    $('stt-cloud-timeout').value = cloud.timeout_s || 120;
    $('stt-cloud-limit').value = (cloud.max_upload_bytes || 24000000) / 1000000;
    $('stt-cloud-allow-remote').checked = Boolean(cloud.allow_remote);
    $('stt-cloud-key').value = '';
    $('stt-cloud-key').disabled = false;
    $('stt-cloud-delete-key').checked = false;
    $('stt-key-state').textContent = cloud.secret_error || (cloud.configured ? 'Ключ STT сохранён для этого адреса.' : 'Ключ STT не сохранён для этого адреса.');
    enginePanel();
    $('llm-base').value = data.llm?.base_url || '';
    $('llm-protocol').value = data.llm?.protocol || 'openai';
    $('llm-model').value = data.llm?.model || '';
    $('llm-max-output-tokens').value = data.llm?.max_output_tokens ?? 16384;
    $('llm-timeout').value = data.llm?.timeout_s ?? 300;
    $('allow-remote').checked = Boolean(data.llm?.allow_remote);
    keepOption('report-template', data.template || 'meeting');
    $('stt-path').value = data.stt?.model_path || '';
    $('stt-device').value = data.stt?.device || 'cpu';
    keepOption('stt-language', data.stt?.language || 'auto');
    keepOption('input-device', data.recording?.input_device, `Устройство ${data.recording?.input_device}`);
    keepOption('output-device', data.recording?.output_device, `Устройство ${data.recording?.output_device}`);
    $('llm-key').value = '';
    $('llm-key').disabled = false;
    $('delete-key').checked = false;
    $('key-state').textContent = data.llm?.secret_error || (data.llm?.configured
      ? 'Ключ сохранён для этого адреса в системном хранилище.'
      : 'Ключ не сохранён для этого адреса. Локальный API может работать без ключа.');
    $('data-directory').textContent = data.data_dir || 'Каталог не указан';
    protocolHint();
  }
  async function loadTemplates() {
    try {
      const result = await request('/api/templates');
      const select = $('report-template');
      const selected = select.value || saved?.template;
      select.replaceChildren();
      result.items.filter(item => !item.archived).forEach(item => {
        const option = node('option', '', `${item.spec.name} · ${item.builtin ? 'Встроенный' : item.id.slice(0, 8)} · v${item.revision}`);
        option.value = item.id; select.append(option);
      });
      if (selected) keepOption('report-template', selected, 'Текущий шаблон недоступен — выберите другой');
    } catch (error) { message('settings-error', error.message); }
  }
  async function load() {
    if (loading || saving || dirty) return;
    loading = true;
    message('settings-error');
    updateStatus('Загрузка настроек…');
    try {
      const data = await request('/api/settings');
      if (dirty) return;
      saved = data;
      apply(saved);
      $('settings-fields').disabled = false;
      updateStatus('Все изменения сохранены');
      changed();
    } catch (error) {
      message('settings-error', error.message);
      updateStatus('Не удалось загрузить настройки. Откройте раздел повторно.');
    } finally { loading = false; }
  }
  function payload() {
    const llm = {
      base_url: $('llm-base').value.trim(), protocol: $('llm-protocol').value,
      model: $('llm-model').value.trim(), allow_remote: $('allow-remote').checked,
      max_output_tokens: Number($('llm-max-output-tokens').value), timeout_s: Number($('llm-timeout').value),
    };
    const cloud = $('stt-engine').value === 'cloud' ? {
      base_url: $('stt-cloud-base').value.trim(), model: $('stt-cloud-model').value.trim(),
      response_format: $('stt-cloud-format').value, timeout_s: Number($('stt-cloud-timeout').value),
      max_upload_bytes: Math.round(Number($('stt-cloud-limit').value) * 1000000),
      allow_remote: $('stt-cloud-allow-remote').checked,
    } : {};
    if ($('stt-cloud-delete-key').checked) cloud.delete_key = true;
    else if ($('stt-cloud-key').value.trim()) cloud.api_key = $('stt-cloud-key').value.trim();
    if ($('delete-key').checked) llm.delete_key = true;
    else if ($('llm-key').value.trim()) llm.api_key = $('llm-key').value.trim();
    return {
      llm,
      stt: { engine: $('stt-engine').value, ...(Object.keys(cloud).length ? { cloud } : {}), model_path: $('stt-path').value.trim(), device: $('stt-device').value, language: $('stt-language').value },
      recording: { input_device: $('input-device').value || null, output_device: $('output-device').value || null },
      template: $('report-template').value,
    };
  }
  function verifySettings(candidate, current) {
    for (const [group, keys] of [['llm', ['base_url', 'protocol', 'model', 'allow_remote', 'max_output_tokens', 'timeout_s']], ['stt', ['model_path', 'device', 'language']], ['recording', ['input_device', 'output_device']]]) {
      for (const key of keys) {
        const expected = candidate[group][key];
        const actual = current[group]?.[key];
        if (key === 'base_url' && String(expected).replace(/\/$/, '') === String(actual).replace(/\/$/, '')) continue;
        if (expected !== actual) throw new Error('Сервер сохранил другие значения. Проверьте настройки и повторите сохранение.');
      }
    }
    if (candidate.stt.engine !== current.stt?.engine) throw new Error('Не удалось подтвердить режим распознавания.');
    const expectedCloud = { ...saved.stt?.cloud, ...candidate.stt.cloud };
    for (const key of ['base_url', 'model', 'response_format', 'timeout_s', 'max_upload_bytes', 'allow_remote']) {
      if (!(key in expectedCloud)) continue;
      const expected = expectedCloud[key], actual = current.stt?.cloud?.[key];
      if (key === 'base_url' && String(expected).replace(/\/$/, '') === String(actual).replace(/\/$/, '')) continue;
      if (expected !== actual) throw new Error('Не удалось подтвердить настройки облачного STT.');
    }
    for (const [profile, state, label] of [[candidate.llm, current.llm, 'LLM'], [candidate.stt.cloud, current.stt?.cloud, 'STT']]) {
      if (profile?.delete_key && (state?.configured !== false || state?.secret_error)) {
        throw new Error(`Не удалось подтвердить удаление ключа ${label}. Проверьте состояние ключа и повторите сохранение.`);
      }
    }
    if (candidate.template !== current.template) throw new Error('Не удалось подтвердить сохранение шаблона.');
  }
  form.addEventListener('invalid', event => {
    const panel = event.target.closest('[role="tabpanel"]');
    if (panel) tab(panel.id.replace('settings-', ''));
  }, true);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (saving || !saved || !dirty) return;
    const llmSecret = $('delete-key').checked || $('llm-key').value.trim();
    const sttSecret = $('stt-cloud-delete-key').checked || $('stt-cloud-key').value.trim();
    if (llmSecret && sttSecret) {
      message('settings-error', 'Есть несохранённые действия с ключами LLM и STT. За один раз можно изменить только один ключ. Отмените действие в одном из профилей и повторите сохранение.');
      updateStatus('Настройки не сохранены. Действия с ключами оставлены в форме.');
      return;
    }
    if (sttSecret && $('stt-engine').value === 'local'
        && $('stt-cloud-base').value.trim().replace(/\/$/, '') !== saved.stt?.cloud?.base_url?.replace(/\/$/, '')) {
      message('settings-error', 'Действие с ключом STT относится к изменённому адресу. Выберите «Через API» и сохраните этот профиль либо отмените действие с ключом STT.');
      updateStatus('Настройки не сохранены. Действие с ключом STT оставлено в форме.');
      return;
    }
    saving = true;
    message('settings-error');
    updateStatus('Сохранение настроек…');
    $('settings-fields').disabled = true;
    const candidate = payload();
    try {
      await request('/api/settings', { method: 'PUT', body: candidate });
      // Never claim success until the exact resource has been read back.
      const current = await request('/api/settings');
      verifySettings(candidate, current);
      saved = current;
      dirty = false;
      apply(saved);
      updateStatus('Настройки сохранены');
      changed();
    } catch (error) {
      message('settings-error', error.message);
      updateStatus('Сохранение не подтверждено. Ваши изменения оставлены в форме.');
    } finally {
      saving = false;
      $('settings-fields').disabled = false;
      $('save-settings').disabled = !dirty;
      // Clear sensitive input after every attempted save; never retain a secret in UI state.
      $('llm-key').value = '';
      $('stt-cloud-key').value = '';
    }
  });
  fieldIds.forEach(id => {
    $(id).addEventListener('input', markDirty);
    $(id).addEventListener('change', markDirty);
  });
  $('llm-key').addEventListener('input', markDirty);
  $('allow-remote').addEventListener('change', markDirty);
  $('delete-key').addEventListener('change', () => {
    $('llm-key').disabled = $('delete-key').checked;
    if ($('delete-key').checked) $('llm-key').value = '';
    markDirty();
  });
  $('llm-protocol').addEventListener('change', protocolHint);
  $('stt-engine').addEventListener('change', enginePanel);
  $('stt-cloud-base').addEventListener('input', () => { $('stt-cloud-allow-remote').checked = false; });
  $('stt-cloud-key').addEventListener('input', markDirty);
  $('stt-cloud-allow-remote').addEventListener('change', markDirty);
  $('stt-cloud-delete-key').addEventListener('change', () => {
    $('stt-cloud-key').disabled = $('stt-cloud-delete-key').checked;
    if ($('stt-cloud-delete-key').checked) $('stt-cloud-key').value = '';
    markDirty();
  });

  async function loadDevices() {
    if (devicesLoading) return;
    devicesLoading = true;
    $('refresh-devices').disabled = true;
    $('device-status').textContent = 'Ищем устройства… Остальные разделы доступны.';
    try {
      const data = await request('/api/devices');
      for (const [id, devices] of [['input-device', data.microphones], ['output-device', data.outputs]]) {
        const select = $(id);
        const selected = select.value;
        select.replaceChildren();
        const automatic = node('option', '', 'Системное устройство по умолчанию');
        automatic.value = '';
        select.append(automatic);
        (devices || []).forEach(device => {
          const option = node('option', '', device.name);
          option.value = String(device.id);
          select.append(option);
        });
        keepOption(id, selected, `Устройство ${selected} · недоступно в списке`);
      }
      const count = (data.microphones?.length || 0) + (data.outputs?.length || 0);
      $('device-status').textContent = data.available ? `Доступных устройств: ${count}${data.reason ? `. ${data.reason}` : ''}` : data.reason || 'Аудиоустройства недоступны.';
    } catch (error) {
      $('device-status').textContent = error.message;
      reportError(error);
    } finally {
      devicesLoading = false;
      $('refresh-devices').disabled = false;
    }
  }
  $('refresh-devices').addEventListener('click', loadDevices);
  return {
    value: () => saved, busy: () => saving, dirty: () => dirty, load, loadTemplates, tab,
    discard() { if (saved) apply(saved); dirty = false; updateStatus('Изменения отменены'); },
  };
}
