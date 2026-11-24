// Alfred's greeting for the two-snap ritual (Owner, 13/11/2026: «no debe decir siempre lo mismo, más realista, nivel Jarvis»).
// One spoken line, different every time, built from real facts of the moment: the hour, today's agenda and to-do, the last
// workout. Claude writes the next one ahead of time and its voice is synthesised in advance, so the snap answers at once; if
// nothing is ready (first launch, no plan, the budget brake) a local line is put together on the spot. Nothing is invented.
import fs from 'node:fs';
import path from 'node:path';
import * as hoy from './hoy';
import * as tts from './tts';
import {lifeView} from './life';

export type Facts={hora:string;parte:'madrugada'|'mañana'|'tarde'|'noche';dia:string;fecha:string;agenda:string;tareas:string[];hechas:number;
 entreno:{titulo:string;dias:number}|null;yaHoy:number};
type Ready={text:string;fecha:string;parte:Facts['parte'];hecho:string};
const SONNET='claude-sonnet-5-5';
const FRESH_MS=4*3600_000;
const dir=(d:string)=>path.join(d,'ritual');
const readJSON=(f:string)=>{try{return JSON.parse(fs.readFileSync(f,'utf8'));}catch{return null;}};

export function parteDel(h:number):Facts['parte']{return h<6?'madrugada':h<13?'mañana':h<20?'tarde':'noche';}

/** The last greetings said, newest last: Claude is told not to echo them and the local lines avoid them. */
export function history(dataDir:string,n=12):{text:string;at:string}[]{
 try{return fs.readFileSync(path.join(dir(dataDir),'historial.jsonl'),'utf8').trim().split('\n').slice(-n).map(l=>JSON.parse(l)).filter(x=>typeof x?.text==='string');}catch{return [];}
}
function remember(dataDir:string,text:string,source:string){
 try{fs.mkdirSync(dir(dataDir),{recursive:true});const f=path.join(dir(dataDir),'historial.jsonl');
  fs.appendFileSync(f,JSON.stringify({text,source,at:new Date().toISOString()})+'\n');
  const lines=fs.readFileSync(f,'utf8').trim().split('\n');if(lines.length>200)fs.writeFileSync(f,lines.slice(-100).join('\n')+'\n');}catch{}
}

/** A task as it would be said: no links, no «https», first letter lower-case after «Lo primero de hoy:». */
export const hablado=(t:string)=>t.replace(/https?:\/\/\S+/g,'').replace(/\bhttps?\b\s*/gi,'').replace(/\s+/g,' ').trim().replace(/^./,c=>c.toLocaleLowerCase('es'));

export async function gather(dataDir:string,now=new Date()):Promise<Facts>{
 const fecha=hoy.isoDay(now),day=hoy.load(dataDir,fecha);
 let entreno:Facts['entreno']=null;
 try{
  const v=await lifeView(dataDir);
  const last=v.training.sessions.find(s=>s.status==='REALIZADO'&&s.dateCertain&&s.date<=fecha);
  if(last)entreno={titulo:last.title.replace(/\s*\(.*?\)\s*/g,' ').trim(),dias:Math.round((Date.parse(fecha)-Date.parse(last.date))/864e5)};
 }catch{}
 const yaHoy=history(dataDir,50).filter(h=>h.at&&hoy.isoDay(new Date(h.at))===fecha).length;
 return {hora:now.toLocaleTimeString('es-ES',{hour:'2-digit',minute:'2-digit'}),parte:parteDel(now.getHours()),
  dia:now.toLocaleDateString('es-ES',{weekday:'long',day:'numeric',month:'long'}),fecha,agenda:day?.agenda||'',
  tareas:(day?.items||[]).filter(i=>!i.hecho).map(i=>hablado(i.t)).filter(Boolean).slice(0,3),hechas:(day?.items||[]).filter(i=>i.hecho).length,entreno,yaHoy};
}

// ---- The line put together here, when nothing better is ready ------------------------------------------------------
const pick=<T,>(xs:T[],rnd:()=>number)=>xs[Math.floor(rnd()*xs.length)%xs.length];
const OPEN:Record<Facts['parte'],string[]>={
 madrugada:['Trasnochando otra vez, Owner.','Las horas raras son las mejores, ¿eh?','Sistemas en línea. Es tarde, pero aquí estoy.'],
 mañana:['Buenos días, Owner.','Arriba, señor. Sistemas en línea.','Buenos días. Todo despierto y listo.','Buen día, Owner. Café y a por ello.'],
 tarde:['Buenas tardes, Owner.','De vuelta al mando, señor.','Buenas tardes. Todo sigue en su sitio.','Aquí estoy, Owner. Sistemas en línea.'],
 noche:['Buenas noches, Owner.','Sesión nocturna, ¿eh? Aquí me tienes.','Buenas noches, señor. Todo en orden.','La noche es tuya, Owner.'],
};
const CLOSE=['Cuando quieras.','Tú mandas.','A por ello.','Dime por dónde empezamos.','Listo para lo que haga falta.'];
export function local(f:Facts,said:string[],estilo='',rnd=Math.random):string{
 const facts:string[]=[];
 const next=f.agenda.split(' · ').find(e=>/^\d{2}:\d{2}/.test(e)&&e.slice(0,5)>f.hora);
 if(next)facts.push(`A las ${next.slice(0,5)} tienes ${next.slice(6)}.`);
 if(f.tareas.length)facts.push(f.hechas?`Llevas ${f.hechas} hecha${f.hechas>1?'s':''}; lo siguiente: ${f.tareas[0]}.`:`Lo primero de hoy: ${f.tareas[0]}.`);
 if(f.entreno&&f.entreno.dias>=3)facts.push(`Por cierto, llevas ${f.entreno.dias} días sin entrenar.`);
 else if(f.entreno&&f.entreno.dias===0)facts.push(`Buen trabajo con el ${f.entreno.titulo.toLocaleLowerCase('es')} de hoy.`);
 if(f.yaHoy>0&&f.parte!=='mañana')facts.push('Bienvenido de vuelta.');
 for(let i=0;i<12;i++){
  // His own line keeps its place now and then: it is how he likes to be greeted.
  if(estilo&&rnd()<0.15&&!said.includes(estilo))return estilo;
  const text=[pick(OPEN[f.parte],rnd),facts.length?pick(facts,rnd):'',pick(CLOSE,rnd)].filter(Boolean).join(' ');
  if(!said.includes(text))return text;
 }
 return [pick(OPEN[f.parte],rnd),pick(CLOSE,rnd)].join(' ');
}

// ---- The line Claude writes ahead of time ----------------------------------------------------------------------------
export function prompt(f:Facts,said:string[],estilo:string):string{
 const datos=[`Ahora: ${f.dia}, ${f.hora} (${f.parte}).`,
  f.agenda?`Agenda de hoy: ${f.agenda}.`:'Sin eventos en el calendario hoy.',
  f.tareas.length?`Tareas pendientes de hoy: ${f.tareas.join(' | ')} (hechas: ${f.hechas}).`:f.hechas?`Ha hecho ${f.hechas} tareas de hoy y no le queda ninguna.`:'',
  f.entreno?`Último entreno registrado: ${f.entreno.titulo}, hace ${f.entreno.dias} días.`:'',
  f.yaHoy?`Ya ha arrancado el sistema ${f.yaHoy} ${f.yaHoy===1?'vez':'veces'} hoy.`:'Es la primera vez que arranca el sistema hoy.'].filter(Boolean);
 return [`Eres Alfred, el asistente de Owner, al estilo de JARVIS: elegante, rápido, con un punto de humor seco y complicidad. Owner acaba de arrancar su sistema con dos chasquidos de dedos y vas a saludarle en voz alta.`,
  `Escribe UNA sola intervención hablada, de 10 a 28 palabras, en español de España. Que suene natural dicha en voz alta, como alguien que le conoce de verdad.`,
  `Usa como mucho uno o dos de estos datos reales, el que más importe ahora; no los enumeres todos y no inventes nada que no esté aquí (ni tiempo, ni noticias, ni cifras):`,
  ...datos.map(d=>'- '+d),
  estilo?`Owner eligió este saludo propio; úsalo solo como pista de su tono, sin copiarlo: «${estilo}»`:'',
  said.length?`No repitas el comienzo, la estructura ni las expresiones de los últimos saludos:\n${said.slice(-8).map(s=>'- '+s).join('\n')}`:'',
  `Nada de sermones ni de reproches. Si nombras una tarea, dila como se diría hablando, sin enlaces, «https», siglas raras ni tecnicismos. Sin emojis, comillas, listas ni markdown. Devuelve solo la frase.`].filter(Boolean).join('\n');
}
export function clean(reply:string):string|null{
 const t=reply.replace(/[*_`#>]/g,'').replace(/^["«“']+|["»”']+$/g,'').replace(/\s+/g,' ').trim();
 const words=t.split(' ').length;
 return t&&words>=4&&words<=40&&t.length<=300?t:null;
}

function braked(dataDir:string){const u=readJSON(path.join(dataDir,'uso.json'));return (u?.fiveHour??0)>=0.85||(u?.sevenDay??0)>=0.85||u?.status==='rejected';}

async function ask(text:string):Promise<string>{
 const {query}=await import('@anthropic-ai/claude-agent-sdk');
 const q=query({prompt:text,options:{model:SONNET,maxTurns:1,allowedTools:[],settingSources:[],strictMcpConfig:true,
  canUseTool:async()=>({behavior:'deny',message:'Sin herramientas.'}),systemPrompt:'Respondes solo con la frase pedida.',stderr:()=>{}}});
 let out='';const timer=setTimeout(()=>{try{q.close();}catch{}},45_000);
 try{for await(const m of q){if(m.type==='result'&&m.subtype==='success')out=String(m.result||'');}}finally{clearTimeout(timer);}
 return out;
}

let preparing:Promise<void>|null=null;
/** Writes the next greeting (and its voice) ahead of time. Cheap: one short Sonnet reply, skipped under the budget brake. */
export function prepare(dataDir:string,estilo=''):Promise<void>{
 if(preparing)return preparing;
 preparing=(async()=>{
  if(braked(dataDir)||process.env.PROMPTED_OS_DISABLE_RITUAL_PREP==='1')return;
  const f=await gather(dataDir),said=history(dataDir).map(h=>h.text);
  const text=clean(await ask(prompt(f,said,estilo)));
  if(!text||said.includes(text))return;
  fs.mkdirSync(dir(dataDir),{recursive:true});
  try{await tts.synthesize(text,AbortSignal.timeout(20_000),true);}catch{}   // the voice waits on disk: the snap answers at once
  fs.writeFileSync(path.join(dir(dataDir),'siguiente.json'),JSON.stringify({text,fecha:f.fecha,parte:f.parte,hecho:new Date().toISOString()} satisfies Ready));
 })().catch(e=>{tts.note('ritual: no se pudo preparar el saludo · '+String(e?.message||e).slice(0,120));}).finally(()=>{preparing=null;});
 return preparing;
}

function usable(r:Ready|null,f:Facts,now:number):r is Ready{return !!r&&typeof r.text==='string'&&r.fecha===f.fecha&&r.parte===f.parte&&now-Date.parse(r.hecho)<FRESH_MS;}

/** The greeting to say now: the prepared one if it still fits the moment, else a local line. Then the next is prepared. */
export async function greeting(dataDir:string,estilo='',now=new Date()):Promise<{text:string;source:'claude'|'local'}>{
 if(estilo)try{fs.mkdirSync(dir(dataDir),{recursive:true});fs.writeFileSync(path.join(dir(dataDir),'estilo.txt'),estilo.slice(0,400));}catch{}
 const f=await gather(dataDir,now),file=path.join(dir(dataDir),'siguiente.json'),ready=readJSON(file);
 const said=history(dataDir).map(h=>h.text);
 let out:{text:string;source:'claude'|'local'};
 if(usable(ready,f,now.getTime())&&!said.includes(ready.text)){out={text:ready.text,source:'claude'};try{fs.rmSync(file);}catch{}}
 else out={text:local(f,said,estilo),source:'local'};
 remember(dataDir,out.text,out.source);
 setTimeout(()=>void prepare(dataDir,estilo),15_000).unref?.();   // after the greeting has been spoken
 return out;
}

/** Keeps a greeting ready while the app is open: on start and whenever the prepared one no longer fits the hour. */
export function keepReady(dataDir:string){
 const estilo=()=>{try{return fs.readFileSync(path.join(dir(dataDir),'estilo.txt'),'utf8').trim();}catch{return '';}};
 const check=async()=>{try{const f=await gather(dataDir);if(!usable(readJSON(path.join(dir(dataDir),'siguiente.json')),f,Date.now()))await prepare(dataDir,estilo());}catch{}};
 setTimeout(check,60_000).unref?.();
 setInterval(check,30*60_000).unref?.();
}
