# collector

Скрипты сбора для кнопки «Обновить». Облачный сеанс Routine клонирует этот репозиторий на закреплённом
коммите и запускает скрипты отсюда; код из базы сайта (`config/*lib`) больше не исполняется.

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

Правка скрипта: коммит сюда, затем новый SHA коммита в тексте задания Routine (шаг «Код сбора»).
Без обновления SHA запуск продолжит брать прежнюю версию.
