#!/usr/bin/env python3
"""v7 source registry validation, diverse plans, bounded read-only batch collection."""
import argparse, collections, datetime as dt, importlib.util, json, pathlib, sys, urllib.parse
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
SPEC=importlib.util.spec_from_file_location('legacy_hr_scan',ROOT/'v6/v5/legacy/F02/claude_package/v4/hr_scan.py')
collector=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(collector)
def rows(name):return collector.read_jsonl(ROOT/'data'/f'{name}.jsonl')
def validate():
    errors=[];registry=rows('source_registry');exp=rows('expansion_candidates');reviews=rows('priority_reviews');checks=rows('page_checks');configs=rows('collector_config')
    ids={r['source_id'] for r in registry}
    def check(ok,msg):
        if not ok:errors.append(msg)
    check(len(ids)==len(registry),'Duplicate registry IDs')
    check(len(exp)>=400,'Fewer than 400 expansion candidates')
    check(all(r['new_source_key'] and r['discovery_urls'] and r['discovery_refs'] for r in exp),'Unproven expansion candidate')
    check(len(reviews)==100,'Priority review count mismatch')
    for name in ['expansion_candidates','priority_reviews','page_checks','collector_config','observed_routes','surface_tasks','source_performance','feedback','commercial_examples','discovery_provenance']:
        rr=rows(name);check(all(r['source_id'] in ids for r in rr),f'Orphan source ID in {name}')
    attempt_ids={r['attempt_id'] for r in checks}
    check(all(set(r['attempt_ids'])<=attempt_ids for r in reviews),'Missing review evidence')
    check(all(not r['history_complete'] for r in registry),'Unsupported full history assertion')
    check(all(not r['buyer_confirmed'] for r in rows('commercial_examples')),'Unsupported buyer confirmation')
    check(len(rows('legacy_route_classification'))==4156,'Legacy route count changed')
    for c in configs:
        p=urllib.parse.urlsplit(c['entry_url'])
        check(p.hostname in c['allowed_hosts'],f'Host absent from allowlist: {c["source_id"]}')
    policy=json.loads((ROOT/'policy.json').read_text())
    check(policy['monthly_global_cash_cap_rub']<=10000,'Budget exceeds cap')
    check(not policy['paid_calls_enabled'],'Paid calls enabled')
    return {'passed':not errors,'errors':errors,'registry':len(registry),'expansion':len(exp),'reviews':len(reviews),'attempts':len(checks),'configs':len(configs)}
def diverse(items):
    groups=collections.defaultdict(list)
    for r in items:groups[r['research_track']].append(r)
    out=[]
    while groups:
        for k in sorted(list(groups)):
            out.append(groups[k].pop(0))
            if not groups[k]:del groups[k]
    return out

def plan(limit=20):
    if not 1<=limit<=100:raise ValueError('Plan limit must be 1..100')
    cfg={x['source_id']:x for x in rows('collector_config')};sources=[r for r in rows('source_registry') if r['source_id'] in cfg]
    review_map={r['source_id']:r for r in rows('priority_reviews')}
    actionable={'cooperation_invitation','expert_pool','course_author_pool','content_partner_pool','paid_corporate_teacher_pool','speaker_application','association_partnership','expert_and_reseller_pool','training_program_operator'}
    def rank(r):
        review=review_map.get(r['source_id'],{})
        return (0 if review.get('source_kind') in actionable else 1,0 if r['access_state']=='content_read_partial' else 1,0 if review.get('public_contact') else 1,r['source_id'])
    reviewed=diverse(sorted([r for r in sources if r['review_id']],key=rank));unreviewed=diverse([r for r in sources if not r['review_id']])
    explore=max(1,round(limit*.2));selected=[(x,'priority') for x in reviewed[:limit-explore]]+[(x,'exploration') for x in unreviewed[:explore]]
    seen={r['source_id'] for r,_ in selected}
    for r in diverse(sources):
        if len(selected)>=limit:break
        if r['source_id'] not in seen:selected.append((r,'fill'));seen.add(r['source_id'])
    return [{'source_id':r['source_id'],'name':r['name'],'research_track':r['research_track'],'lane':lane,'entry_url':cfg[r['source_id']]['entry_url'],'manual_only':cfg[r['source_id']]['manual_only'],'status':'planned_not_run','coverage_14d':'not_established'} for r,lane in selected]

def scan(ids,db,as_of,max_pages):
    dt.date.fromisoformat(as_of)
    if not 1<=len(ids)<=20:raise ValueError('Select 1..20 source IDs per batch')
    if len(set(ids))!=len(ids):raise ValueError('Duplicate source IDs in batch')
    if not 1<=max_pages<=5:raise ValueError('max-pages must be 1..5')
    configs={r['source_id']:r for r in rows('collector_config')}
    if set(ids)-configs.keys():raise ValueError('Unknown source ID')
    policy=json.loads((ROOT/'policy.json').read_text())
    if policy['paid_calls_enabled']:raise ValueError('This runner does not implement paid services')
    store=collector.Store(db);result=[]
    try:
        for sid in ids:
            cfg=configs[sid]
            if cfg['manual_only']:result.append({'source_id':sid,'state':'manual_required','coverage_complete':False});continue
            try:result.append(collector.collect(cfg,store,as_of,max_pages))
            except Exception as e:
                row={'run_id':'V7-'+collector.digest(collector.now()+sid)[:20],'source_id':sid,'state':'failed_retained','error':str(e),'coverage_complete':False};store.run(row);result.append(row)
    finally:store.db.close()
    return result

def main():
    ap=argparse.ArgumentParser(description=__doc__);sp=ap.add_subparsers(dest='command',required=True)
    sp.add_parser('validate');p=sp.add_parser('plan');p.add_argument('--limit',type=int,default=20);p.add_argument('--out',required=True)
    p=sp.add_parser('scan');g=p.add_mutually_exclusive_group(required=True);g.add_argument('--source',action='append');g.add_argument('--plan');p.add_argument('--db',default='pilot.sqlite');p.add_argument('--as-of',default=dt.datetime.now(dt.timezone(dt.timedelta(hours=3))).date().isoformat());p.add_argument('--max-pages',type=int,default=2)
    a=ap.parse_args()
    if a.command=='validate':
        r=validate();print(json.dumps(r,ensure_ascii=False,indent=2));return 0 if r['passed'] else 1
    if a.command=='plan':
        r=plan(a.limit);collector.write_jsonl(a.out,r);print(json.dumps({'sources':len(r),'file':a.out,'network_executed':False}));return 0
    ids=a.source or [r['source_id'] for r in collector.read_jsonl(a.plan)]
    print(json.dumps(scan(ids,a.db,a.as_of,a.max_pages),ensure_ascii=False,indent=2));return 0
if __name__=='__main__':sys.exit(main())
