# Реестр источников МИСБ

## Свежие потребности (01.10.2026, MISB_fresh_demand): 126 каналов, 131 наблюдение

Пакет — в `handoff_fd/`, отчёт — `REPORT-fd-2026-10-01.md`. Каналы — документы `misb-fd--FD-S…` и `_meta-fd`
(коллекция srcreg), наблюдения — коллекция `demand` (вкладка сайта «Запросы и сигналы»):

    python3 probe_v3.py <sources с source_url> probe.json
    python3 import_fd.py build handoff_fd W --date … --probe probe.json      # W/out/srcreg-fd, W/out/demand
    python3 registry_plan_v2.py … --fd W/out/srcreg-fd --write              # fd-daily, fd-pages (уходят из reg2-*)
    # план скопировать в W/db/config/ и пересобрать v3, cu, fd: очередь покажет fd-* у записей v3

## Корпоративные университеты (01.10.2026, MISB_corporate_universities): 247 организаций, 474 канала

Пакет — в `handoff_cu/`, отчёт — `REPORT-cu-2026-10-01.md`. Документы `misb-cu--<source_id>` (один на канал,
организация — в поле `inst`), `_meta-cu`:

    python3 probe_v3.py handoff_cu/sources.jsonl probe.json
    RELAY_URL=… RELAY_TOKEN=… python3 relay_probe_v2.py W/out/srcreg-cu relay.json
    python3 import_cu.py build handoff_cu W --date … --probe probe.json --relay-probe relay.json
    python3 registry_plan_v2.py W/out/srcreg-v3 ../collector/sources-plan.json --queries handoff_v3/search_queries.jsonl \
        --cu W/out/srcreg-cu --cu-queries handoff_cu/search_queries.jsonl --write
    python3 cu_holdings.py handoff_cu/institutions.jsonl ../collector/sources-plan.json --write
    # план скопировать в W/db/config/ и повторить import_cu.py build: очередь покажет ротацию

Ротации `cu-lists`, `cu-pages`, `cu-retry` и шаблоны `cu-search` пишут результаты в `meta/cu-checks` и
`meta/cu-query-checks` (поле `checksDoc` источника). Канал, адрес которого уже читает ротация v3 (`reg2-*`), второй раз не
читается: сайт показывает результат записи v3. `cu_holdings.py` добавляет российские группы пакета в поиск закупок
TenderGuru по холдингам (`terms`, в конец списка; круг остаётся 31 запуск).

## Пакет v3 (01.10.2026, MISB_Claude_handoff_v3): 507 записей — действующий

Пакет — в `handoff_v3/`, отчёт — `REPORT-v3-2026-10-01.md`. Импорт тем же скриптом с `--dataset misb-v3`
(документы `misb-v3--<source_id>`; заменили документы v2 с теми же номерами):

    python3 probe_v3.py handoff_v3/sources.jsonl probe.json [--skip старый_probe.json]   # Telegram читается как t.me/s/<канал>
    python3 import_v2.py build handoff_v3 W --dataset misb-v3 --date … --probe probe.json --relay-probe relay.json
    python3 registry_plan_v2.py W/out/srcreg-v3 ../collector/sources-plan.json --queries handoff_v3/search_queries.jsonl --write

`registry_plan_v2.py` сохраняет порядок адресов в ротации (курсор — позиция в списке), новые добавляет в конец,
адреса с конфликтом идентичности не подключает; `--queries` добавляет источник `reg3-search` (256 шаблонов, 8 за
запуск) со списком известных доменов `knownHosts` — новые домены из выдачи пишутся в `meta/discoveries`.
Совпадение только домена t.me, vk.com, ok.ru и подобных не считается связью с подключённым источником.

## Пакет v2 (01.10.2026, MISB_Claude_handoff_v2): 282 записи

Пакет целиком — в `handoff_v2/` (копия как есть, контрольные суммы manifest.json сходятся); это и есть хранилище
фактов и происхождения. В базу сайта (`srcreg`, документ `misb-v2--<source_id>`) идёт компактная запись для показа.
Отчёт о результатах — `REPORT-v2-2026-10-01.md`.

| Скрипт | Что делает |
| --- | --- |
| `probe_v2.py handoff_v2 probe.json` | доступ к адресам из облака (robots.txt соблюдается, капча и вход не обходятся) |
| `relay_probe_v2.py <srcreg-v2> relay.json` | повторная проверка недоступных через российский релей (`RELAY_URL`, `RELAY_TOKEN` из окружения) |
| `import_v2.py dry-run\|build handoff_v2 W --date … --probe … --relay-probe …` | сверка с источниками сбора и документы `srcreg` |
| `registry_plan_v2.py <srcreg-v2> ../collector/sources-plan.json [--write]` | источники ротации `reg2-lists`, `reg2-pages`, `reg2-retry` в плане сбора |

Правила (policy.json пакета):
- `source_id` — неизменяемый ключ; повторный импорт даёт те же документы байт в байт; ничего не удаляется,
  записи 29.09 остаются и связаны по адресу (`match.v1Docs`);
- одинаковый домен — не склейка: такая запись получает статус «сверить с подключённым»;
- статусы пакета (`content_read`, `search_index`, `candidate`) — как источник найден, а не работа сборщика;
  состояние подключения берётся из источников сайта (`sources`), реальный результат чтения адреса — из
  `meta/registry-checks` (пишет `collector/registry_checks.py` в обычном запуске);
- 403, 429, капча, вход, таймаут — задача доступа, не «заказов нет»; прочитано без строк по теме — `empty_success`;
- очередь: `collecting` (обычный источник сбора), `collecting_indirect` (лоты через другой источник), `rotation`
  (проверка по кругу reg2-*), `blocked_existing` (источник сбора выключен), `reconcile`, `access_task`,
  `candidate_verify`; у каждой записи `nextDueAt` = дата импорта + `metadata_poll_target_hours`.

Ручные связи — `MANUAL` в `import_v2.py`.

## Пакет 29.09.2026: 100 записей

Интеграция пакета `MISB_Claude_Code_sources` (2026-09-29.2) в действующий сайт. Отдельного сайта нет.

`import_registry.py`:
- `dry-run PKG WORK` — сопоставление 100 записей с источниками сайта и каталогом; отчёт без записи в базу;
- `probe PKG WORK` — чтение адресов из облака Claude Code без входа (robots.txt, капча и блокировки не
  обходятся, проверка TLS не отключается); ошибка чтения оттуда не доказывает недоступность с сервера сбора;
- `build PKG WORK --date ГГГГ-ММ-ДД` — документы коллекции `srcreg`: одна запись = один документ
  `<dataset_id>--<source_id>`, плюс `_map` (соответствие `dataset_id:source_id` → документ, `legacy_row_id`,
  ключи источников сайта) и `_meta`. Сборка детерминирована: повторный запуск даёт те же документы и id, дублей нет.

Правила:
- исследовательские поля (`research`, `proposal`, `probe`) хранятся отдельно от состояния подключения;
  статус `not_connected` из пакета не перезаписывает фактическое состояние источника;
- состояние подключения выводится на сайте из документов `sources` по `match.siteKeys`
  (`never_run`, `running`, `success`, `partial`, `blocked`, `auth_required`, `error`, `stale`);
- «нет результатов» не равно «недоступно»; суммы, ИНН и сроки не выдумываются;
- регулярных заданий не добавлено, запуск вручную кнопкой «Обновить».

`registry_plan.py` добавляет в план сбора три источника, у которых список закупок виден из облака без входа:
`kz-mpkz`, `kz-qazaqgaz`, `corp-akron` (статус `planned`).

Сопоставление вручную (`MANUAL`) сверено с пакетом 29.09.2026: 43 совпадения (1 через API, 38 прямых, 4
частичных), 57 новых, кандидатов без решения 0.
