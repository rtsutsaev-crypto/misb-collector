"""Доступ к адресам реестра v2 из облака: python3 probe_v2.py handoff_v2 probe.json (robots.txt соблюдается, вход и капча не обходятся)."""
import json,sys,os,datetime as dt
sys.path.insert(0,os.path.join(os.path.dirname(os.path.abspath(__file__)),"..","sources_probe"))
import probe_urls as pu
H=sys.argv[1]; out=sys.argv[2]
L=[json.loads(l) for l in open(os.path.join(H,'sources.jsonl'),encoding='utf-8')]
urls,owner=[],{}
for r in L:
    for kind,u in (("url",r.get("source_url")),("evidence",r.get("evidence_url"))):
        if u and u.startswith("http") and u not in owner:
            owner[u]=(r["source_id"],kind); urls.append(u)
res=pu.run(urls,1.0,8)
today=dt.date.today().isoformat(); by={}
for x in res:
    sid,kind=owner[x["url"]]
    by.setdefault(sid,{"checkedOn":today,"from":"облако сеанса Claude Code, без входа, robots.txt соблюдён"})[kind]={k:x.get(k) for k in ("url","status","final","robots","gate","size","title")}|{"procWords":x.get("proc"),"dates":x.get("dates"),"trainWords":x.get("train")}
json.dump(by,open(out,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
print(len(urls),'urls',len(by),'sources')
