// Alfred's voice brain: one warm Claude session (Agent SDK) for the whole app run.
// Replies stream sentence by sentence so the Mac starts speaking while the rest of
// the thought is still forming. Anything that acts on the machine asks the owner first.
import {ruta,rutas,texto} from './config';
import {appendFileSync,existsSync,mkdirSync,readFileSync,realpathSync,renameSync,writeFileSync} from 'node:fs';
import {homedir} from 'node:os';
import {dirname,join,resolve as resolvePath,sep} from 'node:path';
import {projects,divisions} from '../../alfred-os/lib/alfred-data';
import {note} from './tts';
import * as profile from './profile';
import {proyectosEquipo} from './configurar';
import {sesionesExternas} from './sesiones';

export type VoiceEvent =
 |{t:'state';v:'thinking'|'tool'}
 |{t:'step';text:string}
 |{t:'model';model:string;deep:boolean}
 |{t:'hilo';id:string;nombre:string}
 |{t:'say';text:string;directions:string[];raw:string}   // raw: Alfred's text as written (Telegram), text: cleaned for the voice
 |{t:'permission';id:string;what:string;detail:string}
 |{t:'permission-closed';id:string;allowed:boolean}
 |{t:'usage';usage:Usage}
 |{t:'done';text:string}
 |{t:'error';message:string};
export type Usage={status:string;fiveHour?:number;sevenDay?:number;resetsAt?:number};
type Emit=(e:VoiceEvent)=>void;
export type Turn={id:string;emit:Emit;buffer:string;spoken:string;done:(text:string)=>void;urls:string[]};
type Pending={resolve:(allow:boolean,reason?:string)=>void;timer:ReturnType<typeof setTimeout>};

// Where Alfred works and what else it may read (configuracion.json → alfred); by default the app's data folder only.
const AGENT_HOME=ruta('alfred.carpeta','PROMPTED_OS_ALFRED_HOME',process.env.PROMPTED_OS_DATA_DIR||homedir());
const READABLE=rutas('alfred.puedeLeer');
const SONNET='claude-sonnet-5-5',OPUS='claude-opus-5-5';
// Same ledger as the team engine, so one file shows what every model cost that day.
const LEDGER=ruta('equipo.consumo','PROMPTED_OS_CONSUMO_DIR',join(process.env.PROMPTED_OS_DATA_DIR||homedir(),'consumo'));
// Most of what the owner says out loud is quick and Sonnet answers it well. Thinking work goes to Opus.
const HEAVY=/\b(analiz|revis|audit|planific|estrategi|compar|disen|arquitectur|codigo|program|depur|investig|por que|evalu|prioriz|redact|propuesta|decid|decision|piensa|razona|a fondo|en detalle)/;
type Pick={model:string;effort:'low'|'high'};
const ANSWER_WINDOW_MS=Number(process.env.PROMPTED_OS_ANSWER_WINDOW_MS)||75_000,NO_ANSWER='sin respuesta a tiempo';
// Reading never needs a spoken yes; anything that writes, runs or reaches out still does.
const READ_ONLY=['Read','Glob','Grep','WebSearch','TodoWrite','ListMcpResourcesTool','ReadMcpResourceTool',...['ls','cat','head','tail','wc','find','grep','rg','git log','git status','git diff','git show','sqlite3 -readonly'].map(c=>`Bash(${c}:*)`)];

// The owner's connectors (Gmail, Calendar, Drive… the same claude.ai connectors Claude Code has on this Mac). Looking things
// up in them needs no yes, like reading a file; sending, creating, changing, publishing or deleting always does.
export const readOnlyMcp=(tool:string)=>/^mcp__.+__(search|get|list|read|query|describe|fetch|find|lookup|show|count|suggest)([_-][a-z0-9_-]*)?$/i.test(tool)&&!/(^|[_-])(send|create|update|delete|post|publish|cancel|trash|upload|share|set|write|remove|move|copy|import|label|mark|reply|forward|respond|bulk)([_-]|$)/i.test(tool.split('__').pop()||'');
const mcpServer=(tool:string)=>(tool.split('__')[1]||'').replace(/^claude_ai_/i,'').replace(/_/g,' ').trim()||'una conexión';

// 20/11/2026, Owner: «acabo usando Claude o Codex en vez del chat… es un lío de conversaciones». One thread per project (plus
// Personal), each working inside its repository (its CLAUDE.md) and resumed across restarts; Alfred moves to the thread of the
// project the owner names, and stays there until he names another. Hilos/<id> keep the Claude session id (Workspace/hilos.json).
export type Hilo={id:string;nombre:string;cwd:string};
const HILOS_FILE=()=>join(process.env.PROMPTED_OS_DATA_DIR||homedir(),'hilos.json');
type HilosState={actual:string;sesiones:Record<string,string>};
function loadHilos():HilosState{try{const d=JSON.parse(readFileSync(HILOS_FILE(),'utf8'));return {actual:String(d.actual||'personal'),sesiones:d.sesiones&&typeof d.sesiones==='object'?d.sesiones:{}};}catch{return {actual:'personal',sesiones:{}};}}
function saveHilos(h:HilosState){try{const f=HILOS_FILE();mkdirSync(dirname(f),{recursive:true});writeFileSync(f+'.tmp',JSON.stringify(h,null,1));renameSync(f+'.tmp',f);}catch{}}
export function hilos():Hilo[]{
 const own=proyectosEquipo().filter(p=>p.repo&&existsSync(p.repo)).map(p=>({id:p.id,nombre:p.nombre,cwd:p.repo}));
 return [{id:'personal',nombre:'Personal',cwd:AGENT_HOME},...own];
}
const plain=(t:string)=>t.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'');
/** The context blocks in front of a message ([Telegram/WhatsApp preamble], [what went to the inbox before]): not what he asked. */
export function withoutPreamble(text:string){
 let t=text.trimStart();
 while(t.startsWith('[')){let depth=0,end=-1;for(let i=0;i<t.length;i++){if(t[i]==='[')depth++;else if(t[i]===']'&&--depth===0){end=i;break;}}if(end<0)break;t=t.slice(end+1).trimStart();}
 return t;
}
/** The thread a message names: exactly one project mentioned by name (or its usual short name); null keeps the current one. */
export function pickHilo(text:string,list:Hilo[]=hilos()):string|null{
 const t=' '+plain(withoutPreamble(text)).replace(/[^a-z0-9ñ]+/g,' ')+' ';
 const names=(h:Hilo)=>{const n=new Set([plain(h.nombre),plain(h.id).replace(/-/g,' ')]);
  if(h.id==='beta'){n.add('Beta');n.add('tf');}
  if(h.id==='prompted-os'){n.add('owner os');n.add('operating system');n.add('life os');}
  if(h.id==='norte')n.add('norte');
  if(h.id==='personal'){n.delete('personal');n.add('hilo personal');}
  return [...n].map(x=>x.replace(/[^a-z0-9ñ]+/g,' ').trim()).filter(x=>x.length>=2);};
 const hit=list.filter(h=>names(h).some(n=>t.includes(' '+n+' ')));
 return hit.length===1?hit[0].id:null;
}
let hiloState:HilosState=loadHilos();
/** Owner's own recent Claude Code and Codex sessions on this thread's project, for «¿qué hicimos en Claude con…?». */
function afuera(){
 const h=currentHilo();if(h.id==='personal')return '';
 let list:ReturnType<typeof sesionesExternas>=[];
 try{list=sesionesExternas(h.cwd,[h.nombre,h.id.replace(/-/g,' ')],homedir(),6,Object.values(hiloState.sesiones));}catch{}
 if(!list.length)return '';
 const day=(iso:string)=>new Date(iso).toLocaleDateString('es-ES',{day:'numeric',month:'numeric'});
 return `Sesiones recientes de ${profile.load().name} sobre este proyecto fuera de ti (si te pregunta qué se hizo, lee el final del archivo; son de solo lectura): ${list.map(x=>`${day(x.fecha)} ${x.fuente} «${x.titulo}» (${x.archivo})`).join(' · ')}.\n`;
}
const currentHilo=()=>hilos().find(h=>h.id===hiloState.actual)||hilos()[0];

// Alfred's standing instructions: the generic core is here; everything about the person (who they are, goals, rules,
// their projects and team) comes from their private profile in the data folder (profile.ts), never from the code.
export const discipline=()=>{const p=profile.load();return `SESIÓN DE VOZ. Eres Alfred, el asistente personal de ${p.name} dentro de ${p.app}. Eres su mentor, no un simple ejecutor. Lo que escribes se lee en voz alta con una voz masculina en castellano, no se muestra como texto.
Tu registro es el de un mayordomo de inteligencia artificial al estilo Jarvis: sereno, elegante y preciso, con un humor seco y sutil, nunca servil ni exagerado. Español de España, frases cortas y naturales. Responde en dos o tres frases salvo que la pregunta pida más.
Nada de markdown, listas, emojis, código ni URLs. Di los números como se dicen en voz alta. NUNCA leas una ruta de archivo ni un identificador largo: nombra la cosa ("la ficha del proyecto", "el CLAUDE punto md"), no su dirección.
Puedes mover la interfaz mientras hablas con indicaciones entre dobles ángulos, que no se leen: <<vista:alfred>>, <<vista:equipo>>, <<vista:proyectos>>, <<vista:entregas>>, <<vista:memoria>>, <<vista:contenido>> (calendario de publicaciones, perfiles y tendencias), <<area:${divisions.map(d=>d.id).join('|')}>> y <<proyecto:ID>>. Ponlas justo antes de la frase a la que acompañan. IDs de proyecto: ${projects.map(p=>p.id+' ('+p.name+')').join(', ')}.
Trabajas por hilos, uno por proyecto: ahora estás en el hilo «${currentHilo().nombre}»${currentHilo().id==='personal'?'':`, dentro de su repositorio (sigue su CLAUDE.md)`}. Hilos: ${hilos().map(h=>h.nombre).join(', ')}. La app te cambia de hilo cuando ${p.name} nombra otro proyecto; no hace falta que lo menciones.
${afuera()}Puedes hacer lo mismo que en Claude Code: leer, buscar, editar archivos, usar la terminal, abrir páginas y usar sus conexiones (Gmail, Calendar, Drive y las demás que tenga). Leer y consultar no pide permiso; escribir, ejecutar, enviar, crear, publicar, gastar o borrar siempre pide su sí, y la app se lo pregunta por ti. Nada se publica, envía ni gasta sin que él lo diga.
Un cambio pequeño lo haces tú aquí. El trabajo largo (varios archivos, pruebas, revisión) encárgalo al equipo o a Hermes: las órdenes están en el CLAUDE punto md de tu carpeta personal (${AGENT_HOME}); léelo antes de encargar. Si ${p.name} te dice que se ha ido a Claude o a Codex en vez de usarte, pregúntale en una línea por qué y apúntalo en el inbox de ideas como «Escape: …».
Consola de voz, la gestiona la app con frases exactas: "modo profundo", "modo rápido", "nueva conversación", "informe de uso". Si te preguntan cómo cambiar algo de eso, di la frase exacta.
Modelos, para ahorrar uso del plan: la app elige uno por turno. Sonnet cinco punto cinco para lo corriente; Opus cinco punto cinco para analizar, revisar, planificar, decidir o con "modo profundo"; ${p.name} puede forzarlo diciendo "con Opus" o "con Sonnet".${p.instructions?`
Lo que ${p.name} te ha dejado escrito sobre sí mismo, sus objetivos y cómo quiere que trabajes (instrucciones suyas, por encima de las generales cuando choquen):
${p.instructions}`:''}`;};

let session:{q:any;push:(text:string)=>void;close:()=>void}|null=null;
let starting:Promise<void>|null=null;
let turn:Turn|null=null;
// An interrupted turn still reports its own result; skip it (and its late deltas) so it never closes the next turn.
let stale=0;
let deep=false;
let active:Pick={model:SONNET,effort:'low'};
let spent=0;
let usage:Usage|null=null;
const pending=new Map<string,Pending>();
// From the phone the 75-second window often runs out (09–10/11/2026, the gym). The request is kept for LATE_MS: a «sí» that
// comes later lets Alfred do that same action (same tool on the same file, command or page) once, within GRANT_MS, without asking again.
const LATE_MS=12*3600e3,GRANT_MS=15*60e3;
const late=new Map<string,{key:string;what:string;at:number}>(),granted=new Map<string,number>();
const fingerprint=(tool:string,input:Record<string,any>)=>tool+'\n'+String(input.file_path??input.notebook_path??input.command??input.url??JSON.stringify(input));
/** A «sí» to a request that ran out: true if it was still kept, and Alfred may then do it once. */
export function approveLate(id:string,now=Date.now()):{what:string}|null{
 const e=late.get(id);late.delete(id);if(!e||now-e.at>LATE_MS)return null;
 granted.set(e.key,now+GRANT_MS);return {what:e.what};
}

export function status(){const h=currentHilo();return {available:true,deep,usage,warm:!!session,model:active.model,hilo:{id:h.id,nombre:h.nombre},hilos:hilos().map(x=>({id:x.id,nombre:x.nombre}))};}
/** Moves Alfred to another thread: the open session closes and that thread's own one is resumed on the next turn. */
export async function setHilo(id:string){
 if(!hilos().some(h=>h.id===id)||id===hiloState.actual)return false;
 await interrupt();session?.close();session=null;hiloState={...hiloState,actual:id};saveHilos(hiloState);return true;
}

// One model per turn: «modo profundo», an explicit «con Opus», a long message or thinking work → Opus high;
// everything else (and an explicit «con Sonnet») → Sonnet low, which answers in voice time.
export function route(text:string):Pick{
 // A leading [note] (the Telegram preamble) is context, not the request.
 const t=text.replace(/^\[[^\]]*\]\s*/,'').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'');
 if(/\b(con|usa|usando) sonnet\b/.test(t))return {model:SONNET,effort:'low'};
 if(deep||/\b(con|usa|usando) opus\b/.test(t)||t.length>600||HEAVY.test(t))return {model:OPUS,effort:'high'};
 return {model:SONNET,effort:'low'};
}

function record(m:any){
 const total=typeof m.total_cost_usd==='number'?m.total_cost_usd:null;
 const cost=total===null?null:Math.max(0,total-spent);if(total!==null)spent=total;
 const d=new Date(),pad=(n:number)=>String(n).padStart(2,'0'),day=`${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;
 const line={hora:`${day}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`,rol:'alfred',modelo:active.model,esfuerzo:active.effort,coste_usd:cost===null?null:Math.round(cost*1e6)/1e6};
 try{mkdirSync(LEDGER,{recursive:true});appendFileSync(join(LEDGER,day+'.jsonl'),JSON.stringify(line)+'\n');}catch{}
}

function describe(tool:string,input:Record<string,any>,title?:string):{what:string;detail:string}{
 const file=(p:unknown)=>typeof p==='string'?p.split('/').filter(Boolean).slice(-2).join(' / '):'un archivo';
 const detail=tool==='Bash'?String(input.command||''):JSON.stringify(input,null,1).slice(0,1200);
 if(tool==='Write')return {what:'crear o reescribir '+file(input.file_path),detail};
 if(tool==='Edit'||tool==='MultiEdit'||tool==='NotebookEdit')return {what:'cambiar '+file(input.file_path||input.notebook_path),detail};
 if(tool==='Bash')return {what:'ejecutar un comando'+(input.description?': '+String(input.description).toLowerCase():''),detail};
 if(tool==='WebFetch')return {what:'abrir una página web',detail:String(input.url||'')};
 if(tool.startsWith('mcp__'))return {what:(title?title.toLowerCase():'usar '+(tool.split('__').pop()||'').replace(/[_-]+/g,' '))+' en '+mcpServer(tool),detail};
 return {what:title||'usar la herramienta '+tool,detail};
}

/** The links in a message, without trailing punctuation. Exported for the checks. */
export const linksIn=(text:string)=>(text.match(/https?:\/\/[^\s<>"'«»]+/g)||[]).map(u=>u.replace(/[).,;:!?]+$/,''));
const sentByOwner=(t:Turn,url:string)=>{const u=url.replace(/\/+$/,'');return !!u&&t.urls.some(x=>x.replace(/\/+$/,'')===u);};

async function canUseTool(tool:string,input:Record<string,any>,options:{signal:AbortSignal;title?:string}){
 const current=turn;
 if(!current)return {behavior:'deny' as const,message:`No hay ninguna conversación activa con ${profile.load().name}.`};
 // A link the owner himself sent in this message is read without asking; any other page still needs his yes
 // (a page Alfred reads must not be able to send him off to fetch others).
 if(tool==='WebFetch'&&sentByOwner(current,String(input.url||'')))return {behavior:'allow' as const,updatedInput:input};
 if(ownNotebook(tool,input))return {behavior:'allow' as const,updatedInput:input};
 if(readOnlyMcp(tool))return {behavior:'allow' as const,updatedInput:input};
 const key=fingerprint(tool,input),until=granted.get(key);
 if(until){granted.delete(key);if(until>Date.now())return {behavior:'allow' as const,updatedInput:input};}
 const id='perm-'+Math.random().toString(36).slice(2,10),{what,detail}=describe(tool,input,options.title);
 return new Promise<{behavior:'allow';updatedInput:Record<string,any>}|{behavior:'deny';message:string}>(resolve=>{
  const finish=(allow:boolean,reason?:string)=>{
   const entry=pending.get(id);if(!entry)return;clearTimeout(entry.timer);pending.delete(id);
   current.emit({t:'permission-closed',id,allowed:allow});
   if(!allow&&reason===NO_ANSWER){
    const now=Date.now();for(const [k,v] of late)if(now-v.at>LATE_MS)late.delete(k);late.set(id,{key,what,at:now});
    return resolve({behavior:'deny',message:`${profile.load().name} no ha contestado a tiempo (desde el móvil no siempre puede). Dile en una línea qué ibas a hacer y que, si te contesta «sí», lo haces; no le digas que tiene que estar delante del Mac.`});
   }
   resolve(allow?{behavior:'allow',updatedInput:input}:{behavior:'deny',message:reason?`${profile.load().name} ha dicho que no: "${reason}". Ajusta el plan a lo que pide.`:`${profile.load().name} no lo ha autorizado.`});
  };
  pending.set(id,{resolve:finish,timer:setTimeout(()=>finish(false,NO_ANSWER),ANSWER_WINDOW_MS)});
  options.signal.addEventListener('abort',()=>finish(false),{once:true});
  current.emit({t:'permission',id,what,detail});
 });
}

/** Only for voice-checks.mjs: the permission rule with a fake turn. */
export const forChecks={canUseTool,setTurn:(t:Turn|null)=>{turn=t;}};
export function answer(id:string,allow:boolean,reason?:string){const entry=pending.get(id);if(!entry)return false;entry.resolve(allow,reason);return true;}

// Lift <<directions>> out and speak only complete sentences; an unclosed << waits for more text.
export function drain(current:Turn,final=false){
 let text=current.buffer;
 const open=text.lastIndexOf('<<');
 const safeEnd=!final&&open>text.lastIndexOf('>>')?open:text.length;
 let head=text.slice(0,safeEnd);const tail=text.slice(safeEnd);
 let cut=-1;
 if(final)cut=head.length;
 else{const re=/[.!?…](?=\s)|\n/g;let m;while((m=re.exec(head)))cut=m.index+1;}
 if(cut<=0){return;}
 const piece=head.slice(0,cut);current.buffer=head.slice(cut)+tail;
 const directions:string[]=[];
 const spoken=piece.replace(/<<\s*([a-z]+:[a-z0-9_-]+)\s*>>/gi,(_,d)=>{directions.push(d.toLowerCase());return ' ';})
  .replace(/<<[^>]*>>/g,' ').replace(/https?:\/\/\S+/g,'').replace(/[*_#`>|]/g,'').replace(/^\s*[-•]\s+/gm,'').replace(/\s+/g,' ').trim();
 // Telegram reads the text as written (lists, links and its own <<hoy:…>>/<<repaso:…>> directives intact); only the
 // interface directions are lifted. The voice gets the cleaned sentence.
 const raw=piece.replace(/<<\s*[a-z]+:[a-z0-9_-]+\s*>>/gi,'');
 if(spoken||directions.length||raw.trim()){current.spoken+=(current.spoken?' ':'')+spoken;current.emit({t:'say',text:spoken,directions,raw});}
}

// A file inside the configured notes vault.
const VAULT_DIR=ruta('vault','PROMPTED_OS_VAULT_DIR');
const inVault=(file:string)=>!!VAULT_DIR&&file.startsWith(VAULT_DIR.replace(/\/+$/,'')+'/');
// The notes Alfred keeps for the owner from what he tells him (the training diary): changing one needs no yes. From the
// gym, by WhatsApp, the 75-second permission always ran out and the workout never reached the diary (09–10/11/2026).
// Only Edit (a whole rewrite still asks), only these exact files, resolved through symlinks so no other file passes.
const real=(p:string)=>{try{return realpathSync(p);}catch{try{return join(realpathSync(dirname(p)),p.split(sep).pop()||'');}catch{return '';}}};
export function ownNotebook(tool:string,input:Record<string,any>):boolean{
 if(!VAULT_DIR||!['Edit','MultiEdit'].includes(tool)||typeof input.file_path!=='string'||!input.file_path.startsWith('/'))return false;
 const vault=real(VAULT_DIR),file=real(resolvePath(input.file_path));if(!vault||!file||!file.startsWith(vault+sep))return false;
 const notes=[texto('vida.diarioEntrenamiento','Personal/Salud y deporte/01-Registros/Diario-de-entrenamiento.md'),...rutas('alfred.anotaSinPreguntar')];
 return notes.some(n=>real(resolvePath(vault,n))===file);
}
// One short, honest line per tool call, in the owner's words. Never the raw command: that stays in the permission detail.
export function stepText(name:string,input:any):string{
 const file=String(input?.file_path||input?.path||''),cmd=String(input?.command||''),base=file.split('/').pop()?.replace(/\.md$/,'')||'';
 if(name==='Read'){if(inVault(file))return `Leyendo tu nota «${base}»`;if(/projects\.json$/.test(file))return 'Leyendo los proyectos del equipo';if(/Administración/.test(file))return 'Leyendo tu administración';return base?`Leyendo ${base}`:'Leyendo un archivo';}
 if(name==='Bash'){
  if(/equipo\.py\s+status/.test(cmd))return 'Mirando el tablero del equipo';
  if(/equipo\.py\s+encargar/.test(cmd))return 'Preparando el encargo para el equipo';
  if(/equipo\.py\s+(resultado|lanzado)/.test(cmd))return 'Apuntando el resultado del experimento';
  if(/equipo\.py\s+mensaje/.test(cmd))return 'Escribiendo al equipo';
  if(/\bgit\s+(log|show|diff)/.test(cmd))return 'Revisando los últimos cambios';
  if(/\b(ls|find|cat|head|tail|wc)\b/.test(cmd))return 'Mirando los archivos';
  // Otherwise Alfred's own one-line description of the command (the same words the permission card shows).
  const said=String(input?.description||'').trim().replace(/\.$/,'');
  return said&&said.length<=90?said[0].toUpperCase()+said.slice(1):'Haciendo una comprobación';}
 if(name==='Grep'||name==='Glob')return 'Buscando en tus archivos';
 if(name==='WebSearch')return input?.query?`Buscando en la web: ${String(input.query).slice(0,60)}`:'Buscando en la web';
 if(name==='WebFetch')return 'Leyendo una página web';
 if(name==='Write'||name==='Edit')return inVault(file)?`Escribiendo en tu nota «${base}»`:'Escribiendo un archivo';
 if(name==='TodoWrite')return 'Ordenando los pasos';
 if(name.startsWith('mcp__'))return (readOnlyMcp(name)?'Consultando ':'Usando ')+mcpServer(name);
 return '';
}

function explain(raw:string){
 if(/not logged in|\/login|authentication|oauth/i.test(raw))return 'No puedo pensar ahora: Claude no tiene la sesión iniciada en este Mac. Hay que volver a iniciar sesión en Claude Code.';
 if(/limit|usage|quota|rate/i.test(raw))return 'He llegado al límite de uso de Claude. Vuelve a intentarlo cuando se renueve.';
 return 'No he podido terminar la respuesta. Claude ha dado un error: '+raw.slice(0,160);
}

async function start(){
 const {query}=await import('@anthropic-ai/claude-agent-sdk');
 const h=currentHilo(),resume=hiloState.sesiones[h.id];
 const extra=[...READABLE,...(h.cwd!==AGENT_HOME?[AGENT_HOME]:[])];
 const inbox:any[]=[];let wake:(()=>void)|null=null,closed=false;
 async function* input(){while(!closed){if(inbox.length){yield inbox.shift();continue;}await new Promise<void>(r=>wake=r);wake=null;}}
 const q=query({prompt:input(),options:{
  model:active.model,effort:active.effort,fallbackModel:active.model===OPUS?SONNET:OPUS,cwd:h.cwd,additionalDirectories:extra,settingSources:['project'],...(resume?{resume}:{}),
  includePartialMessages:true,permissionMode:'default',canUseTool,allowedTools:READ_ONLY,
  systemPrompt:{type:'preset',preset:'claude_code',append:discipline()},stderr:()=>{},
 }});
 session={q,push:text=>{inbox.push({type:'user',message:{role:'user',content:text},parent_tool_use_id:null});wake?.();},close:()=>{closed=true;wake?.();try{q.close();}catch{}}};
 (async()=>{
  try{
   for await(const m of q){
    if(m.type==='system'&&(m as any).subtype==='init'&&(m as any).session_id){if(hiloState.sesiones[h.id]!==(m as any).session_id){hiloState.sesiones[h.id]=(m as any).session_id;saveHilos(hiloState);}continue;}
    if(m.type==='rate_limit_event'){
     // unifiedWindows arrives at runtime but is not in the published types; fall back to the documented fields.
     const info:any=m.rate_limit_info,w=info?.unifiedWindows||{},own=(k:string)=>info?.rateLimitType===k?info.utilization:undefined;
     usage={status:info?.status,fiveHour:w.five_hour?.utilization??own('five_hour'),sevenDay:w.seven_day?.utilization??own('seven_day'),resetsAt:w.five_hour?.resetsAt??info?.resetsAt};turn?.emit({t:'usage',usage});
     // The team's health check reads it (equipo.py check_uso): plan usage seen from the Mac, with its time.
     if(process.env.PROMPTED_OS_DATA_DIR)try{writeFileSync(join(process.env.PROMPTED_OS_DATA_DIR,'uso.json'),JSON.stringify({...usage,hora:new Date().toISOString()}));}catch{}
     continue;}
    if(m.type==='result')record(m);
    if(stale>0){if(m.type==='result')stale--;continue;}
    const current=turn;if(!current)continue;
    // Visible steps: each tool Alfred uses becomes one plain line on screen ("Mirando el tablero del equipo…").
    if(m.type==='assistant'&&m.parent_tool_use_id===null){for(const b of (m.message?.content||[]) as any[])if(b?.type==='tool_use'){const text=stepText(b.name,b.input||{});if(text)current.emit({t:'step',text});}continue;}
    if(m.type==='stream_event'&&m.parent_tool_use_id===null){
     const e=m.event;
     if(e.type==='content_block_delta'&&e.delta?.type==='text_delta'){current.buffer+=e.delta.text;drain(current);}
     else if(e.type==='content_block_start'&&e.content_block?.type==='tool_use'){drain(current,true);current.emit({t:'state',v:'tool'});}
     continue;
    }
    if(m.type==='result'){drain(current,true);turn=null;
     // An error before any word (no login, usage limit) must be said out loud, not met with silence.
     if((m.is_error||m.subtype!=='success')&&!current.spoken){const why=explain(String(m.result||m.subtype||''));note('cerebro: '+String(m.result||m.subtype).slice(0,160));current.emit({t:'error',message:why});current.done(why);}
     else current.done(current.spoken);}
   }
  }catch(e){if(resume&&hiloState.sesiones[h.id]===resume){delete hiloState.sesiones[h.id];saveHilos(hiloState);}turn?.emit({t:'error',message:'La sesión de Alfred se ha cortado. Vuelve a hablarme y la reabro.'});turn?.done('');turn=null;}
  finally{session=null;stale=0;spent=0;for(const p of pending.values())p.resolve(false);}
 })();
}

async function ensure(){if(session)return;if(!starting)starting=start().finally(()=>{starting=null;});await starting;}
export async function warm(){try{await ensure();}catch{}}

export async function speak(id:string,text:string,emit:Emit):Promise<string>{
 const want=route(text),named=pickHilo(text);
 if(named&&named!==hiloState.actual)await setHilo(named);
 if(!session)active=want;
 await ensure();
 if(turn)await interrupt();
 if(want.model!==active.model||want.effort!==active.effort){
  try{if(want.model!==active.model)await session!.q.setModel(want.model);if(want.effort!==active.effort)await session!.q.applyFlagSettings({effortLevel:want.effort});active=want;}catch{}
 }
 return new Promise<string>(resolve=>{
  turn={id,emit,buffer:'',spoken:'',done:resolve,urls:linksIn(text)};
  const h=currentHilo();emit({t:'state',v:'thinking'});emit({t:'model',model:active.model,deep});emit({t:'hilo',id:h.id,nombre:h.nombre});if(usage)emit({t:'usage',usage});
  session!.push(text);
 });
}

export const busy=()=>!!turn;

export async function interrupt(){
 const current=turn;if(!current)return;
 for(const p of pending.values())p.resolve(false,'Se ha interrumpido la conversación');
 turn=null;stale++;current.done(current.spoken);
 try{await session?.q.interrupt();}catch{stale=Math.max(0,stale-1);}
}

export async function control(action:string){
 if(action==='interrupt'){await interrupt();return status();}
 if(action==='clear'){await interrupt();session?.close();session=null;delete hiloState.sesiones[hiloState.actual];saveHilos(hiloState);return status();}
 if(action.startsWith('hilo:')){await setHilo(action.slice(5));return status();}
 if(action==='deep'||action==='fast'){deep=action==='deep';return status();}
 if(action==='warm'){void warm();return status();}
 throw Error('Acción de voz no disponible.');
}

export function shutdown(){for(const p of pending.values())p.resolve(false);session?.close();session=null;}
