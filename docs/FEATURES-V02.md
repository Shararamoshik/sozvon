# Контракт расширения «Созвон» 0.2

Источник требований: `.hermes/plans/2026-09-26_231622-sozvon-stt-export-editors.md`.
Пользователь разрешил реализацию. Ограничение «только план» в старом документе относится к его подготовке, не текущему этапу. Baseline: b59c353, 451 passed, 1 skipped, Starlette/httpx warning, ruff clean.

## Владение файлами

- Исполнитель хранения: `sozvon/core/`, `sozvon/transcripts/`, `sozvon/templates/`, `sozvon/storage/`, `sozvon/llm/{schema,request,provider}.py`, новые тесты этих функций. Не менять `llm/network.py`, сервисы, веб, runtime.
- Исполнитель STT: `sozvon/config.py`, `sozvon/shared/`, `sozvon/llm/network.py`, `sozvon/audio/{cloud_stt,cloud_response,result_limits,stt}.py`, новые tests STT/settings. Не менять capture/backend/runtime/services/web.
- Исполнитель экспорта: `sozvon/export/`, новые тесты export, `scripts/verify_exports.py`. Не менять runtime/services/web/repository/manifests.
- Исполнитель UI: `sozvon/web/static/`, `sozvon/web/templates/`, новые `tests/test_ui_{cloud_stt,transcript_editor,templates,export}.py`. Не менять backend, существующие тесты или скрипты. Сохранить стиль приложения.
- Родитель: манифесты/lock, runtime, Application, services/transcripts.py, services/templates.py, routers, server.py, интеграционные тесты, документация, сборка и коммиты. Дети не коммитят и не синхронизируют Windows. Каждый владеет `__init__.py` только в назначенных новых пакетах.

## Зафиксированные публичные функции

Хранение: план К1–К7. `RevisionConflict.current_revision`; `ResourceConflict` для 409. `storage.transcripts`: `read_revision(repo,mid,revision)->list[dict]`, `list_revisions(repo,mid)->list[dict]`, `save_edits(repo,mid,TranscriptEdit)->int`, `restore_revision(repo,mid,base_revision,target_revision)->int`, `save_recognition(repo,mid,base_revision,segments,origin,metadata)->int`. Сохранять старые методы Repo совместимыми. detail в согласованной read transaction. Нет циклического импорта repo/migrations/templates.

Шаблоны: `TemplateSpec`, `builtin_spec(id)`, `storage.templates.{get_template,list_templates,create_template,save_template,archive_template}` с сигнатурами К6. `sozvon/templates/rendering.py: sections_for_report(report)->list[{key,title,kind,items}]` использует snapshot/legacy fallback, не актуальную БД. Repo.detail добавляет это как `report.sections` для UI. В пользовательских разделах UUID создаёт клиент crypto.randomUUID без дефисов, сервер строго валидирует. Это уточняет противоречие §1.3/К6: сервер не переназывает полученный корректный key.

STT: `SettingsStore.stt_snapshot()->(полный detached config, отдельный key|None)` под одним RLock. Public `stt.cloud.endpoint` — полный конечный URL. Local default не меняется; loopback без key допустим для локальных совместимых сервисов/синтетических тестов, external требует согласие и key. `transcribe_cloud(payload,stop_event,emit)->dict` как К10; `shared.network.validated_base_url`; protocol/dispatch делает родитель. Старый hash LLM не менять.

Экспорт: `export.document.{Block,ExportDocument,build_document}`, `export.worker.render_export(payload,stop_event,emit)`, `export.service.export_meeting(service,mid,options)->(bytes,media_type,filename)`. `options` обычный dict с format/content/include_transcript/include_quotes/report_id. Снимок repo.detail в одной транзакции. JSON сохраняет transcript_history; не использовать неверную новую расшифровку для старого report. Stale -> ResourceConflict. Semaphore на экземпляре Application `_export_lock`, stop-event `_export_stop`, set при close. Worker через service.worker().run с timeout=60; родитель регистрирует dispatch. Ограничения snapshot 8 MiB, output 32 MiB, IPC прежний 1 MiB. Шрифты читать реальные имена в static/fonts, не угадывать регистр.

## API для UI и интеграции

Session/Origin/CSRF прежние. Пути и тела §3 плана. Новые ответы:
- GET /api/templates -> {items:[{id,builtin,archived,revision,spec}]}; POST {spec}->201 такой объект; copy {name?}->201; PUT {base_revision,spec}->объект; archive {base_revision}->{archived:true}.
- GET /api/meetings/{id}/transcript/revisions -> {items:[{revision,parent_revision,origin,created_at,metadata}]}; GET transcript?revision=N -> {revision,segments}.
- PUT transcript {base_revision,edits:[{id,text,speaker}]} и POST transcript/restore {base_revision,target_revision} -> {revision,segments}. speaker=null явно очищает метку.
- POST transcribe {base_revision,replace_confirmed,cloud_confirmed_url} ->202 {job_id}. Без body совместим только первый local запуск с revision0. Cloud требует подтверждения точного endpoint и для loopback в UI.
- POST report {template_id?,template_revision?} ->202 {job_id}; пустое тело использует default.
- GET export?format=docx|pdf|md|json&content=auto|report|transcript&include_transcript=0|1&include_quotes=0|1&report_id=... -> файл или JSON ошибки. JSON архив не фильтруется и это подписано UI.
- Новые маршруты: 422 validation, 404 missing, 409 conflict/busy/stale, 413 too large. `RevisionConflict` -> {detail:текст,current_revision:N}; generic ValueError прежних endpoints совместим, новые роутеры преобразуют validation отдельно.

## Проверки

TDD по одному поведению: реальный RED → GREEN; `.venv/bin/python -m pytest <свой тест> -q`, `.venv/bin/ruff check <свои пути>`. Без платной сети/чужих данных. Не ослаблять прежние security-тесты. Все внешние ответы — только явно обозначенные синтетические HTTP фикстуры. Родитель проверяет интеграцию реальных модулей, браузер/геометрию, Windows worker и EXE, независимое ревью.
