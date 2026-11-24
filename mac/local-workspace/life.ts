import fs from 'node:fs/promises';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {createHash,randomUUID} from 'node:crypto';
import os from 'node:os';
import type {IncomingMessage} from 'node:http';
import {ruta,texto} from './config';
import type {WorkoutEntry,LifeData,HealthImport} from '../../alfred-os/lib/life-data';
import {validHealthSummary} from '../../alfred-os/lib/life-data';
import {shortcutDay,mergeShortcut,type ShortcutDay} from '../../alfred-os/lib/health-shortcut';

export const MAX_HEALTH_BYTES=500*1024*1024;
const clean=(s:string)=>s.replace(/\*\*|__/g,'').trim();
export function diarySessions(markdown:string):WorkoutEntry[]{
 const entries:WorkoutEntry[]=[];
 const blocks=[...markdown.matchAll(/^#{2,4}\s+(\d{4}-\d{2}-\d{2})([^\n]*)\n([\s\S]*?)(?=^#{2,4}\s+\d{4}-\d{2}-\d{2}\b|^#{1,2}\s+(?!\d{4}-\d{2}-\d{2})|$(?![\s\S]))/gm)];
 for(const m of blocks){
  const date=m[1],parsed=new Date(date+'T12:00:00Z');if(!Number.isFinite(+parsed)||parsed.toISOString().slice(0,10)!==date)continue;
  const title=clean(m[2].replace(/^\s*[·—:–-]\s*/,''))||'Entrenamiento';
  const body=m[3],explicit=clean(body).match(/(?:Estado\s*:\s*|^\s*[-*]\s*)(REALIZADO|PARCIAL|PLANIFICADO)\b/im);
  const status=(explicit?.[1].toUpperCase()||'NO CONFIRMADO') as WorkoutEntry['status'];
  const details=body.split('\n').map(clean).filter(l=>l&& !/^<!--/.test(l)).map(l=>l.replace(/^(?:[-*]|#{1,6})\s+/,''));
  entries.push({id:date+'-'+entries.length,date,dateCertain:!/fecha\s+(?:no\s+confirmada|estimada|por\s+confirmar|pendiente)|sin\s+confirmar/i.test(m[2]),title,status,details:details.slice(0,32).map(l=>l.slice(0,900)),truncated:details.length>32||details.some(l=>l.length>900)});
 }
 return entries.sort((a,b)=>b.date.localeCompare(a.date));
}
async function training():Promise<LifeData['training']>{
 const vault=ruta('vault','PROMPTED_OS_VAULT_DIR');if(!vault)return {status:'unconfigured',note:null,updatedAt:null,sessions:[]};
 const relative=texto('vida.diarioEntrenamiento','Personal/Salud y deporte/01-Registros/Diario-de-entrenamiento.md');
 try{
  const realVault=await fs.realpath(vault),note=await fs.realpath(path.resolve(realVault,relative));
  if(!note.startsWith(realVault+path.sep))throw Error('outside vault');
  const stat=await fs.stat(note);if(!stat.isFile()||stat.size>2*1024*1024)throw Error('too large');
  return {status:'ready',note,updatedAt:stat.mtime.toISOString(),sessions:diarySessions(await fs.readFile(note,'utf8'))};
 }catch(e){return {status:(e as NodeJS.ErrnoException).code==='ENOENT'?'missing':'error',note:null,updatedAt:null,sessions:[]};}
}
const healthFile=(directory:string)=>path.join(directory,'vida','salud.json');
export const healthTempFolder=(directory:string)=>path.join(os.tmpdir(),'owner-health-import-'+createHash('sha256').update(path.resolve(directory)).digest('hex').slice(0,24));
async function sweepHealthTemps(directory:string){
 // Per-Workspace namespace: never touch another app instance's import or arbitrary files.
 for(const folder of [healthTempFolder(directory),path.join(directory,'vida')]){
  let stat;try{stat=await fs.lstat(folder);}catch{continue;}if(!stat.isDirectory()||stat.isSymbolicLink())continue;
  for(const name of await fs.readdir(folder)){if(/^\.import-[0-9a-f-]{36}\.xml$/.test(name))await fs.rm(path.join(folder,name),{force:true});}
 }
}
export async function cleanInterruptedHealthImports(directory:string){if(!importing)await sweepHealthTemps(directory);}
export function helperPath(){return process.env.PROMPTED_OS_HEALTH_HELPER||'';}
// The iPhone shortcut «Alfred · Salud» saves one JSON a day in iCloud Drive (Shortcuts folder → Alfred). Read on every look at
// Vida: only when the files change is the summary rewritten, and an XML import (which drops the mark) gets them back on top.
const SHORTCUT_DEFAULT=path.join(os.homedir(),'Library/Mobile Documents/iCloud~is~workflow~my~workflows/Documents/Alfred');
export const shortcutFolder=()=>ruta('vida.saludAtajo','PROMPTED_OS_HEALTH_SHORTCUT_DIR',SHORTCUT_DEFAULT);
async function syncShortcut(directory:string){
 if(importing)return;
 let names:string[];try{names=await fs.readdir(shortcutFolder());}catch{return;}
 const folder=shortcutFolder();
 // iCloud keeps old files only in the cloud («.salud-….json.icloud»): ask for them, they come in on a later look.
 const evicted=names.filter(n=>/^\..+\.json\.icloud$/i.test(n)).slice(0,40);
 if(evicted.length)try{spawn('brctl',['download',...evicted.map(n=>path.join(folder,n))],{stdio:'ignore'}).on('error',()=>{}).unref();}catch{}
 const files=names.filter(n=>/\.json$/i.test(n)&&!n.startsWith('.')).sort().slice(-400);
 const seen:{name:string;size:number;mtime:number}[]=[];
 for(const name of files){try{const st=await fs.stat(path.join(folder,name));if(st.isFile()&&st.size<=64*1024)seen.push({name,size:st.size,mtime:st.mtimeMs});}catch{}}
 if(!seen.length)return;
 const signature=createHash('sha256').update(JSON.stringify(seen)).digest('hex').slice(0,32);
 let base=null;try{base=JSON.parse(await fs.readFile(healthFile(directory),'utf8'));if(!validHealthSummary(base))return;}catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')return;}
 if(base?.shortcut?.signature===signature)return;
 const days:ShortcutDay[]=[];
 for(const f of seen){try{const d=shortcutDay(JSON.parse(await fs.readFile(path.join(folder,f.name),'utf8')),f.name);if(d)days.push(d);}catch{}}
 const merged=mergeShortcut(base,days,new Date().toISOString().replace(/\.\d+Z$/,'Z'),signature);
 if(!validHealthSummary(merged))return;
 await fs.mkdir(path.dirname(healthFile(directory)),{recursive:true,mode:0o700});
 const tmp=healthFile(directory)+'.'+randomUUID()+'.tmp';
 try{await fs.writeFile(tmp,JSON.stringify(merged),{mode:0o600,flag:'wx'});await fs.rename(tmp,healthFile(directory));}catch{await fs.rm(tmp,{force:true});}
}
export async function lifeView(directory:string):Promise<LifeData>{
 try{await syncShortcut(directory);}catch{}
 let health:LifeData['health']={status:'missing',data:null};
 try{const stat=await fs.stat(healthFile(directory));if(stat.size>4*1024*1024)throw Error('too large');const raw=JSON.parse(await fs.readFile(healthFile(directory),'utf8'));if(!validHealthSummary(raw))throw Error('invalid');health={status:'ready',data:raw};}catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')health={status:'error',data:null};}
 const helper=helperPath();let available=false;try{await fs.access(helper);available=path.isAbsolute(helper);}catch{}
 return {local:true,training:await training(),health,capabilities:{healthImport:available}};
}
export class LifeError extends Error{constructor(public status:number,message:string){super(message);}}
let importing=false;
const parserChildren=new Set<ReturnType<typeof spawn>>();
export function stopHealthImport(){for(const child of parserChildren)child.kill('SIGTERM');}
async function receive(req:IncomingMessage,file:string){
 const handle=await fs.open(file,'wx',0o600);let size=0;
 try{for await(const chunk of req){size+=chunk.length;if(size>MAX_HEALTH_BYTES)throw new LifeError(413,'El XML supera el límite de 500 MB.');await handle.write(chunk);}if(!size)throw new LifeError(400,'El archivo está vacío.');}finally{await handle.close();}
}
async function parseHealth(file:string):Promise<Omit<HealthImport,'importedAt'>>{
 const helper=helperPath();if(!helper||!path.isAbsolute(helper))throw new LifeError(503,'La importación está disponible en la app local actualizada.');
 return new Promise((resolve,reject)=>{
  const child=spawn(helper,[file],{stdio:['ignore','pipe','ignore'],env:{PATH:'/usr/bin:/bin',LANG:'es_ES.UTF-8'}});
  parserChildren.add(child);
  let out='',done=false;const finish=(error:Error|null,value?:any)=>{if(done)return;done=true;clearTimeout(timer);error?reject(error):resolve(value);};
  const timer=setTimeout(()=>{child.kill('SIGKILL');finish(new LifeError(408,'El archivo tarda demasiado en procesarse. Prueba una exportación más pequeña.'));},120000);
  child.stdout.on('data',d=>{out+=d;if(out.length>4*1024*1024){child.kill('SIGKILL');finish(new LifeError(400,'La exportación contiene demasiados registros.'));}});
  child.on('error',()=>finish(new LifeError(503,'No se puede abrir el importador local.')));
  child.on('close',code=>{parserChildren.delete(child);if(code!==0)return finish(new LifeError(422,'No se pudo leer el XML de Salud. Descomprime la exportación del iPhone y selecciona export.xml.'));try{const value=JSON.parse(out);if(!validHealthSummary(value,false))throw Error();finish(null,value);}catch{finish(new LifeError(422,'El importador no ha devuelto datos válidos.'));}});
 });
}
export async function importHealth(directory:string,req:IncomingMessage){
 if(importing)throw new LifeError(409,'Ya hay una importación en curso.');
 if(!['application/xml','text/xml','application/octet-stream'].includes((req.headers['content-type']||'').split(';')[0]))throw new LifeError(415,'Selecciona el archivo XML de Salud.');
 const length=Number(req.headers['content-length']);if(length>MAX_HEALTH_BYTES)throw new LifeError(413,'El XML supera el límite de 500 MB.');
 importing=true;const folder=path.join(directory,'vida'),temporary=healthTempFolder(directory),file=path.join(temporary,'.import-'+randomUUID()+'.xml');let tmp='';
 try{
  await sweepHealthTemps(directory);
  await fs.mkdir(folder,{recursive:true,mode:0o700});await fs.mkdir(temporary,{recursive:true,mode:0o700});
  const stat=await fs.lstat(temporary);if(!stat.isDirectory()||stat.isSymbolicLink())throw new LifeError(500,'La carpeta temporal no está disponible.');
  await fs.chmod(temporary,0o700);await receive(req,file);
  const parsed=await parseHealth(file),data:HealthImport={...parsed,importedAt:new Date().toISOString()};
  tmp=healthFile(directory)+'.'+randomUUID()+'.tmp';await fs.writeFile(tmp,JSON.stringify(data),{mode:0o600,flag:'wx'});await fs.rename(tmp,healthFile(directory));
  return {ok:true,health:{status:'ready',data}};
 }finally{await fs.rm(file,{force:true}).catch(()=>{});if(tmp)await fs.rm(tmp,{force:true}).catch(()=>{});importing=false;}
}
