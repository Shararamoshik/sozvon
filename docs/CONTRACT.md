# «Созвон»: базовый договор и расширение 0.2

Описание ниже фиксирует исходный прототип 0.1. Изменённые возможности/сигнатуры 0.2
описаны в [FEATURES-V02.md](FEATURES-V02.md) и имеют приоритет над ограничениями ниже.
Реализованы облачный STT, история/редактор расшифровки, пользовательские шаблоны,
DOCX/PDF и единый экспорт. Встроенные шаблоны копируются перед редактированием,
отчёты хранят snapshot шаблона/расшифровки, ключи STT и LLM раздельны.

Независимый проект `/home/shar/sozvon`, пакет `sozvon`, без импортов `app` и зависимости от `/home/shar/protokol`. Исходный «Протокол» не изменяется. Python 3.12, FastAPI/Jinja/JS, SQLite, subprocess. Проверки: `.venv/bin/python -m pytest`, `.venv/bin/ruff check sozvon tests`.

## Владение файлами

- Исполнитель runtime: только `sozvon/runtime/`, `tests/test_runtime*.py`.
- Исполнитель audio: только `sozvon/audio/`, `tests/test_audio*.py`.
- Исполнитель llm: только `sozvon/llm/`, `tests/test_llm*.py`.
- Исполнитель ui: только `sozvon/web/templates/`, `sozvon/web/static/`, `tests/test_ui*.py`.
- Родитель: `sozvon/__main__.py`, `sozvon/web/server.py`, `sozvon/services/`, `sozvon/storage/`, `sozvon/config.py`, `tests/` вне перечисленных выше, упаковка и интеграция.

Каждый пишет собственные `__init__.py` только внутри своей директории. Не коммитить: родитель проверяет и фиксирует общую согласованную версию. TDD: тест → ожидаемое падение → код → тест. Никаких скачиваний модели без явной отдельной команды.

## Runtime

`sozvon.runtime.host.WorkerHost`:

- `run(operation: str, payload: dict, *, timeout: float=120, stop_event: threading.Event|None=None, on_event: Callable[[dict],None]|None=None) -> dict`
- Один реальный дочерний процесс на вызов; безопасные stdin/stdout каналы, JSON UTF-8 с 4-byte big-endian length, предел кадра 1 MiB. Не pickle, не shell=True. Максимум stderr буфера, без вечных блокировок.
- Ошибка `WorkerError(code: str, message: str)` с публичным `code`.
- `stop_event` посылает команду stop. Для записи это завершение с сохранением; для других задач — отмена. После ограниченного ожидания worker принудительно завершается.
- В исходниках запуск `[sys.executable, '-m', 'sozvon', '--worker']`, для frozen `[sys.executable, '--worker']`. Родитель реализует dispatch флага в __main__.
- `sozvon.runtime.worker.main() -> int` читает команду `{operation,payload}`, запускает поток чтения управляющей stop/EOF, вызывает функцию обработки, возвращает события `{type:'ready'|'progress'|'result'|'error', ...}`. Worker stdout только протокол; нативный chatter уводится в stderr, сохранив отдельный fd для канала.
- `ping` возвращает `{pid, python, version}` и не загружает аудиомодули. Не добавлять произвольные тестовые execute/sleep/crash операции в продуктивный протокол; тестовые процессы допускаются в тестах через внедрённую команду.
- Аудио операции `devices`, `audio_info`, `transcribe`, `record`: lazy import `from sozvon.audio.operations import run`; вызов `run(operation, payload, stop_event, emit)`.
- LLM операция `report`: lazy import `from sozvon.llm.provider import generate`; вызов `generate(payload, stop_event, emit)`.
- emit принимает dict события, например `{type:'progress', stage:'Распознавание', completed:3,total:8,unit:'с'}`.

## Аудио

`sozvon.audio.operations.run(operation,payload,stop_event,emit)->dict`.

- devices: payload `{}`; ответ `{available:bool, reason:str, microphones:[{id,name}], outputs:[{id,name}], default_input:str|null, default_output:str|null}`. Недоступность PortAudio не ломает импорт приложения. По умолчанию выбирается устройство системы, не первое.
- audio_info: payload `{path:absolute_string}`; ответ `{duration_ms:int, sample_rate:int, channels:int, peak:float, rms:float}`. Реальная декодировка PyAV, предел безопасного анализа; вернуть ошибку повреждённого файла.
- transcribe: payload `{path, model_path, device:'cpu'|'auto'|'cuda', language:'auto'|'ru'..., compute_type:'int8'}`. model_path ОБЯЗАТЕЛЬНО существующий локальный каталог модели, не разрешать автоматическую загрузку. faster-whisper lazy. Ответ `{segments:[{id,ordinal,start_ms,end_ms,speaker:null,text}], model,device,warnings:[]}`. GPU-fallback только auto, с согласованным cpu/int8, ошибки ленивой итерации тоже учитываются. Истинный пустой результат -> ошибка «Речь не обнаружена», не выдуманный текст.
- record: payload `{output_dir:absolute_string, input_device:null|string, output_device:null|string, allow_partial:false, max_seconds:60}`. Микрофон sounddevice, system PyAudioWPatch WASAPI. Отдельные WAV-файлы по источнику; блоки пишутся вне колбэка, ограниченная очередь, отдельный формат каждого устройства. stop_event завершает с сохранением. Без обеих дорожек при allow_partial=false отказ до старта. emit уровни и duration; ответ `{tracks:[{source:'mic'|'system',path,duration_ms,sample_rate,channels,peak,rms}],duration_ms,warnings:[]}`. Поток явно стартуется. Для прототипа максимум записи настраиваемый <=14400 секунд; аварийное сегментированное восстановление пока не обещать.
- При ошибке ValueError/RuntimeError с безопасной причиной; runtime переводит в error.

## LLM

`sozvon.llm.provider.generate(payload,stop_event,emit)->dict`.

payload `{base_url,protocol:'openai'|'anthropic',model,api_key:'',timeout:300,max_output_tokens:16384,segments:[...],title,template:'meeting'|'client'|'technical',language:'ru'}`.

Настройки `llm.max_output_tokens`: строгое целое 256–65536, по умолчанию 16384;
`llm.timeout_s`: строгое целое 10–600, по умолчанию 300. Они входят в атомарный
снимок настроек/ключа и снимок задания. В payload таймаут называется `timeout`,
в обоих сетевых протоколах бюджет передаётся как `max_tokens`; дедлайн worker —
`timeout_s + 15`. Старые TOML без полей получают значения по умолчанию без изменения
ключей и данных. Один запуск — один POST, без автоматической смены модели/повторов.
Причины `length`/`max_tokens`, фильтрация (`content_filter`), отказ модели и неизвестное
завершение различаются безопасными статическими сообщениями до разбора документа.
Сообщение о лимите добавляет только собственный запрошенный бюджет и числовые счётчики
из allowlist (включая reasoning_tokens); raw-значение причины, отказ, рассуждения и текст
ответа не отражаются, неизвестная причина не эхоится. Длина сообщения ограничена 500
символами. Незавершённый ответ даже с валидным JSON не заменяет прежний отчёт.
Интерфейс «Настройки → Отчёт» содержит поля лимита и таймаута с теми же границами;
сохранение проверяется чтением назад.

Base URL включает версию для OpenAI (`http://127.0.0.1:1234/v1`); Anthropic база без /v1/messages. Не делать запросы на проверку endpoint автоматически. httpx trust_env=false, follow_redirects=false; HTTPS для удалённых, HTTP только loopback.

Ответ структурирован: `{summary:[{text,evidence:[{segment_id,quote}]}],decisions:[...],proposals:[...],tasks:[{text,owner:null|string,due:null|string,evidence:[...]}],questions:[...],risks:[...]}`. Ссылки на существующие segment id и реальные цитаты проверяются. Без доказуемого источника вернуть ошибку, не «успех». Не автоматизировать повтор платного запроса. Нет скрытого repair-вызова. Учитывать бюджет: для прототипа ограничить размер входа и объяснить, что длинный ещё не поддержан, не молча обрезать.

Итог `{document:структура, model, usage:{...}|null, warnings:[]}`. Нет Markdown-render unsafe. API-ключи не возвращать/не логировать. Для тестов HTTP MockTransport/respx, без настоящих ключей/запросов.

## Web API для интерфейса

Главная `/` — Jinja `index.html`, контекст только `version`. Статические `/static/...`. Всё приложение — одна локальная страница, JS строит безопасным DOM без innerHTML пользовательского контента.

Сессия: при `#key=...` JS забирает код, немедленно очищает адрес и POST `/api/session` JSON `{key}`. Ответ `{csrf}`; cookie HttpOnly. Без фрагмента GET `/api/session` возвращает `{csrf}` при действующей cookie либо 401. Заголовок X-CSRF-Token для POST/PUT/DELETE, credentials same-origin. 401 показывает «Откройте приложение через ярлык», не форму пароля. Не писать ключ/CSRF в localStorage.

JSON ошибки `{detail:'Понятная причина'}`. fetch должен читать detail. Все JSON-ответы UTF-8; ошибки постоянны на экране.

- GET `/api/meetings` -> `{items:[{id,title,created_at,kind:'text'|'audio'|'recording',status,error:null|string,segment_count,report_count,duration_ms:null|int}],job:null|{id,meeting_id,operation,status,stage,error,progress:null|dict}}`.
- POST `/api/import/text` `{title,text}` -> `{id}`; не запускает модель.
- POST `/api/import/audio` multipart file -> `{id}`; не запускает автоматически модель.
- GET `/api/meetings/{id}` -> `{id,title,kind,status,error,duration_ms,segments:[...],reports:[{id,created_at,model,document}],notes:string,sources:[{id,name,url}],job:null|{...}}`.
- POST `/api/meetings/{id}/transcribe` `{}` -> `{job_id}`.
- POST `/api/meetings/{id}/report` `{}` -> `{job_id}`.
- PUT `/api/meetings/{id}/notes` `{text}` -> `{saved:true}`.
- GET `/api/meetings/{id}/export?format=md|json` -> download. Не показывать DOCX/SRT как готовые до подключения.
- GET `/api/settings` -> `{llm:{base_url,protocol,model,configured,allow_remote:false},stt:{model_path,device,language},recording:{input_device:null,output_device:null},template:'meeting'|'client'|'technical',data_dir}`.
- PUT `/api/settings` кандидат той же структуры БЕЗ configured/data_dir; backend игнорирует только эти read-only поля. Поле `api_key` необязательно в llm: непустое сохраняет в keyring, пустое оставляет старый; delete_key:true удаляет. Никакой сырой ключ обратно.
- GET `/api/devices` -> контракт devices; это может занять секунды, UI показывает состояние и не блокирует остальные экраны.
- POST `/api/record/start` `{allow_partial:false,max_seconds:3600}` -> `{id,job_id}`.
- POST `/api/jobs/{id}/stop` `{}` -> `{accepted:true}`. Запись сохраняет аудио; остальное отменяется без успеха.
- GET `/api/health` -> `{ok:true,version}` без сессии.

Дизайн: название «Созвон», тёплая рабочая библиотека, rail 208px, Golos Text локально / serif отчёт (ассеты добавляет родитель). Один акцент #B23A14, фон #F5F2EC, текст #171614. Навигация «Записи», «Настройки», «О приложении», кнопка «Новая запись». Шаблоны выбираются в настройках, самостоятельный редактор и словарь пока не реализуются и не рисуются рабочими. Новая запись — вкладки «Текст», «Аудиофайл», «Записать». Карточка: «Отчёт / Расшифровка / Заметки / Источники», default готовый отчёт иначе текст. Настройки категориями «Отчёт», «Распознавание», «Запись». Честное отсутствие моделей/ключа, никакого демо-результата LLM. Первый запуск без compulsory-мастера.

Обязательная проверка 1440/1280/1024 и 200%, фокус, сохранение, нет внешних запросов и шрифтов, нет console errors. Каждый элемент должен действительно работать или быть явно недоступным с причиной. Русские подписи, aria-label, активные состояния. Верхняя строка «Техническая версия» не выдаёт это за полный релиз из архитектурного плана.
