const {classifyLead}=require("./classify_code.js");
const d=JSON.parse(require("fs").readFileSync("bidzaar_items.json","utf8"));
const now=new Date("2026-09-29T16:11:00Z");
const res=d.items.map(it=>({...it,cls:classifyLead({title:it.name})}));
const open=res.filter(x=>x.acceptanceEndDate&&new Date(x.acceptanceEndDate)>now);
const by={};open.forEach(x=>by[x.cls.out]=(by[x.cls.out]||0)+1);
console.log("open",open.length,by);
for(const x of open.filter(x=>x.cls.out!=="excluded"))console.log(x.cls.out.padEnd(13),x.acceptanceEndDate.slice(0,10),"|",String(x.companyName).slice(0,28),"|",x.name.slice(0,100),"|",x.cls.why.join("; ").slice(0,80));
console.log("--- excluded open (first 12)");
for(const x of open.filter(x=>x.cls.out==="excluded").slice(0,12))console.log(x.name.slice(0,90),"|",x.cls.why.join("; ").slice(0,60));
// flow by publish month for non-excluded, all history
const m={};res.filter(x=>x.cls.out!=="excluded"&&x.publishDate).forEach(x=>{const k=x.publishDate.slice(0,7);m[k]=(m[k]||0)+1});
console.log("non-excluded by month (all statuses):",JSON.stringify(Object.fromEntries(Object.entries(m).sort().slice(-14))));
const last30=res.filter(x=>x.cls.out!=="excluded"&&x.publishDate&&(now-new Date(x.publishDate))<=30*864e5).length;
const r30=res.filter(x=>x.cls.out==="relevant"&&x.publishDate&&(now-new Date(x.publishDate))<=30*864e5).length;
console.log("published last 30d non-excluded",last30,"relevant only",r30);
