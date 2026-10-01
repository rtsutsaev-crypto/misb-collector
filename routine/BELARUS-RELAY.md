# Беларусь через релей: недостающий сертификат

**Что не так.** Сайты icetrade.by и goszakupki.by (а также gias.by, api.goszakupki.by) отдают только свой
сертификат и не присылают промежуточный сертификат GlobalSign. Браузер докачивает его сам, поэтому в браузере всё
открывается; Python, Node и curl на релее — нет, отсюда `CERTIFICATE_VERIFY_FAILED` и статус 599.
Проверено 01.10.2026: SSL Labs для icetrade.by — «chain issues: incomplete», сервер присылает 1 сертификат.

Белорусская национальная криптография (bign, СТБ 34.101) тут ни при чём: сертификаты обычные RSA от GlobalSign,
корень GlobalSign Root R46 уже есть в стандартных хранилищах (Mozilla, certifi, Debian). Не хватает только
промежуточного звена. Проверку TLS отключать не нужно и нельзя.

## Что сделать на сервере релея (5 минут)

1. Скопируйте на сервер файл [`by-chain.pem`](by-chain.pem) из этой папки (4 сертификата GlobalSign) или скачайте
   их с сайта GlobalSign и сверьте отпечатки:

   | Файл | Что это | SHA-256 |
   | --- | --- | --- |
   | http://secure.globalsign.com/cacert/gsgccr46alphasslca2025.crt | промежуточный, которым подписаны сайты сейчас | `D2:AC:9D:CC:7A:25:50:29:A5:F5:18:0D:63:F1:A9:ED:60:E1:14:12:A8:5C:14:8D:4B:71:F6:AE:1A:36:69:5C` |
   | http://secure.globalsign.com/cacert/rootr46.crt | корень R46 | `4F:A3:12:6D:8D:3A:11:D1:C4:85:5A:4F:80:7C:BA:D6:CF:91:9D:3A:5A:88:B0:3B:EA:2C:63:72:D9:3C:40:C9` |
   | http://secure.globalsign.com/cacert/gsgccr6alphasslca2025.crt | промежуточный R6 (старый сертификат api.goszakupki.by, до 02.10.2026) | `A8:83:55:92:31:F8:38:8D:AF:35:CE:41:C8:10:10:40:AE:8F:D9:B6:56:43:42:47:B9:47:5A:F5:92:CC:08:CA` |
   | http://secure.globalsign.com/cacert/root-r6.crt | корень R6 | `2C:AB:EA:FE:37:D0:6C:A2:2A:BA:73:91:C0:03:3D:25:98:29:52:C4:53:64:73:49:76:3A:3A:B5:AD:6C:CF:69` |

   Скачанные файлы — в формате DER: `openssl x509 -inform DER -in X.crt -out X.pem` и
   `openssl x509 -in X.pem -noout -fingerprint -sha256` для сверки.

2. Добавьте их к доверенным сертификатам — выберите способ по тому, на чём написан релей:
   - **Весь сервер (Debian/Ubuntu), подходит почти всегда:**
     ```sh
     sudo apt install -y ca-certificates
     sudo csplit -s -z -f /usr/local/share/ca-certificates/globalsign-by- -b '%02d.crt' by-chain.pem '/-----BEGIN/' '{*}'
     sudo update-ca-certificates
     ```
     Python `requests` по умолчанию берёт сертификаты из certifi, а не из системы — для него ещё
     `export REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt` в окружении релея.
   - **Только Python requests:** `cat "$(python3 -m certifi)" by-chain.pem > /etc/relay/ca.pem` и
     `REQUESTS_CA_BUNDLE=/etc/relay/ca.pem`.
   - **Python httpx / aiohttp / urllib:** `ctx = ssl.create_default_context(cafile=certifi.where());
     ctx.load_verify_locations("by-chain.pem")` и передать `verify=ctx` (httpx) или `ssl=ctx` (aiohttp, urllib).
   - **Node:** `NODE_EXTRA_CA_CERTS=/etc/relay/by-chain.pem` в окружении процесса.

   Файл, переданный как `--cacert`/`cafile`, *заменяет* стандартный набор, поэтому в нём должны быть и корни —
   в `by-chain.pem` они есть. Ограничивать эти сертификаты только белорусскими сайтами не нужно: это публичные
   центры GlobalSign.

3. Перезапустите релей.

## Проверка на сервере

```sh
curl --cacert by-chain.pem -sS -o /dev/null -w '%{http_code}\n' https://www.icetrade.by/
openssl s_client -connect icetrade.by:443 -servername icetrade.by -CAfile by-chain.pem </dev/null 2>/dev/null | grep 'Verify return code'
```
Ожидается код 200 (или 301/302) и `Verify return code: 0 (ok)`. То же для `goszakupki.by` и `gias.by`.
Проверка через сам релей: запрос `/fetch` на `https://icetrade.by/` должен вернуть `x-upstream-status: 200`, а не 599.

Если сертификат GlobalSign Root R46 не находится вовсе — устарели `ca-certificates`/`certifi`:
`sudo apt install -y ca-certificates` и `pip install -U certifi`.

## После этого

Источники `by-icetrade` и `by-goszakupki` в `collector/sources-plan.json` сейчас выключены (`type: skip`, прежний тип —
в `prevType`). Когда релей начнёт отдавать эти сайты, их нужно вернуть: `type` = значение `prevType`, и закрепить
новый коммит в задании.

Источники проверки: SSL Labs (`api.ssllabs.com/api/v3/analyze?host=icetrade.by`), Cert Spotter (журнал сертификатов
goszakupki.by), сайт GlobalSign. Для goszakupki.by неполная цепочка не проверена напрямую (SSL Labs не достучался
до сайта из-за рубежа), но сертификат выдан тем же промежуточным центром GlobalSign, и ошибка на релее та же.
