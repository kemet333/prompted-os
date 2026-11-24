// Alfred en WhatsApp: the bridge (whatsapp/puente.mjs, its own launchd service) owns the WhatsApp connection of Alfred's
// number and asks the app here for Alfred's answers, with a key only the two of them can read (Workspace/whatsapp/llave,
// written at start-up, mode 600). Same brain, memory and permission rule as Telegram; the permission is asked in the group
// and answered with «sí» or «no». With the app closed the bridge still keeps the inbox and says Alfred is asleep.
import fs from 'node:fs';
import {MAYORDOMO} from './mayordomo';
import path from 'node:path';
import {randomBytes,randomUUID,timingSafeEqual} from 'node:crypto';
import * as profile from './profile';
import * as voice from './voice';
import * as hoy from './hoy';
import * as repaso from './repaso';

export type Evento={t:'say';texto:string}|{t:'permiso';id:string;que:string;detalle:string}|{t:'fin'}|{t:'error';texto:string};
type Remember=(id:string,author:'user'|'assistant',text:string)=>Promise<void>;

let key='',dataDir='';
export function start(dir:string){
 dataDir=dir;key=randomBytes(32).toString('base64url');
 const folder=path.join(dir,'whatsapp');fs.mkdirSync(folder,{recursive:true,mode:0o700});
 fs.writeFileSync(path.join(folder,'llave'),key,{mode:0o600});
}
/** The data folder without the bridge key: Telegram's module routes to WhatsApp with it (and its checks run without start). */
export function datos(dir:string){if(!dataDir)dataDir=dir;}
export function authorized(header:unknown){
 if(!key||typeof header!=='string')return false;
 const a=Buffer.from(header),b=Buffer.from('Bearer '+key);return a.length===b.length&&timingSafeEqual(a,b);
}
const today=()=>hoy.load(dataDir,hoy.isoDay(new Date()));
const prefix=()=>'['+profile.load().name+' te escribe por WhatsApp desde el móvil, en su grupo con Alfred: no está delante del Mac y te lee en texto, no te oye. '+MAYORDOMO+' Nada de markdown (ni negritas ni títulos). Las indicaciones <<vista…>> no sirven aquí. Si te manda un enlace, léelo (WebFetch) y dile en una línea qué es y si merece la pena. '+hoy.context(today())+' '+repaso.context(repaso.load(dataDir),new Date())+']\n';

/** One message for Alfred → his answer as events, in order (the bridge sends each «say» as it comes). */
export async function converse(text:string,emit:(e:Evento)=>void,remember:Remember){
 const id=randomUUID();await remember(id,'user',text);
 const day=today()||{fecha:hoy.isoDay(new Date()),items:[]},cards=repaso.load(dataDir);
 let listChanged=false,cardsAdded=0,pieces='';
 const flush=()=>{const out=pieces.replace(/[ \t]+\n/g,'\n').replace(/\n{3,}/g,'\n\n').trim();pieces='';if(out)emit({t:'say',texto:out});};
 try{
  const reply=await voice.speak(id,prefix()+text,e=>{
   if(e.t==='say'&&(e.raw||e.text)){const r=hoy.applyDirectives(e.raw??e.text,day,dataDir);if(r.changed)listChanged=true;const c=repaso.applyDirectives(r.text,cards,new Date());cardsAdded+=c.added.length;pieces+=c.text;}
   else if(e.t==='state'&&e.v==='tool')flush();
   else if(e.t==='permission'){flush();emit({t:'permiso',id:e.id,que:e.what,detalle:(e.detail||'').slice(0,700)});}
   else if(e.t==='error')emit({t:'error',texto:e.message});
  });
  flush();
  if(listChanged)hoy.save(dataDir,day);
  if(cardsAdded)repaso.save(dataDir,cards);
  if(reply)await remember(id,'assistant',repaso.applyDirectives(hoy.applyDirectives(reply,{fecha:day.fecha,items:[]}).text,[],new Date()).text);
 }catch{emit({t:'error',texto:'No he podido abrir la sesión de Claude. Comprueba en el Mac que tu cuenta sigue conectada.'});}
 emit({t:'fin'});
}
export const permiso=(id:string,si:boolean)=>voice.answer(id,si,si?undefined:'Owner ha dicho que no por WhatsApp');
/** A «sí» that came after the request ran out: Alfred may do that one action without asking again. */
export const permisoTarde=(id:string)=>!!voice.approveLate(id);

// ---------- what goes to the group instead of Telegram (the bridge delivers it: whatsapp/avisos.mjs) ----------
const carpeta=()=>path.join(dataDir,'whatsapp');
/** WhatsApp is where Alfred writes first when the bridge is connected to the group (estado.json, refreshed every few seconds)
 *  and Owner has not turned it off (configuracion.json → whatsapp.avisos: false). Otherwise Telegram, as before. */
export function activo(){
 if(!dataDir)return false;
 try{if(JSON.parse(fs.readFileSync(path.join(dataDir,'configuracion.json'),'utf8'))?.whatsapp?.avisos===false)return false;}catch{}
 try{const e=JSON.parse(fs.readFileSync(path.join(carpeta(),'estado.json'),'utf8'));return e.conectado===true&&Date.now()-Date.parse(e.hora)<5*60e3;}catch{return false;}
}
let ultimo=0;
const enviados=new Map<number,string>();
const ENVIADOS=()=>path.join(carpeta(),'enviados.json');
/** Leaves one message for the bridge and returns its number, which Telegram code keeps as the message id. */
export function encolar(e:{texto:string;botones?:any[][];responder?:boolean}):number{
 const ref=Math.max(Date.now(),ultimo+1);ultimo=ref;
 const dir=path.join(carpeta(),'salida');fs.mkdirSync(dir,{recursive:true,mode:0o700});
 const tmp=path.join(dir,'.'+ref+'.tmp');
 fs.writeFileSync(tmp,JSON.stringify({ref,texto:e.texto,...(e.botones?.length?{botones:e.botones}:{}),...(e.responder?{responder:true}:{})}),{mode:0o600});
 fs.renameSync(tmp,path.join(dir,ref+'-app.json'));
 // what each message said: a reply to «¿Qué mediste…?» is recognised by the text it answers, as in Telegram
 if(!enviados.size)try{for(const [k,v] of Object.entries(JSON.parse(fs.readFileSync(ENVIADOS(),'utf8'))))enviados.set(Number(k),String(v));}catch{}
 enviados.set(ref,e.texto);for(const k of [...enviados.keys()].slice(0,Math.max(0,enviados.size-200)))enviados.delete(k);
 try{fs.writeFileSync(ENVIADOS(),JSON.stringify(Object.fromEntries(enviados)),{mode:0o600});}catch{}
 return ref;
}
export function textoDe(ref:number){
 if(!enviados.has(ref))try{const t=JSON.parse(fs.readFileSync(ENVIADOS(),'utf8'))[ref];if(typeof t==='string')return t;}catch{}
 return enviados.get(ref);
}
/** Votes made while the app was closed (the bridge kept them): taken once, in order. */
export function pendientes():{data:string;ref:number}[]{
 const f=path.join(carpeta(),'botones-pendientes.jsonl'),tomado=f+'.procesando';
 try{fs.renameSync(f,tomado);}catch{return [];}
 try{return fs.readFileSync(tomado,'utf8').split('\n').filter(Boolean).flatMap(l=>{try{const v=JSON.parse(l);return typeof v.data==='string'?[{data:v.data,ref:Number(v.ref)||0}]:[];}catch{return [];}});}
 finally{fs.rmSync(tomado,{force:true});}
}
