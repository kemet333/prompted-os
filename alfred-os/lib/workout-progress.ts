// What the training diary says about each exercise: the top load of every session, read from the lines Alfred writes
// («- Press inclinado mancuernas: 28, 30 y 30 kg, 3×8»). Only real numbers from the diary: a line without a clear load
// (per side without a total, «peso desconocido») gives nothing, and a figure marked as approximate keeps that mark.
import type {ActivityRecord} from './life-activity';

export type ExerciseEntry={date:string;kg:number;scheme:string;approx:boolean;line:string};
export type ExerciseProgress={id:string;name:string;entries:ExerciseEntry[];last:ExerciseEntry;previous:ExerciseEntry|null;best:ExerciseEntry;record:boolean};

// First match wins: the more specific names go before the general ones.
const EXERCISES:[string,string,RegExp][]=[
 ['press-frances','Press francés',/press franc[eé]s/],
 ['press-inclinado','Press inclinado',/press inclinado|^inclinado\b/],
 ['press-plano','Press plano',/press plano|press (de )?banca/],
 ['press-militar','Press militar',/press militar|^militar\b/],
 ['dominadas','Dominadas',/dominadas/],
 ['rdl','Peso muerto rumano',/peso muerto rumano|\brdl\b/],
 ['predicador','Curl predicador',/predicador/],
 ['curl-banco','Curl bíceps en banco',/curl (b[ií]ceps )?(en )?banco|curl inclinado/],
 ['pullover','Pullover',/pullover/],
 ['remo-divergente','Remo divergente',/remo.*divergente/],   // another machine: its kilos are not the open row's
 ['remo','Remo',/\bremo\b|seated row/],
 ['triceps','Tríceps en polea',/tr[ií]ceps/],
 ['aperturas-inversas','Aperturas inversas',/aperturas inversas|deltoides posterior/],
 ['aperturas','Aperturas de pecho',/aperturas|contractora/],
 ['laterales','Elevaciones laterales',/laterales/],
 ['fondos','Fondos',/\bfondos\b/],
 ['prensa','Prensa de piernas',/\bprensa\b/],
 ['sentadilla','Sentadilla',/sentadilla/],
 ['femoral','Curl femoral',/femoral/],
 ['gemelos','Gemelos',/gemelos/],
 ['crunch','Crunch abdominal',/crunch/],
];
const num=(s:string)=>Number(s.replace(',','.'));

/** One diary line → the exercise and its top load, or null. */
export function exerciseLine(raw:string):Omit<ExerciseEntry,'date'>&{id:string;name:string}|null{
 const line=raw.replace(/\*\*|__/g,'').replace(/^\s*(REALIZADO|PARCIAL|PLANIFICADO)[^:]*:\s*/i,'').trim();
 const lower=line.toLocaleLowerCase('es');
 const head=(lower.includes(':')?lower.slice(0,lower.indexOf(':')):lower.split(/[,;]/)[0]).trim();
 const match=EXERCISES.find(([, ,re])=>re.test(head));
 if(!match)return null;
 const total=lower.match(/(\d+(?:[.,]\d+)?)\s*kg\s+totales/);
 let kg:number|null=total?num(total[1]):null;
 if(kg===null){
  if(/kg\s*(\/|por\s+)lado/.test(lower))return null;          // per side without the bar: not comparable
  // «28, 30 y 30 kg», «100 y 86 kg», «18–20 kg»: every number of the list counts; «3×8–10» are reps, never kilos
  const plain=lower.replace(/\d+\s*[×x]\s*\d+(?:\s*[–-]\s*\d+)?/g,' ').replace(/[×x]\s*\d+(?:\s*[–-]\s*\d+)?/g,' ');
  const loads=[...plain.matchAll(/((?:\d+(?:[.,]\d+)?\s*(?:,|\by\b|–|-|\/)\s*)*\d+(?:[.,]\d+)?)\s*kg/g)]
   .flatMap(m=>m[1].match(/\d+(?:[.,]\d+)?/g)||[]).map(num).filter(n=>Number.isFinite(n)&&n>0&&n<500);
  if(!loads.length)return null;
  kg=Math.max(...loads);
 }
 const scheme=(line.match(/\d+\s*[×x]\s*\d+(?:\s*[–-]\s*\d+)?/)?.[0]||'').replace(/\s+/g,'').replace('x','×');
 return {id:match[0],name:match[1],kg,scheme,approx:/aprox|confirmar|sin confirmar|~|unos\s+\d/.test(lower),line};
}

/** Every exercise with a load in the performed, dated sessions; the most recently trained first. */
export function exerciseProgress(records:ActivityRecord[]):ExerciseProgress[]{
 const by=new Map<string,{name:string;days:Map<string,ExerciseEntry>}>();
 for(const r of records){
  if(!r.performed||!r.certain||r.source!=='Diario de Alfred')continue;
  for(const d of r.details){
   const e=exerciseLine(d);if(!e)continue;
   const ex=by.get(e.id)||{name:e.name,days:new Map()};by.set(e.id,ex);
   const prev=ex.days.get(r.day);
   if(!prev||e.kg>prev.kg)ex.days.set(r.day,{date:r.day,kg:e.kg,scheme:e.scheme,approx:e.approx,line:e.line});
  }
 }
 return [...by.entries()].map(([id,{name,days}])=>{
  const entries=[...days.values()].sort((a,b)=>a.date.localeCompare(b.date));
  const last=entries.at(-1)!,previous=entries.at(-2)||null;
  const best=entries.reduce((a,b)=>b.kg>a.kg?b:a);
  return {id,name,entries,last,previous,best,record:entries.length>1&&best===last&&!!previous&&last.kg>Math.max(...entries.slice(0,-1).map(e=>e.kg))};
 }).sort((a,b)=>b.last.date.localeCompare(a.last.date)||b.entries.length-a.entries.length);
}

// The usual split: a «Torso» day counts for pushing and pulling.
const GROUPS:[string,string,RegExp][]=[
 ['empuje','Empuje',/empuje|torso|pecho/],
 ['tracciones','Tracciones',/tracciones|torso|espalda/],
 ['pierna','Pierna',/pierna/],
];
/** Days since the last performed session of each group, or null when there is none. */
export function daysSinceGroups(records:ActivityRecord[],today:string):{id:string;name:string;days:number|null;date:string|null}[]{
 const day=(s:string)=>Date.UTC(+s.slice(0,4),+s.slice(5,7)-1,+s.slice(8,10));
 return GROUPS.map(([id,name,re])=>{
  const last=records.filter(r=>r.performed&&r.certain&&r.day<=today&&re.test(r.title.toLocaleLowerCase('es'))).sort((a,b)=>b.day.localeCompare(a.day))[0];
  return {id,name,date:last?.day||null,days:last?Math.round((day(today)-day(last.day))/864e5):null};
 });
}
