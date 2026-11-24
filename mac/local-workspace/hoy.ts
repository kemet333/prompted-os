// Today's to-do: at most five short lines, written by Alfred every morning from what is pending (the team's decisions,
// what was left undone yesterday, the person's goals and routine) and sent once on Telegram with a button per line.
// Readable in a second: one verb-first line per task, and under it one plain sentence saying exactly what to do, where and
// how long it takes (Owner 29/10: «los recordatorios tienen que ser cosas que entienda a la perfección»). The sentence goes to
// the reminder's notes on the iPhone too. Alfred adds or ticks items from a conversation with
// the directives <<hoy:+texto>> and <<hoy:hecho:N>> (N = the item's number), which never reach the person.
// Pure functions and small file helpers only, so telegram-checks can exercise them without Telegram or Claude.
import fs from 'node:fs';
import path from 'node:path';

export type Item={t:string;hecho:boolean;rid?:string;n?:string;de?:'owner'};   // rid: its reminder in Apple Reminders; n: what exactly to do; de: added by the owner in Reminders (never removed)
export type Day={fecha:string;items:Item[];mensaje?:number;enviado?:string;agenda?:string};
export const MAX=5;
export const HOUR=9;          // the morning message; until LAST it is still sent if the Mac was off at nine
export const LAST=21;    // a to-do at seven in the evening is still better than none (18/10: the app opened at 14:05 and no list came)

export const isoDay=(d:Date)=>{const p=(n:number)=>String(n).padStart(2,'0');return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}`;};
const fileOf=(dataDir:string,fecha:string)=>path.join(dataDir,'hoy',fecha+'.json');
export function load(dataDir:string,fecha:string):Day|null{
 try{const d=JSON.parse(fs.readFileSync(fileOf(dataDir,fecha),'utf8'));return d&&Array.isArray(d.items)?d:null;}catch{return null;}
}
/** One line per morning in hoy/registro.log: whether Alfred wrote the list or the fallback was used, and why. */
export function note(dataDir:string,line:string){try{fs.mkdirSync(path.join(dataDir,'hoy'),{recursive:true});fs.appendFileSync(path.join(dataDir,'hoy','registro.log'),new Date().toISOString()+' '+line+'\n');}catch{}}
export function save(dataDir:string,day:Day){fs.mkdirSync(path.join(dataDir,'hoy'),{recursive:true});fs.writeFileSync(fileOf(dataDir,day.fecha),JSON.stringify(day,null,1));}

const tidy=(s:string)=>s.replace(/\*\*|__|`/g,'').replace(/\s+/g,' ').trim().replace(/[.;]+$/,'');
/** Alfred's reply → the list: lines that start with a dash, bullet or number; short, unique, five at most.
 *  «- Título :: qué hacer, dónde y cuánto tardas» gives each task its plain explanation. */
export function parseItems(reply:string):{t:string;n:string}[]{
 const out:{t:string;n:string}[]=[];
 // one task per line; a reply squeezed into one line («- a - b» or «a | b») is split too
 const parts=reply.includes('\n')?[]:reply.split(/\s+\|\s+|\s+-\s+(?=[A-ZÁÉÍÓÚÑ¿])/);
 const rows=parts.length>1?parts.map(r=>'- '+r.replace(/^\s*[-•*]\s+/,'')):reply.split('\n');
 for(const raw of rows){
  const m=raw.match(/^\s*(?:[-•*]|\d+[.)])\s+(.+)$/);if(!m)continue;
  const [title,...rest]=m[1].split('::');
  let t=tidy(title);if(!t)continue;if(t.length>70)t=t.slice(0,69).trimEnd()+'…';
  let n=tidy(rest.join('::'));if(n.length>240)n=n.slice(0,239).trimEnd()+'…';
  if(!out.some(o=>o.t.toLowerCase()===t.toLowerCase()))out.push({t,n});
  if(out.length>=MAX)break;
 }
 return out;
}
export const parse=(reply:string):string[]=>parseItems(reply).map(i=>i.t);

const DAYS=['dom','lun','mar','mié','jue','vie','sáb'];
/** The message: a header and one line per task, a tick on the ones done. */
export function render(day:Day):string{
 const d=new Date(day.fecha+'T12:00:00'),left=day.items.filter(i=>!i.hecho).length;
 const head=`Hoy, ${DAYS[d.getDay()]} ${d.getDate()}`+(day.items.length&&!left?' · todo hecho':'');
 return [head,...(day.agenda?['📅 '+day.agenda]:[]),...day.items.flatMap(i=>[(i.hecho?'✓ ':'○ ')+i.t,...(i.n&&!i.hecho?['   '+i.n]:[])])].join('\n');
}
/** One button per task still open; tapping it ticks the task. */
export function keyboard(day:Day){
 return {inline_keyboard:day.items.map((it,i)=>it.hecho?null:[{text:'✓ '+(it.t.length>34?it.t.slice(0,33)+'…':it.t),callback_data:'h:'+day.fecha+':'+i}]).filter(Boolean)};
}
export function tick(day:Day,i:number){if(day.items[i])day.items[i].hecho=true;return day;}

/** What Alfred needs to write today's list. Yesterday's undone items come first so nothing silently drops. */
export function prompt(ctx:{yesterday:Day|null;decisions:string[];agenda?:string;detalles?:string[]}):string{
 const undone=(ctx.yesterday?.items||[]).filter(i=>!i.hecho).map(i=>i.t);
 const done=(ctx.yesterday?.items||[]).filter(i=>i.hecho).map(i=>i.t);
 return ['[Tarea programada de la mañana, no es un mensaje suyo. Escribe su to-do de hoy.',
  `Como mucho ${MAX} tareas, una por línea, con este formato exacto: "- Título :: explicación".`,
  'El título empieza por un verbo y tiene menos de 60 caracteres. La explicación es una frase llana que dice exactamente qué hacer, dónde (qué app, web o sitio) y cuánto tarda, para que la entienda a la primera sin contexto: nada de jerga, siglas ni nombres internos del equipo («lote», «experimento», «fusión», «t_…»).',
  'Si no puedes explicar una tarea así de claro, no la pongas.',
  'Elige lo que más le acerca a sus objetivos hoy y lo que solo puede hacer él. Ten en cuenta su rutina y sus objetivos (están en tus instrucciones y en su vault).',
  ctx.agenda?'Hoy en su calendario: '+ctx.agenda+'. Planea alrededor de eso (no lo repitas como tarea).':'',
  ctx.decisions.length?'Decisiones del equipo que le esperan: '+ctx.decisions.join('; ')+'.':'El equipo no le espera para nada.',
  ctx.detalles?.length?'Qué es cada una y qué le toca hacer: '+ctx.detalles.join(' | ')+'.':'',
  undone.length?'De ayer quedó sin hacer: '+undone.join('; ')+'. Si sigue importando, mantenlo.':'',
  done.length?'Ayer hizo: '+done.join('; ')+'.':'',
  'Responde solo con la lista: sin saludo, sin explicaciones, sin nada más.]'].filter(Boolean).join('\n');
}

// 15/11, 03:33: «recuérdame que mañana tengo que aceptar a los usuarios del indicador». Alfred had no way to keep it for another
// day and said it lived only while the session stayed open. <<hoy:AAAA-MM-DD+Título :: …>> keeps it in hoy/futuro.json, and that
// morning's list (app or team service) takes it first, whatever Alfred writes then.
export type Later={fecha:string;t:string;n?:string};
const laterFile=(dataDir:string)=>path.join(dataDir,'hoy','futuro.json');
export function loadLater(dataDir:string):Later[]{try{const l=JSON.parse(fs.readFileSync(laterFile(dataDir),'utf8'));return Array.isArray(l)?l:[];}catch{return [];}}
function saveLater(dataDir:string,l:Later[]){fs.mkdirSync(path.join(dataDir,'hoy'),{recursive:true});const f=laterFile(dataDir);fs.writeFileSync(f+'.tmp',JSON.stringify(l,null,1));fs.renameSync(f+'.tmp',f);}
export function addLater(dataDir:string,x:Later){const l=loadLater(dataDir);if(!l.some(o=>o.fecha===x.fecha&&o.t.toLowerCase()===x.t.toLowerCase())){l.push(x);saveLater(dataDir,l);}}
/** What was kept for this day (or an earlier one that never got its list): returned and removed. */
export function takeLater(dataDir:string,fecha:string):Later[]{const l=loadLater(dataDir),due=l.filter(x=>x.fecha<=fecha);if(due.length)saveLater(dataDir,l.filter(x=>x.fecha>fecha));return due;}
/** The kept items in front of the morning's lines, without repeating one. */
export function withLater<T extends {t:string}>(lines:T[],later:Later[],make:(x:Later)=>T):T[]{
 return [...later.filter(x=>!lines.some(l=>l.t.toLowerCase()===x.t.toLowerCase())).map(make),...lines];
}

/** <<hoy:+texto>> adds a task, <<hoy:hecho:N>> ticks task N (1-based), <<hoy:AAAA-MM-DD+texto>> keeps it for that day (with
 *  dataDir). Returns the reply without the directives. */
export function applyDirectives(reply:string,day:Day,dataDir?:string):{text:string;changed:boolean}{
 let changed=false;
 const text=reply.replace(/<<hoy:([^>]{1,320})>>/g,(_,body:string)=>{
  let b=body.trim();
  const dated=b.match(/^(\d{4}-\d{2}-\d{2})\s*\+/);
  if(dated){
   b=b.slice(dated[0].length-1);
   if(dated[1]>day.fecha&&dataDir){const [ti,...re]=b.slice(1).split('::'),t=tidy(ti).slice(0,70),n=tidy(re.join('::')).slice(0,240);if(t)addLater(dataDir,{fecha:dated[1],t,...(n?{n}:{})});return '';}
  }
  if(b.startsWith('+')){const [ti,...re]=b.slice(1).split('::'),t=tidy(ti),n=tidy(re.join('::')).slice(0,240);
   if(t&&day.items.length<MAX+3&&!day.items.some(i=>i.t.toLowerCase()===t.toLowerCase())){day.items.push({t:t.slice(0,70),hecho:false,...(n?{n}:{})});changed=true;}}
  else{const n=Number(b.match(/^hecho:\s*(\d+)$/)?.[1]);if(n>=1&&day.items[n-1]&&!day.items[n-1].hecho){day.items[n-1].hecho=true;changed=true;}}
  return '';
 }).replace(/[ \t]{2,}/g,' ').replace(/[ \t]+\n/g,'\n').replace(/\n{3,}/g,'\n\n').trim();
 return {text,changed};
}
/** The list as context for a conversation, so «¿qué me queda?» or «ya lo he hecho» can be answered and acted on. */
export function context(day:Day|null,now=new Date()):string{
 const list=!day||!day.items.length?'Hoy todavía no tiene to-do.':'Su to-do de hoy: '+day.items.map((i,n)=>`${n+1}. ${i.t}${i.hecho?' (hecha)':''}`).join(' · ')+'. Para tachar la número N, <<hoy:hecho:N>>.';
 return list+' '+LATER_HELP(now);
}
const DIAS=['domingo','lunes','martes','miércoles','jueves','viernes','sábado'];
/** How Alfred adds a task today or keeps one for another day: shared by the app chat, Telegram and WhatsApp. */
export const LATER_HELP=(now=new Date())=>`Hoy es ${DIAS[now.getDay()]} ${isoDay(now)}. Para añadir una tarea hoy escribe <<hoy:+Título :: qué hacer, dónde y cuánto tarda>>; si te pide que se lo recuerdes otro día («mañana», «el jueves»), <<hoy:AAAA-MM-DD+Título :: …>> con la fecha de ese día: entra sola en su to-do de esa mañana y en Recordatorios del iPhone, aunque la app esté cerrada. No digas que un recordatorio depende de que la sesión siga abierta.`;
