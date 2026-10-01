"""Offline qualification and measurement; no network calls and no automatic outreach."""
import argparse, collections, datetime as dt, hashlib, json, pathlib
def date(value):
    try: return dt.date.fromisoformat(value) if value else None
    except (TypeError,ValueError): return None
def qualify(row, as_of):
    r=dict(row); today=date(as_of)
    if not today: raise ValueError('as_of must be YYYY-MM-DD')
    end,deadline=date(r.get('event_end')),date(r.get('deadline'))
    r['effective_last_date']=min([d for d in [end,deadline] if d],default=None)
    if r['effective_last_date']: r['effective_last_date']=r['effective_last_date'].isoformat()
    if end and deadline and deadline>end: r['timing']='date_conflict_urgent'
    if end and end<today: r['timing']='past_event'
    elif deadline and deadline<today and not end: r['timing']='past_deadline'
    if r.get('status') in ['likely_completed_linkage_inferred','historically_closed'] or r['timing'] in ['past_event','past_deadline']:
        queue,action='repeat_cycle','Уточнить следующий цикл; сохранить историю и контакт организатора.'
    elif r['evidence'] in ['reader_error','generic_page']:
        queue,action='restore_evidence','Восстановить исходную карточку или подтвердить потребность через официальный маршрут.'
    elif r['misb_fit'] in ['noncore_consulting','mixed_scope','uncertain']:
        queue,action='scope_review','Уточнить обучающую часть, аудиторию и возможность исполнения.'
    elif r['timing'] in ['date_conflict_urgent','event_in_progress']:
        queue,action='urgent_status_check','Сначала подтвердить незакрытый слот и возможность исполнения.'
    elif r['buying_intent'] in ['standing_partner_invitation','subcontract_signal']:
        queue,action='partnership','Проверить роль посредника, условия включения в пул и коммерческую модель.'
    elif r['buying_intent'] in ['inferred_need','reference']:
        queue,action='signal_research','Проверить связь события с обучением; сформулировать гипотезу, не приписывать бюджет.'
    elif r['evidence']=='legacy_not_rechecked':
        queue,action='recheck_legacy','Перечитать первоисточник, дату и текущий статус.'
    elif r['reachability']=='platform_restricted':
        queue,action='resolve_access','Найти разрешенный маршрут к организатору и проверить условия доступа.'
    else: queue,action='qualify_request','Подтвердить задачу, покупателя, сроки, бюджет и требования к провайдеру.'
    r['queue']=queue;r['next_action']=r.get('next_action_override') or action
    r['proposal_ready']=False  # Human delivery/open-status checks are mandatory; this is a research record.
    r['qualified_as_of']=as_of
    return r
def unit_cost(cost, qualified):
    return None if cost is None or qualified is None or qualified<=0 else cost/qualified
def read(path): return [json.loads(s) for s in path.read_text().splitlines() if s.strip()]
def write(path,rows): path.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in rows))
def measure(root):
    feedback=read(root/'data/feedback.jsonl');opps={x['opportunity_id']:x for x in read(root/'data/opportunities.jsonl')}
    seen=set();global_qualified=set(); by=collections.defaultdict(set)
    for f in feedback:
        oid=f['opportunity_id']
        if oid not in opps: raise ValueError('unknown opportunity '+oid)
        if oid in seen: raise ValueError('duplicate feedback ID '+oid)
        seen.add(oid)
        if f.get('qualified') is True:
            if not all(f.get(k) is True for k in ['topic_fit','delivery_ready','request_open','buyer_reachable','evidence_sufficient']): raise ValueError('qualification lacks checks '+oid)
            global_qualified.add(oid)
            for sid in opps[oid]['source_ids']: by[sid].add(oid)
    return dict(reviewed=sum(f['stage']!='unreviewed' for f in feedback),unique_qualified=len(global_qualified),qualified_by_source={k:len(v) for k,v in by.items()},source_attribution='multi-touch; do not sum source totals into global total',population='retained 191 opportunities; new pilot records require append + ID validation',result_type='feedback_measurement_not_14d_yield')
def validate(root):
    opps=read(root/'data/opportunities.jsonl'); ids=[r['opportunity_id'] for r in opps]
    old={r['original']['need_id'] for r in read(root/'v5/data/needs_snapshot.jsonl')}
    assert len(ids)==len(set(ids)) and old.issubset(set(ids)),'lost or duplicate need IDs'
    bids={r['buyer_id'] for r in read(root/'data/buyer_resolution.jsonl')}
    assert all(r['buyer_id'] in bids for r in opps),'orphan buyer'
    assert all(r['do_not_drop'] for r in opps)
    assert all(r['budget_kind']!='market_average_price' for r in opps)
    config=json.loads((root/'pilot_config.json').read_text()); assert sum(config['allocations_rub'].values())<=config['monthly_budget_rub']
    assert config['exploration_share']>=0.2
    errors=[]
    manifest=root/'audit/manifest.json'
    if manifest.exists():
        for x in json.loads(manifest.read_text()):
            p=root/x['path']
            if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest()!=x['sha256']:errors.append(x['path'])
    assert not errors,'manifest mismatch: '+str(errors[:5])
    return dict(valid=True,opportunities=len(ids),buyers=len(bids),manifest_checked=manifest.exists())
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['rebuild','validate','measure']);p.add_argument('--root',type=pathlib.Path,default=pathlib.Path(__file__).resolve().parents[1]);p.add_argument('--as-of',default='2026-10-01');a=p.parse_args()
    if a.command=='rebuild':
        rows=[qualify(r,a.as_of) for r in read(a.root/'data/opportunities_input.jsonl')];write(a.root/'data/opportunities.jsonl',rows);print(json.dumps(dict(opportunities=len(rows),queues=dict(collections.Counter(r['queue'] for r in rows))),ensure_ascii=False))
    elif a.command=='measure': print(json.dumps(measure(a.root),ensure_ascii=False,indent=2))
    else: print(json.dumps(validate(a.root),ensure_ascii=False))
