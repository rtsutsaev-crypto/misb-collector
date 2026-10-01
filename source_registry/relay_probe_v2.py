"""Повторная проверка недоступных адресов через российский релей:
RELAY_URL=… RELAY_TOKEN=… python3 relay_probe_v2.py <папка srcreg-v2 из import_v2.py build> relay.json
Токен — только из окружения (текст задания), в файлы не пишется."""
import json,sys,os,subprocess,re,html,glob,concurrent.futures as cf
D,OUT=sys.argv[1],sys.argv[2];TOK=os.environ["RELAY_TOKEN"];RELAY=os.environ.get("RELAY_URL","").rstrip("/")
PROC=re.compile(r"закупк|тендер|конкурс|запрос\s+(?:котировок|предложений|цен)|аукцион|лот\b|извещени|tender|procurement",re.I)
DATE=re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b|\b20\d{2}-\d{2}-\d{2}\b")
docs=[json.load(open(f)) for f in glob.glob(os.path.join(D,'misb-v*--*.json'))]
todo=[d for d in docs if d['access']['state'] in ('unreachable','forbidden','http_error','empty_page')]
def get(d):
    u=d['url']
    r=subprocess.run(["curl","-s","-m","60","-H","X-Relay-Token: "+TOK,"-G","--data-urlencode","url="+u,"-D","-",RELAY+"/fetch"],capture_output=True,text=True,errors="ignore")
    head,_,body=r.stdout.partition("\r\n\r\n")
    m=re.search(r"x-upstream-status:\s*(\d+)",head,re.I); st=int(m.group(1)) if m else 0
    plain=html.unescape(re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>"," ",body,flags=re.S|re.I))
    t=re.search(r"<title[^>]*>(.*?)</title>",body,re.S|re.I)
    return d['sourceId'],{"status":st,"size":len(body),"procWords":len(PROC.findall(plain)),"dates":len(DATE.findall(plain)),"title":(" ".join(html.unescape(t.group(1)).split())[:100] if t else "")}
with cf.ThreadPoolExecutor(6) as ex: res=dict(ex.map(get,todo))
json.dump(res,open(OUT,'w'),ensure_ascii=False,indent=1)
import collections
print(len(todo),collections.Counter((v['status'], v['procWords']>=5 and v['dates']>=3) for v in res.values()))
