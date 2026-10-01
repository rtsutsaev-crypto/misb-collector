# collector

Всё, что исполняет кнопка «Обновить»: инструкция сбора, план источников, словарь и скрипты. Репозиторий
подключён к Routine; сеанс переключается на коммит, закреплённый в тексте задания, и работает отсюда.
База сайта хранит только данные: код и инструкции из неё (`config/collector`, `config/*lib`,
`config/sources-plan`, `config/dictionary`) больше не читаются.

| Файл | Был в базе |
| --- | --- |
| `INSTRUCTIONS.md` | `config/collector.prompt` |
| `sources-plan.json` | `config/sources-plan` |
| `dictionary.json` | `config/dictionary` |

| Файл | Был в базе | Источник в `config/collector` |
| --- | --- | --- |
| `collector.py` | `config/collectorlib.code` | отбор, дедуп, сборка лида, вердикты |
| `pages.py` | `config/pageslib.code` | pages-script |
| `gosplan_delta.py` | `config/gosplandelta.code` | gosplan-delta |
| `forecast.py` | `config/forecastlib.code` | forecast |
| `contracts.py` | `config/contractslib.code` | contracts |
| `contracts_build.py` | `config/contractslib.buildCode` | contracts |
| `etp_search.py` | `config/etpsearch.code` | etp-search |
| `speaker_boards.py` | `config/speakerboards.code` | speaker-boards |
| `dadata.py` | `config/dadatalib.code` | enrich-dadata |
| `eisdocs.py` | `config/eisdocs.code` | eis-docs |

Правка инструкции, плана, словаря или скрипта: коммит сюда, затем новый SHA коммита в тексте задания
Routine (шаг 0). Без обновления SHA запуск продолжит брать прежнюю версию.
