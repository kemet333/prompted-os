// Local-only Life dashboard contract. No health records are added to model context.
export type WorkoutEntry={id:string;date:string;dateCertain:boolean;title:string;status:'REALIZADO'|'PARCIAL'|'PLANIFICADO'|'NO CONFIRMADO';details:string[];truncated:boolean};
export type HealthMetric={id:string;value:number;unit:string;date:string;source:string};
export type HealthDay={date:string;energy?:number;energyGoal?:number;exercise?:number;exerciseGoal?:number;stand?:number;standGoal?:number};
export type HealthWorkout={date:string;end:string;activity:string;minutes?:number;distanceKm?:number;energy?:number;source:string};
export type HealthImport={schema:1;exportedAt:string|null;importedAt:string;days:HealthDay[];metrics:HealthMetric[];workouts:HealthWorkout[];duplicates:number;shortcut?:{signature:string;lastDay:string|null;at:string}};
export type LifeData={local:true;training:{status:'ready'|'missing'|'unconfigured'|'error';note:string|null;updatedAt:string|null;sessions:WorkoutEntry[]};health:{status:'ready'|'missing'|'error';data:HealthImport|null};capabilities:{healthImport:boolean}};

const object=(v:unknown):v is Record<string,unknown>=>typeof v==='object'&&v!==null&&!Array.isArray(v);
const number=(v:unknown)=>typeof v==='number'&&Number.isFinite(v)&&v>=0;
const shortText=(v:unknown)=>typeof v==='string'&&v.length<=100;
const day=(v:unknown)=>{if(typeof v!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(v))return false;const d=new Date(v+'T12:00:00Z');return Number.isFinite(+d)&&d.toISOString().slice(0,10)===v;};
const stamp=(v:unknown)=>typeof v==='string'&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/.test(v)&&day(v.slice(0,10))&&Number.isFinite(Date.parse(v));
const optionalNumbers=(v:Record<string,unknown>,keys:string[])=>keys.every(k=>v[k]===undefined||number(v[k]));
// Shared guard for disk, native-helper output and UI reads: a corrupt cache is an error state, never a partial dashboard.
export function validHealthSummary(v:unknown,imported=true):v is HealthImport{
 if(!object(v)||v.schema!==1||!(v.exportedAt===null||stamp(v.exportedAt))||(imported&&!stamp(v.importedAt))||!Number.isInteger(v.duplicates)||(v.duplicates as number)<0)return false;
 if(!Array.isArray(v.days)||v.days.length>366||!Array.isArray(v.metrics)||v.metrics.length>3||!Array.isArray(v.workouts)||v.workouts.length>1000)return false;
 if(!v.days.every(d=>object(d)&&day(d.date)&&optionalNumbers(d,['energy','energyGoal','exercise','exerciseGoal','stand','standGoal'])))return false;
 const units:Record<string,string>={restingHeartRate:'lpm',hrv:'ms',weight:'kg'};
 if(!v.metrics.every(m=>object(m)&&typeof m.id==='string'&&Object.hasOwn(units,m.id)&&units[m.id]===m.unit&&number(m.value)&&stamp(m.date)&&shortText(m.source)))return false;
 if(new Set(v.metrics.map(m=>m.id)).size!==v.metrics.length)return false;
 return v.workouts.every(w=>object(w)&&stamp(w.date)&&stamp(w.end)&&Date.parse(w.end as string)>=Date.parse(w.date as string)&&shortText(w.activity)&&shortText(w.source)&&optionalNumbers(w,['minutes','distanceKm','energy']));
}
