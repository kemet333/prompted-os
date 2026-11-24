import type {LifeData} from './life-data';

export type ActivityKind='strength'|'running'|'other';
export type ActivityRecord={id:string;day:string;date:string;certain:boolean;title:string;status:string;performed:boolean;kind:ActivityKind;source:string;details:string[];truncated:boolean;measures:string[]};
export const activityNames:Record<string,string>={Running:'Carrera',Walking:'Paseo',Cycling:'Ciclismo',TraditionalStrengthTraining:'Fuerza',FunctionalStrengthTraining:'Fuerza funcional',Soccer:'Fútbol',Swimming:'Natación',HighIntensityIntervalTraining:'Intervalos',Other:'Otro entrenamiento'};
export function localDay(value:Date){return [value.getFullYear(),String(value.getMonth()+1).padStart(2,'0'),String(value.getDate()).padStart(2,'0')].join('-');}
export function activityKind(title:string):ActivityKind{
 const t=title.toLocaleLowerCase('es');
 if(/fuerza|pesas|empuje|tracciones|pierna|torso|pecho|espalda|gimnasio|strength/.test(t))return 'strength';
 if(/carrera|running/.test(t))return 'running';
 return 'other';
}
export function activityRecords(data:LifeData|null,source:'diary'|'health'):ActivityRecord[]{
 const records:ActivityRecord[]=source==='health'?(data?.health.data?.workouts||[]).map((w,i)=>({id:'health:'+w.date+':'+i,day:localDay(new Date(w.date)),date:w.date,certain:true,title:activityNames[w.activity]||w.activity||'Entrenamiento',status:'REALIZADO',performed:true,kind:activityKind(w.activity),source:w.source||'Apple Health',details:[],truncated:false,measures:[w.minutes!=null?w.minutes.toLocaleString('es-ES',{maximumFractionDigits:1})+' min':'',w.distanceKm!=null?w.distanceKm.toLocaleString('es-ES',{maximumFractionDigits:2})+' km':'',w.energy!=null?w.energy.toLocaleString('es-ES',{maximumFractionDigits:0})+' kcal':''].filter(Boolean)})):(data?.training.sessions||[]).map(s=>({id:s.id,day:s.date,date:s.date,certain:s.dateCertain,title:s.title,status:s.status,performed:s.status==='REALIZADO',kind:activityKind(s.title),source:'Diario de Alfred',details:s.details,truncated:s.truncated,measures:[]}));
 return records.sort((a,b)=>b.date.localeCompare(a.date));
}
// A blank cell means no documented completed session. Sources are deliberately never added together.
export function activityTimeline(records:ActivityRecord[],weeks:number,today=new Date()){
 const anchor=new Date(today.getFullYear(),today.getMonth(),today.getDate(),12);
 const end=new Date(anchor);end.setDate(end.getDate()+6-(end.getDay()+6)%7);
 const start=new Date(end);start.setDate(start.getDate()-weeks*7+1);
 const now=localDay(anchor),startDay=localDay(start),endDay=localDay(end);
 const valid=records.filter(r=>r.certain&&r.performed&&r.day>=startDay&&r.day<=now);
 const counts=new Map<string,number>();
 for(const r of valid)counts.set(r.day+':'+r.kind,(counts.get(r.day+':'+r.kind)||0)+1);
 const days=Array.from({length:weeks*7},(_,i)=>{const d=new Date(start);d.setDate(start.getDate()+i);const day=localDay(d);return {day,future:day>now,strength:counts.get(day+':strength')||0,running:counts.get(day+':running')||0,other:counts.get(day+':other')||0};});
 return {days,start:startDay,end:endDay,today:now,counts:{strength:valid.filter(r=>r.kind==='strength').length,running:valid.filter(r=>r.kind==='running').length,other:valid.filter(r=>r.kind==='other').length},total:valid.length};
}
