// What the iPhone's «Alfred · Salud» shortcut leaves in iCloud Drive: one small JSON per day. The Shortcuts app writes
// what it has (numbers, «520,3 kcal», «62 lpm», «1.234 kcal»), so every value is read leniently and checked against a
// plausible range: a value that cannot be read, or is impossible, is left out, never guessed.
import type {HealthDay,HealthImport,HealthMetric} from './life-data';

export const SHORTCUT_SOURCE='Atajo del iPhone';
type Field='energy'|'exercise'|'stand'|'restingHeartRate'|'hrv'|'weight';
// Spanish keys first (what the note tells Owner to type), English as a courtesy.
const KEYS:Record<Field,string[]>={
 energy:['energia','energía','energiaActiva','energía activa','activeEnergy','energy'],
 exercise:['ejercicio','minutosEjercicio','minutos de ejercicio','exercise'],
 stand:['dePie','de pie','horasDePie','stand'],
 restingHeartRate:['fcReposo','fc en reposo','frecuenciaReposo','restingHeartRate'],
 hrv:['vfc','variabilidad','hrv'],
 weight:['peso','weight'],
};
const RANGE:Record<Field,[number,number]>={energy:[0,10000],exercise:[0,1440],stand:[0,24],restingHeartRate:[25,150],hrv:[1,300],weight:[25,300]};
// Counts are whole numbers: there «1.234» is a thousand separator; in weights and heart figures a dot is a decimal point.
const COUNTS=new Set<Field>(['energy','exercise','stand']);
const norm=(k:string)=>k.normalize('NFD').replace(/[̀-ͯ]/g,'').replace(/[^a-z]/gi,'').toLowerCase();

/** «1.234,5 kcal», «520,3», «62 lpm», 71.4 → a number, or null. */
export function lenientNumber(v:unknown,field:Field):number|null{
 let n:number;
 if(typeof v==='number')n=v;
 else if(typeof v==='string'){
  // A Shortcuts list may arrive as lines or with a unit; only the first figure is the value.
  const m=v.replace(/ /g,' ').match(/-?\d[\d.,]*/);if(!m)return null;
  let s=m[0].replace(/[.,]$/,'');
  const dot=s.lastIndexOf('.'),comma=s.lastIndexOf(',');
  if(dot>=0&&comma>=0)s=dot>comma?s.replace(/,/g,''):s.replace(/\./g,'').replace(',','.');
  else if(comma>=0)s=/^\d{1,3}(,\d{3})+$/.test(s)&&COUNTS.has(field)?s.replace(/,/g,''):s.replace(',','.');
  else if(dot>=0&&COUNTS.has(field)&&/^\d{1,3}(\.\d{3})+$/.test(s))s=s.replace(/\./g,'');
  n=Number(s);
 }else return null;
 const [lo,hi]=RANGE[field];
 return Number.isFinite(n)&&n>=lo&&n<=hi?Math.round(n*10)/10:null;
}

const realDay=(s:string)=>{const d=new Date(s+'T12:00:00Z');return Number.isFinite(+d)&&d.toISOString().slice(0,10)===s;};
/** «2026-11-11», «2026-11-11T23:30:00+01:00», «11/11/2026», «11-11-26» → «2026-11-11», or null. */
export function lenientDay(v:unknown):string|null{
 if(typeof v!=='string')return null;
 const s=v.trim();let m=s.match(/^(\d{4})-(\d{2})-(\d{2})/);
 if(m)return realDay(m[0])?m[0]:null;
 m=s.match(/^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})\b/);   // Spain: day first
 if(!m)return null;
 const y=m[3].length===2?'20'+m[3]:m[3],d=`${y}-${m[2].padStart(2,'0')}-${m[1].padStart(2,'0')}`;
 return realDay(d)?d:null;
}

export type ShortcutDay={date:string;day:HealthDay;metrics:HealthMetric[]};
/** One file of the shortcut → its day, or null when it has no date or no value at all. */
export function shortcutDay(raw:unknown,fileName=''):ShortcutDay|null{
 if(typeof raw!=='object'||raw===null||Array.isArray(raw))return null;
 const by=new Map(Object.entries(raw as Record<string,unknown>).map(([k,v])=>[norm(k),v]));
 const get=(f:Field)=>{for(const k of KEYS[f]){const v=by.get(norm(k));if(v!==undefined)return lenientNumber(v,f);}return null;};
 const date=lenientDay(by.get('fecha')??by.get('dia')??by.get('date'))??lenientDay(fileName.match(/\d{4}-\d{2}-\d{2}/)?.[0]);
 if(!date)return null;
 const day:HealthDay={date};
 for(const f of ['energy','exercise','stand'] as const){const v=get(f);if(v!==null)day[f]=v;}
 const units={restingHeartRate:'lpm',hrv:'ms',weight:'kg'} as const;
 const metrics:HealthMetric[]=[];
 for(const id of ['restingHeartRate','hrv','weight'] as const){const v=get(id);if(v!==null)metrics.push({id,value:v,unit:units[id],date:date+'T12:00:00Z',source:SHORTCUT_SOURCE});}
 return Object.keys(day).length>1||metrics.length?{date,day,metrics}:null;
}

/** The saved summary with the shortcut's days on top: a day's values replace that day's, each metric keeps its newest reading. */
export function mergeShortcut(base:HealthImport|null,days:ShortcutDay[],now:string,signature:string):HealthImport&{shortcut:{signature:string;lastDay:string|null;at:string}}{
 const out:HealthImport=base?{...base,days:[...base.days],metrics:[...base.metrics]}:{schema:1,exportedAt:null,importedAt:now,days:[],metrics:[],workouts:[],duplicates:0};
 const byDate=new Map(out.days.map(d=>[d.date,d]));
 for(const s of days)byDate.set(s.date,{...byDate.get(s.date),...s.day});
 const metrics=new Map(out.metrics.map(m=>[m.id,m]));
 for(const s of days)for(const m of s.metrics){const old=metrics.get(m.id);if(!old||Date.parse(m.date)>=Date.parse(old.date))metrics.set(m.id,m);}
 const sorted=[...byDate.values()].sort((a,b)=>a.date.localeCompare(b.date)).slice(-366);
 const lastDay=days.map(d=>d.date).sort().at(-1)||null;
 return {...out,days:sorted,metrics:[...metrics.values()],shortcut:{signature,lastDay,at:now}};
}
