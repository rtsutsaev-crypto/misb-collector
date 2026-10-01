// Pure local helpers. No network, credentials, outreach, or scheduled execution.
export function freshness(date,asOf){
 if(!date)return {bucket:'undated',score:5};
 const d=Date.parse(date+'T00:00:00Z'),a=Date.parse(asOf+'T00:00:00Z');
 if(!Number.isFinite(d)||new Date(d).toISOString().slice(0,10)!==date||d>a)return {bucket:'date_conflict',score:5};
 const age=(a-d)/86400000;
 return age<=13?{bucket:'last_14_days',score:15}:age<=29?{bucket:'days_15_30',score:10}:{bucket:'older',score:3};
}
export function assess(x,asOf='2026-10-01'){
 const f=freshness(x.published_at,asOf);
 const scores={fit:x.fit_score??10,evidence:x.evidence_score??10,freshness:f.score,contact:x.contact_score??0,timing:x.timing_score??5};
 const max={fit:30,evidence:25,freshness:15,contact:20,timing:10};
 for(const k of Object.keys(scores))if(!Number.isFinite(scores[k])||scores[k]<0||scores[k]>max[k])throw new Error('Invalid score: '+k);
 const passed=Boolean(x.event_end&&x.event_end<asOf)||Boolean(x.deadline&&x.deadline<asOf);
 let queue='Уточнить условия / контакт';
 if(x.status==='closed'||passed)queue='История / проверить перенос';
 else if(x.reply_state==='restricted')queue='Проверить ограничение откликов';
 else if(x.fit_score===0)queue='Смежное / ручной разбор';
 else if(x.status==='standing_pool')queue='Развивать партнёрство';
 else if(x.event_end&&Date.parse(x.event_end)-Date.parse(asOf)<=3*86400000)queue='Срочная проверка';
 else if(x.contact_score>=14&&x.organization_verified)queue='Уточнить у организации';
 else if(x.reply_state==='platform_response_visible')queue='Подготовить адресный отклик';
 return {scores,score:Object.values(scores).reduce((a,b)=>a+b,0),queue,freshness:f.bucket,buyer_confirmed:x.buyer_confirmed===true,do_not_drop:true};
}
export function clusterObservations(observations,relations){
 const ids=new Set(observations.map(o=>o.observation_id)),p=new Map([...ids].map(x=>[x,x]));
 const find=x=>{let r=x;while(p.get(r)!==r)r=p.get(r);return r;};
 for(const r of relations){
  if(r.type!=='same_demand_different_publication'||!ids.has(r.from_id)||!ids.has(r.to_id))continue;
  const a=find(r.from_id),b=find(r.to_id);p.set(a,a<b?a:b);p.set(b,a<b?a:b);
 }
 const groups=new Map();for(const o of observations){const k=find(o.observation_id);if(!groups.has(k))groups.set(k,[]);groups.get(k).push(o);}
 return [...groups].map(([first,items])=>({need_id:first.replace('FD-O','FD-N'),observation_ids:items.map(x=>x.observation_id),items}));
}
export function matchIntents(text,lexicon){
 const norm=x=>String(x).normalize('NFKC').toLowerCase().replaceAll('ё','е');
 const t=norm(text);const hits=lexicon.filter(x=>t.includes(norm(x.phrase))).map(x=>x.intent_id);
 return {hits,decision:hits.length?'candidate_for_context_review':'unmatched_keep_for_recall_audit',delete:false};
}
export function budgetDecision(cap,existingSpend,actual,reserved,estimated){
 if(existingSpend===null)return {allowed:false,reason:'existing_spend_unknown',remaining:null};
 if([cap,existingSpend,actual,reserved,estimated].some(x=>!Number.isFinite(x)||x<0))throw new Error('Invalid budget');
 const remaining=cap-existingSpend-actual-reserved;
 return {allowed:estimated<=remaining,reason:estimated<=remaining?'within_cap':'defer_paid_processing',remaining};
}
