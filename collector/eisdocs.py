import json,re,sys,time,urllib.request,concurrent.futures as cf
import os
GK=os.environ.get('GOSPLAN_KEY','')
PLAT={'rts-tender.ru':'РТС-тендер','sberbank-ast.ru':'Сбербанк-АСТ','roseltorg.ru':'Росэлторг','etp-ets.ru':'Фабрикант (НЭП)','fabrikant.ru':'Фабрикант','zakazrf.ru':'АГЗ РТ','lot-online.ru':'РАД','etpgpb.ru':'ЭТП ГПБ','tektorg.ru':'ТЭК-Торг','astgoz.ru':'АСТ ГОЗ','b2b-center.ru':'B2B-Center','otc.ru':'OTC.ru','tender.pro':'Tender.Pro','gazneftetorg.ru':'Газнефтеторг','onlinecontract.ru':'Онлайн Контракт','etprf.ru':'ЭТП РФ','rt.ru':'Ростелеком','zakupki.mos.ru':'Портал поставщиков Москвы','sibur.ru':'СИБУР','uralbidin.ru':'Уралбидин','etp.zakazrf.ru':'АГЗ РТ','bidzaar.com':'Bidzaar','estp.ru':'ЭСТП','tender.lot-online.ru':'РАД','etpzakupki.tatar':'Закупки Татарстана','setonline.ru':'СЭТ','rosatom.ru':'Росатом','zakupki.rosatom.ru':'Росатом','komos.ru':'КОМОС','polyus.com':'Полюс','ets.ru':'НЭП'}
TZRE=re.compile(r'описани[ея] объекта закупки|техническ\w* задани|(^|[^а-яa-z])тз([^а-яa-z]|$)|спецификаци|требовани\w* к (услуг|оказани|содержани\w* услуг)|программ\w* обучени|учебн\w* план|задание на оказание',re.I)
def host(u):
    m=re.match(r'https?://([^/:]+)',u or ''); return (m.group(1).lower() if m else '').removeprefix('www.')
def pname(url,name):
    h=host(url)
    for d,n in PLAT.items():
        if h==d or h.endswith('.'+d): return n
    return (name or h or '').strip()
def aslist(v): return v if isinstance(v,list) else ([v] if v else [])
def get(u):
    for i in range(4):
        try:
            r=urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'misb'}),timeout=60); return r.status,r.read()
        except urllib.error.HTTPError as e:
            if e.code==429: time.sleep(2); continue
            if e.code==404: return 404,b''
            time.sleep(3)
        except Exception: time.sleep(3)
    return 0,b''
def parse(num,d):
    law='44' if num.startswith('0') else '223'
    docs=sorted(d.get('docs') or [],key=lambda x:x.get('published_at') or '')
    notices=[x for x in docs if re.search(r'Notification|purchaseNotice',x.get('doc_type') or '') and not re.search(r'Cancel|Rejection|Clarification|Prolongation',x.get('doc_type') or '')]  # 2.0: отмена — не извещение, в ней нет площадки
    last=notices[-1] if notices else (docs[-1] if docs else None)
    out={'law':law,'checkedAt':time.strftime('%Y-%m-%d')}
    if not last: return None
    s=last.get('source') or {}
    ci=s.get('commonInfo') or {}
    etp=ci.get('ETP') or s.get('electronicPlaceInfo') or {}
    if etp.get('url') or etp.get('name'):
        out['platform']=pname(etp.get('url'),etp.get('name')); out['platformUrl']=etp.get('url') or ''
        if etp.get('name') and etp.get('name')!=out['platform']: out['platformFull']=etp['name']
    elif s.get('applSubmisionPlace'): out['platformNote']=str(s['applSubmisionPlace'])[:200]
    # 2.0: электронная ли закупка и каким способом — без площадки это «без ЭТП», а не «неизвестно»
    out['electronic']=bool(etp.get('url') or etp.get('name'))
    if law=='223' and s.get('purchaseCodeName'): out['method']=str(s['purchaseCodeName'])[:80]
    elif law=='44': out['method']=str(last.get('doc_type') or '')[:40]
    if s.get('urlVSRZ'): out['platformCard']=s['urlVSRZ']
    canc=[x for x in docs if re.search(r'Rejection|Cancel',x.get('doc_type') or '') and (x.get('published_at') or '')>=(last.get('published_at') or '')]
    if canc: out['cancelled']=True; out['cancelledAt']=str(canc[-1].get('published_at') or '')[:10]
    out['eisUrl']=ci.get('href') or ('https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber='+num if law=='223' else 'https://zakupki.gov.ru/epz/order/extendedsearch/results.html?searchString='+num)
    pf=(s.get('printFormInfo') or {}).get('url')
    if pf: out['printForm']=pf
    items=[];seen=set()
    def add(att,kind):
        u=att.get('url'); 
        if not u or u in seen: return
        seen.add(u)
        name=(att.get('docDescription') or att.get('fileName') or '').strip()
        fn=(att.get('fileName') or '').strip()
        k=((att.get('docKindInfo') or {}).get('name') or '')
        it={'name':name[:140],'url':u}
        if fn and fn!=name: it['file']=fn[:140]
        if att.get('fileSize'):
            try: it['size']=int(att['fileSize'])
            except: pass
        if att.get('docDate'): it['date']=str(att['docDate'])[:10]
        if kind: it['kind']=kind
        if TZRE.search(' '.join([name,fn,k])): it['tz']=True
        items.append(it)
    for a in aslist((s.get('attachmentsInfo') or {}).get('attachmentInfo'))+aslist((s.get('attachments') or {}).get('document')): add(a,'')
    for x in reversed(docs):
        if x is last: continue
        xs=x.get('source') or {}; t=x.get('doc_type') or ''
        kind='разъяснение' if 'Clarification' in t else ('протокол' if 'rotocol' in t else ('прежняя редакция' if x in notices else t))
        if kind=='прежняя редакция': continue
        for a in aslist((xs.get('attachmentsInfo') or {}).get('attachmentInfo'))+aslist((xs.get('attachments') or {}).get('document')): add(a,kind)
    out['docs']=items[:15]; out['docsTotal']=len(items)
    out['tz']=[{'name':i['name'],'url':i['url']} for i in items if i.get('tz') and not i.get('kind')][:3]
    if not out['tz']:
        g=[i for i in items if not i.get('kind') and re.search(r'документаци|извещени|приложени\w*\s*(№\s*)?1\b|договор|контракт',(i['name']+' '+i.get('file','')),re.I)]
        g.sort(key=lambda i:(0 if re.search(r'документаци',i['name']+i.get('file',''),re.I) else 1 if re.search(r'приложени',i['name']+i.get('file',''),re.I) else 2))
        if g: out['tzLikely']=[{'name':i['name'],'url':i['url']} for i in g[:2]]
    return out
def fetch(num):
    path=('/fz44/purchases/' if num.startswith('0') else '/fz223/purchases/')+num
    c,b=get('https://v2.gosplan.info'+path+'?apikey='+GK)
    time.sleep(0.2)
    if c!=200: return num,None,c
    try: return num,parse(num,json.loads(b)),200
    except Exception as e: return num,None,'parse:'+str(e)[:60]
if __name__=='__main__':
    # usage: GOSPLAN_KEY=... python3 eisdocs.py ids.json out.json [errors.json]  (ids.json — список номеров ЕИС;
    # errors.json — {номер: код ошибки} для eis_misses.py)
    ids=json.load(open(sys.argv[1])); res={}; errs={}
    with cf.ThreadPoolExecutor(5) as ex:
        for n,r,c in ex.map(fetch,ids):
            if r: res[n]=r
            else: errs[n]=c
    json.dump(res,open(sys.argv[2],'w'),ensure_ascii=False)
    if len(sys.argv)>3: json.dump({n:str(c) for n,c in errs.items()},open(sys.argv[3],'w'),ensure_ascii=False)
    print(len(ids),'ok',len(res),'err',len(errs),list(errs.items())[:5])
    print('platform',sum(1 for r in res.values() if r.get('platform')),'tz',sum(1 for r in res.values() if r.get('tz')),'docs',sum(1 for r in res.values() if r.get('docs')))
