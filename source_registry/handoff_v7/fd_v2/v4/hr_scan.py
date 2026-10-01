#!/usr/bin/env python3
"""MISB read-only collector. Python 3.11+, standard library; optional local OCR/PDF tools.
No credentials, LLM calls, purchases, messages, or scheduler. Run --help.
"""
import argparse, csv, datetime as dt, hashlib, html, ipaddress, json, pathlib, re
import socket, sqlite3, subprocess, urllib.error, urllib.parse, urllib.request, urllib.robotparser
import zipfile, xml.etree.ElementTree as ET
from html.parser import HTMLParser

VERSION='4.0'
MAX_BYTES=5_000_000
UTC=dt.timezone.utc
def now(): return dt.datetime.now(UTC).isoformat()
def digest(v): return hashlib.sha256(v.encode()).hexdigest()
def read_jsonl(p): return [json.loads(x) for x in pathlib.Path(p).read_text().splitlines() if x.strip()]
def write_jsonl(p,rows): pathlib.Path(p).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
def canonical(url):
    p=urllib.parse.urlsplit(url);host=p.netloc.lower().removeprefix('www.')
    pairs=[(k,v) for k,v in urllib.parse.parse_qsl(p.query) if not k.startswith('utm_')]
    return urllib.parse.urlunsplit((p.scheme.lower(),host,p.path,urllib.parse.urlencode(pairs),''))
def date_value(value):
    if not value:return None
    try:
        d=dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return d.isoformat()
    except ValueError:
        m=re.fullmatch(r'(\d{2})\.(\d{2})\.(\d{4})',str(value).strip())
        try:return dt.date(int(m[3]),int(m[2]),int(m[1])).isoformat() if m else None
        except ValueError:return None
def bucket(published,as_of):
    if not published:return 'undated'
    try:
        d=dt.datetime.fromisoformat(published.replace('Z','+00:00'))
        if d.tzinfo:d=d.astimezone(dt.timezone(dt.timedelta(hours=3)))
        age=(dt.date.fromisoformat(as_of)-d.date()).days
    except ValueError:return 'date_invalid'
    return 'future_date_review' if age<0 else 'last_14_days' if age<14 else 'days_15_30' if age<30 else 'older_retained'

class Node:
    def __init__(self,tag='',attrs=()):self.tag=tag;self.attrs=dict(attrs);self.children=[]
    def text(self):
        if self.tag in ('script','style','nav','footer','noscript'):return ''
        return ' '.join(x if isinstance(x,str) else x.text() for x in self.children)
    def walk(self):
        yield self
        for x in self.children:
            if isinstance(x,Node):yield from x.walk()
class Tree(HTMLParser):
    void={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
    def __init__(self,s):
        super().__init__(convert_charrefs=True);self.root=Node();self.stack=[self.root];self.feed(s)
    def handle_starttag(self,tag,attrs):
        n=Node(tag,attrs);self.stack[-1].children.append(n)
        if tag not in self.void:self.stack.append(n)
    def handle_startendtag(self,tag,attrs):self.handle_starttag(tag,attrs);self.handle_endtag(tag)
    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,0,-1):
            if self.stack[i].tag==tag:self.stack=self.stack[:i];break
    def handle_data(self,s):self.stack[-1].children.append(s)
def clean(s):return re.sub(r'\s+',' ',html.unescape(s)).strip()
def links_from(node,url):
    found=[]
    for n in node.walk():
        if n.tag=='a' and n.attrs.get('href'):
            u=urllib.parse.urljoin(url,n.attrs['href'])
            if urllib.parse.urlsplit(u).scheme in ('http','https'):
                found.append({'url':u,'label':clean(n.text()),'rel':n.attrs.get('rel','')})
    return found
def declared_date(node):
    # Never infer publication from a deadline, event date, copyright or search crawl date.
    for n in node.walk():
        if n.tag=='meta' and n.attrs.get('property')=='article:published_time':
            return date_value(n.attrs.get('content')),'article:published_time'
        if n.tag=='time' and n.attrs.get('itemprop')=='datePublished':
            return date_value(n.attrs.get('datetime')),'time.datePublished'
        if n.tag=='script' and n.attrs.get('type')=='application/ld+json':
            try:
                obj=json.loads(''.join(x for x in n.children if isinstance(x,str)))
                objs=obj if isinstance(obj,list) else obj.get('@graph',[obj])
                for o in objs:
                    types=o.get('@type',[]);types=types if isinstance(types,list) else [types]
                    if set(types)&{'Article','BlogPosting','NewsArticle','DiscussionForumPosting'} and o.get('datePublished'):
                        return date_value(o['datePublished']),'jsonld.datePublished'
            except (ValueError,AttributeError,TypeError):pass
    return None,'not_established'
def triage(text):
    t=text.lower().replace('ё','е')
    patterns={
      'request_or_recommendation':r'ищем|нужен|нужна|нужны|посоветуй|порекомендуй|кого рекомендуете|кто может|looking for|recommend|іздейміз|патрэб',
      'training_topic':r'обуч|тренинг|тренер|семинар|вебинар|курс|фасилит|модератор|спикер|методолог|методист|scorm|lms|learning|оқыту|навуц|навуч',
      'partnership':r'сотрудничеств|партнер|партнёр|стать автор|пул эксперт|приглашаем.*преподав',
      'employment':r'ваканси|в штат|трудоустрой|полный рабочий|job opening',
      'provider_advertising':r'наш курс|мы предлагаем|запишитесь|купить курс',
      'replacement_urgent':r'отменил|сорвал|заменить тренера|срочно|завтра|на следующей неделе',
      'planning':r'бюджет.*обуч|план.*обуч|запрос.*цен|коммерческ.*предлож|следующ.*год',
      'adjacent_or_excluded':r'корпоративн\w*\s+(праздник|вечерин)|сварщик|слесар|станочник|поставка|консалтинг'}
    hits={k:m.group(0) for k,p in patterns.items() if (m:=re.search(p,t))}
    typ='unmatched_retained'
    if 'training_topic'in hits:typ='training_context_review'
    if 'request_or_recommendation'in hits and 'training_topic'in hits:typ='request_candidate'
    if 'partnership'in hits:typ='partnership_candidate'
    if 'employment'in hits:typ='employment_or_contract_review'
    return {'signal_type':typ,'signals':hits,'buyer_confirmed':False,'do_not_drop':True,'requires_context_review':True}
def record(source_id,url,title,text,published=None,basis='not_established',kind='page',native_id=None,parent=None):
    text=clean(text)
    return {'document_id':'HRD-'+digest(source_id+'|'+(native_id or canonical(url)))[:20],
      'source_id':source_id,'url':url,'native_id':native_id,'parent_id':parent,'kind':kind,
      'title':title,'text':text,'content_hash':digest(text),'published_at':published,'date_basis':basis,
      'retrieved_at':now(),**triage(text)}

def extract_html(raw,url,config):
    tree=Tree(raw).root;nodes=list(tree.walk());links=links_from(tree,url);items=[]
    adapter=config['adapter'];sid=config['source_id']
    if adapter=='telegram':
        for n in nodes:
            post=n.attrs.get('data-post')
            if not post:continue
            body=next((x for x in n.walk() if 'tgme_widget_message_text' in x.attrs.get('class','').split()),None)
            date=next((date_value(x.attrs.get('datetime')) for x in n.walk() if x.tag=='time'),None)
            items.append(record(sid,'https://t.me/'+post,post,body.text() if body else n.text(),date,'telegram_message_time' if date else 'not_established','message',post))
    elif adapter=='listing':
        for l in links:
            if re.search(config['item_url_pattern'],l['url']):
                items.append(record(sid,l['url'],l['label'],l['label'],kind='item_link'))
    else:
        main=next((x for x in nodes if x.tag=='main'),None) or next((x for x in nodes if x.tag=='article'),tree)
        title=next((clean(x.text()) for x in nodes if x.tag=='h1'),config['name'])
        date,basis=declared_date(tree)
        items.append(record(sid,url,title,main.text(),date,basis,'partnership_page' if adapter=='partnership' else 'page'))
    return items,links

def extract_reader(snapshot,config):
    """Ingest rendered reader text, preserving partial scope. Never invent hidden link URLs."""
    raw=snapshot['text'];url=snapshot['url'];sid=config['source_id']
    body='\n'.join(re.sub(r'^L\d+: ?', '',line) for line in raw.splitlines() if re.match(r'^L\d+:',line))
    if not body:body=raw
    first=raw[:1000].lower()
    if 'internal error' in first or 'failed to fetch' in first or 'not accessible via this tool' in first:
        return [],[],{'state':'fetch_failed','reason':'Reader reported an access/fetch error'}
    if any(x in first for x in ['are you not a robot','showcaptcha','verify you are human']):
        return [],[],{'state':'access_required','reason':'CAPTCHA or browser verification'}
    raw_urls=re.findall(r'https?://[^\s<>"】)]+',body)
    links=[{'url':u,'label':'Observed URL in reader content','rel':''} for u in dict.fromkeys(raw_urls)]
    if config['adapter']=='telegram':
        # Reader views can omit absolute message URLs and dates. Preserve the whole page as
        # a collection surface; do not misrepresent fragments as independently dated posts.
        kind='feed_snapshot_unsegmented'
    elif config['adapter']=='listing':kind='listing_snapshot_unsegmented'
    else:kind='partnership_page' if config['adapter']=='partnership' else 'page_snapshot'
    date=None;basis='not_established'
    m=re.search(r'(?:Дата (?:публикации|размещения)|Опубликовано)\s*[:：]?\s*(\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})',body,re.I)
    if m and config['adapter'] not in ('telegram','listing'):
        date=date_value(m[1]);basis='explicit_publication_label'
    doc=record(sid,url,raw.splitlines()[0],body,date,basis,kind)
    doc['retrieved_at']=snapshot.get('retrieved_at',now());doc['transport']='web_reader';doc['reader_ref']=re.search(r'【([^】]+)】',raw)[1] if re.search(r'【([^】]+)】',raw) else None
    return [doc],links,{'state':'content_read_partial','reason':'Rendered reader content; pagination and full history not established'}

def guard_url(url,hosts):
    p=urllib.parse.urlsplit(url)
    if p.scheme!='https' or p.username or p.password or p.hostname not in hosts or p.port not in (None,443):
        raise ValueError('URL outside explicit HTTPS host allowlist')
    addresses=socket.getaddrinfo(p.hostname,443,type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):raise ValueError('Non-public destination')
class RedirectGuard(urllib.request.HTTPRedirectHandler):
    def __init__(self,hosts):self.hosts=hosts
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        guard_url(newurl,self.hosts)
        return super().redirect_request(req,fp,code,msg,headers,newurl)
def fetch(url,hosts,timeout=12):
    guard_url(url,hosts)
    opener=urllib.request.build_opener(RedirectGuard(hosts))
    req=urllib.request.Request(url,headers={'User-Agent':'MISBResearchCollector/4.0 (read-only)','Accept':'text/html,application/xml;q=0.8'})
    with opener.open(req,timeout=timeout) as r:
        data=r.read(MAX_BYTES+1)
        if len(data)>MAX_BYTES:raise ValueError('Response exceeds byte limit; retain URL for deferred extraction')
        return data.decode(r.headers.get_content_charset() or 'utf-8',errors='replace'),r.geturl()
def robots_allowed(url,hosts):
    p=urllib.parse.urlsplit(url);rurl=f'{p.scheme}://{p.netloc}/robots.txt'
    try:
        raw,_=fetch(rurl,hosts);rp=urllib.robotparser.RobotFileParser();rp.parse(raw.splitlines())
        return rp.can_fetch('MISBResearchCollector',url),'robots_checked'
    except urllib.error.HTTPError as e:
        if e.code==404:return True,'robots_absent_404'
        return False,'robots_unavailable_'+str(e.code)
    except Exception:return False,'robots_unavailable'

class Store:
    def __init__(self,path):
        self.db=sqlite3.connect(path)
        self.db.executescript('''CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,payload TEXT);
          CREATE TABLE IF NOT EXISTS revisions(id TEXT,hash TEXT,payload TEXT,PRIMARY KEY(id,hash));
          CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,payload TEXT);
          CREATE TABLE IF NOT EXISTS routes(id TEXT PRIMARY KEY,payload TEXT);
          CREATE TABLE IF NOT EXISTS manager_notes(document_id TEXT PRIMARY KEY,note TEXT);''')
    def ingest(self,item):
        existed=self.db.execute('SELECT 1 FROM documents WHERE id=?',(item['document_id'],)).fetchone()
        old=self.db.execute('SELECT 1 FROM revisions WHERE id=? AND hash=?',(item['document_id'],item['content_hash'])).fetchone()
        payload=json.dumps(item,ensure_ascii=False)
        self.db.execute('INSERT OR IGNORE INTO revisions VALUES(?,?,?)',(item['document_id'],item['content_hash'],payload))
        self.db.execute('INSERT OR REPLACE INTO documents VALUES(?,?)',(item['document_id'],payload));self.db.commit()
        return 'unchanged' if old else 'revised' if existed else 'new'
    def route(self,url,parent,relation):
        ident='HRR-'+digest(parent+'|'+url+'|'+relation)[:20]
        obj={'route_id':ident,'url':url,'parent_id':parent,'relation':relation,'status':'discovered_not_fetched'}
        self.db.execute('INSERT OR IGNORE INTO routes VALUES(?,?)',(ident,json.dumps(obj,ensure_ascii=False)));self.db.commit()
    def run(self,row):
        self.db.execute('INSERT OR REPLACE INTO runs VALUES(?,?)',(row['run_id'],json.dumps(row,ensure_ascii=False)));self.db.commit()
    def export(self,out):
        out=pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
        for table in ['documents','revisions','runs','routes']:
            write_jsonl(out/(table+'.jsonl'),[json.loads(r[0]) for r in self.db.execute('SELECT payload FROM '+table)])

def collect(config,store,as_of,max_pages=2):
    hosts=set(config['allowed_hosts']);queue=[config['entry_url']];seen=set();errors=[];count=0
    run={'run_id':'RUN-'+digest(now()+config['source_id'])[:20],'source_id':config['source_id'],'started_at':now(),'transport':'http','coverage_complete':False}
    while queue and len(seen)<max_pages:
        url=queue.pop(0)
        if url in seen:continue
        seen.add(url);ok,reason=robots_allowed(url,hosts)
        if not ok:errors.append({'url':url,'state':reason});continue
        try:
            raw,final=fetch(url,hosts)
            if re.search(r'<title[^>]*>[^<]*(captcha|access denied|just a moment)',raw,re.I):
                errors.append({'url':url,'state':'access_required'});continue
            items,links=extract_html(raw,final,config)
            for item in items:
                item['freshness']=bucket(item['published_at'],as_of);store.ingest(item);count+=1
                if item['kind']=='item_link':store.route(item['url'],item['document_id'],'detail_to_fetch')
            for link in links:
                is_next='next' in link['rel'].split() or bool(re.search(r'^(следующая|далее|next|older|предыдущие)$',link['label'],re.I))
                relation='pagination' if is_next else 'attachment' if re.search(r'\.(pdf|docx|xlsx|png|jpg|jpeg)(\?|$)',link['url'],re.I) else 'linked_route'
                store.route(link['url'],config['source_id'],relation)
                if is_next and urllib.parse.urlsplit(link['url']).hostname in hosts:queue.append(link['url'])
        except Exception as e:errors.append({'url':url,'state':'fetch_failed','error_type':type(e).__name__})
    run.update({'finished_at':now(),'pages_attempted':len(seen),'documents':count,'errors':errors,'pending_urls':queue,
      'state':'content_read_partial' if count else 'fetch_or_extraction_unresolved','stop_reason':'page_limit' if queue else 'observed_routes_exhausted_not_proof_of_full_history'})
    store.run(run);return run

def ingest_telegram_export(path,source_id,store,as_of):
    data=json.loads(pathlib.Path(path).read_text());chat=str(data.get('id','unknown'))
    for m in data.get('messages',[]):
        if m.get('type')!='message':continue
        content=m.get('text','');content=''.join(x if isinstance(x,str) else x.get('text','') for x in content) if isinstance(content,list) else content
        ident=chat+'/'+str(m['id']);url=m.get('link') or 'telegram-export://'+ident
        # Export IDs identify a local record; they are not invented public post links.
        item=record(source_id,url,'Exported message '+str(m['id']),content,date_value(m.get('date')),'telegram_export_date','message',ident,
          'HRD-'+digest(source_id+'|'+chat+'/'+str(m['reply_to_message_id']))[:20] if m.get('reply_to_message_id') else None)
        item.update({'author':m.get('from'),'edited_at':date_value(m.get('edited')),'freshness':bucket(item['published_at'],as_of),'transport':'authorized_export'})
        store.ingest(item)
        for k in ('file','photo'):
            if m.get(k):store.route(m[k],item['document_id'],'local_attachment_requires_review')

def extract_attachment(path,ocr_languages='rus+eng'):
    p=pathlib.Path(path)
    if p.stat().st_size>20_000_000:raise ValueError('Attachment too large')
    if p.suffix.lower() in ('.docx','.xlsx'):
        with zipfile.ZipFile(p) as z:
            if sum(i.file_size for i in z.infolist())>40_000_000:raise ValueError('Expanded attachment too large')
            if p.suffix.lower()=='.docx':
                tree=ET.fromstring(z.read('word/document.xml'));return '\n'.join(''.join(p.itertext()) for p in tree.iter() if p.tag.endswith('}p'))
            ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'};ss=[]
            if 'xl/sharedStrings.xml' in z.namelist():ss=[''.join(x.itertext()) for x in ET.fromstring(z.read('xl/sharedStrings.xml'))]
            result=[]
            for name in z.namelist():
                if re.fullmatch(r'xl/worksheets/sheet\d+\.xml',name):
                    for c in ET.fromstring(z.read(name)).findall('.//m:c',ns):
                        v=c.find('m:v',ns);value=v.text if v is not None else ''.join(c.itertext())
                        if c.get('t')=='s' and value:value=ss[int(value)]
                        result.append(name+' '+c.get('r','')+' '+str(value or ''))
            return '\n'.join(result)
    if p.suffix.lower()=='.pdf':cmd=['pdftotext','-layout',str(p),'-']
    elif p.suffix.lower() in ('.png','.jpg','.jpeg','.tif','.tiff'):cmd=['tesseract',str(p),'stdout','-l',ocr_languages]
    elif p.suffix.lower() in ('.txt','.csv'):return p.read_text(errors='replace')
    else:raise ValueError('Format unsupported; retain for external extractor')
    return subprocess.run(cmd,capture_output=True,text=True,check=True,timeout=45).stdout

def audit_sample(store,out,per_source=10):
    items=[json.loads(x[0]) for x in store.db.execute('SELECT payload FROM documents')];groups={}
    for item in items:groups.setdefault(item['source_id'],[]).append(item)
    rows=[]
    for sid,group in groups.items():
        # Sample from all items before keyword filtering. Stable selection supports replay.
        for x in sorted(group,key=lambda x:digest('audit-v4|'+x['document_id']))[:per_source]:
            rows.append({'document_id':x['document_id'],'source_id':sid,'url':x['url'],'kind':x['kind'],'published_at':x['published_at'],'machine_signal':x['signal_type'],'human_relevant':'','human_need_type':'','miss_reason':'','reviewer':''})
    with pathlib.Path(out).open('w',newline='',encoding='utf-8-sig') as f:
        fields=list(rows[0]) if rows else ['document_id','source_id','human_relevant','miss_reason']
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
    return len(rows)

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--db',default='hr_scan.sqlite');ap.add_argument('--as-of',default=dt.datetime.now(dt.timezone(dt.timedelta(hours=3))).date().isoformat())
    sub=ap.add_subparsers(dest='command',required=True)
    p=sub.add_parser('fetch');p.add_argument('--config',required=True);p.add_argument('--source',required=True);p.add_argument('--max-pages',type=int,default=2)
    p=sub.add_parser('reader');p.add_argument('--snapshots',required=True);p.add_argument('--config',required=True)
    p=sub.add_parser('telegram-export');p.add_argument('--file',required=True);p.add_argument('--source',required=True)
    p=sub.add_parser('attachment');p.add_argument('--file',required=True);p.add_argument('--source',required=True);p.add_argument('--parent',required=True);p.add_argument('--ocr-lang',default='rus+eng')
    p=sub.add_parser('export');p.add_argument('--out',required=True)
    p=sub.add_parser('audit-sample');p.add_argument('--out',required=True);p.add_argument('--per-source',type=int,default=10)
    a=ap.parse_args();st=Store(a.db)
    if a.command=='fetch':
        cfg=next(c for c in read_jsonl(a.config) if c['source_id']==a.source)
        print(json.dumps(collect(cfg,st,a.as_of,max(1,min(a.max_pages,20))),ensure_ascii=False))
    elif a.command=='reader':
        cfgs=read_jsonl(a.config);snaps=json.loads(pathlib.Path(a.snapshots).read_text());results=[]
        for snap in snaps:
            cfg=next((c for c in cfgs if snap['url'] in c.get('reader_urls',[c['entry_url']])),None)
            if cfg is None:continue
            items,links,status=extract_reader(snap,cfg)
            for item in items:item['freshness']=bucket(item['published_at'],a.as_of);st.ingest(item)
            for l in links:st.route(l['url'],cfg['source_id'],'reader_observed_link')
            run={'run_id':'RUN-'+digest(now()+snap['url'])[:20],'source_id':cfg['source_id'],'url':snap['url'],'transport':'web_reader','documents':len(items),'coverage_complete':False,**status};st.run(run);results.append(run)
        print(json.dumps(results,ensure_ascii=False))
    elif a.command=='telegram-export':ingest_telegram_export(a.file,a.source,st,a.as_of)
    elif a.command=='attachment':
        body=extract_attachment(a.file,a.ocr_lang);obj=record(a.source,pathlib.Path(a.file).name,pathlib.Path(a.file).name,body,kind='attachment',native_id=a.parent+'|'+pathlib.Path(a.file).name,parent=a.parent)
        obj['ocr_review_required']=pathlib.Path(a.file).suffix.lower() in ('.png','.jpg','.jpeg','.tif','.tiff')
        st.ingest(obj)
    elif a.command=='export':st.export(a.out)
    else:print(audit_sample(st,a.out,a.per_source))
if __name__=='__main__':main()
