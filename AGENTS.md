# AGENTS.md — правила для Codex и других агентов в этом репозитории

Репозиторий — сбор закупок обучения и ДПО для «Монитора-консолидатора МИСБ» и сайт монитора. Его можно запускать двумя
способами:

- **на Claude**: артефакт и задание Routine; файлы `routine/` и `config/collector` в базе сайта;
- **в отдельной версии** на своём сервере: папка `standalone/`. Модель идёт через OpenRouter, база — SQLite.

Если вы работаете в форке для отдельной версии, всё про Claude (`routine/`, ссылки на claude.ai) — история, её не трогайте.

## Где что

| Путь | Что |
|---|---|
| `collector/INSTRUCTIONS.md` | полная инструкция сбора, её дословно выполняет агент сбора; версия в первой строке |
| `collector/sources-plan.json` | план источников (key, type, urls, light, manualOnly…); `collector/dictionary.json` — словарь отбора |
| `collector/*.py` | скрипты сбора, только стандартная библиотека Python 3 и curl |
| `site/monitor.html` | сайт (один файл); обращается к базе через `window.claude.use("db")` |
| `standalone/` | отдельная версия: сервер, прослойка `window.claude`, агент OpenRouter, развёртывание — см. `standalone/README.md` |

## Жёсткие правила

1. **Ключи** (ГосПлан, TenderGuru, Чекко, DaData, РосТендер, TenderLand, OpenRouter, токен релея) — только в `/etc/misb/env` на сервере.
   Никогда в git, базу, журналы, отчёты или тесты.
2. **Не обходить** капчи, проверки на бота, страницы входа и robots.txt. Такой источник получает статус blocked.
3. **Никаких автоматических рассылок** и писем заказчикам.
4. **Платные тарифы и покупки** — только предложение владельцу, без самостоятельного подключения.
5. **Данные не выдумывать**: номер, название, срок и сумма лида берутся только со страницы или из API.
6. **Формат базы общий для обеих версий**: документы `коллекция/документ`, JSON, версии. Не меняйте форму документов
   `leadsets`, `leadupdates`, `progress/current`, `config/fit` без правки `site/monitor.html` и скриптов, которые их читают.

## Проверка перед коммитом

```bash
cd standalone && python3 -m unittest discover -s tests      # сервер-агент-релей без сети
python3 -m py_compile collector/*.py standalone/misb/*.py
python3 -c "import json; json.load(open('collector/sources-plan.json')); json.load(open('collector/dictionary.json'))"
```

Сайт проверяйте локально:

```bash
cd standalone
python3 -m misb.snapshot import <снимок> --db /tmp/t.sqlite3
python3 -m misb.server --db /tmp/t.sqlite3 --no-auth --port 8099
```

Откройте http://127.0.0.1:8099. Таблица лидов должна заполниться, а в консоли браузера не должно быть ошибок.

## Типовые задачи

- **Добавить или выключить источник.** Правьте запись в `collector/sources-plan.json`: поля как у соседних записей того
  же type. Если меняется порядок работы, правьте и `collector/INSTRUCTIONS.md`: номер версии в первой строке плюс строка
  «ИЗМЕНЕНИЯ x.y». Сайт и база правок не требуют.
- **Отбор лидов** (что считать обучением). Правьте `collector/dictionary.json` и правила в инструкции. Оценку
  соответствия (fit) на сайте задаёт документ `config/fit` в базе: менять через API сервера или snapshot.
- **Агент сбора** (`standalone/misb/agent.py`, `tools.py`):
  - текст запуска — `launch_text()`;
  - инструменты повторяют контракт инструментов Claude (ArtifactData: get/list/query/set/update/delete/str_replace/batch,
    `if_version`, `out_dir`, `file_path`), от него зависит инструкция;
  - после правок запускайте тесты.
- **Развёртывание изменений** на сервере:

  ```bash
  cd /opt/misb && git pull && systemctl restart misb-server
  ```

  Сбор берёт код из `collector/` при каждом запуске.
- **Расходы на модель** настраиваются в `/etc/misb/env`: `OPENROUTER_MODEL`, `OPENROUTER_PAGE_MODEL`, `MISB_MAX_USD`,
  `MISB_HISTORY_CHARS`, `MISB_WEBSEARCH`.

## Стиль

- Тексты, сообщения и комментарии — по-русски, кратко.
- Время — московское.
- Код — только стандартная библиотека Python, без новых зависимостей без необходимости. Стиль кода — как у соседнего.
