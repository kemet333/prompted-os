// Contenido: the content calendar of every project in one place, so the owner can see what goes out, when, and how it looks.
// Three sources, one list:
//  · the content machine (pieces + publications in machine.db): what it schedules and, once PostOnce is connected,
//    publishes. Changing a time or marking something as published writes there, so the machine stays the backend.
//  · a posting plan in the vault (contenido.plan: a folder with plan-*.md, one «### Day (date)» and «#### Network · Type · HH:MM: Title» per post)
//  · finished full-visual reels with no date yet (Documents/full-visual-0.2.0/workspace/output).
// Plan and loose reels have no database of their own: times and «ya publicado» for them live in Workspace/contenido/agenda.json.
// Above all of them, the publishing calendar (Workspace/contenido/calendario.json, Owner 29/10): what is there gets published by the
// scheduled publisher, which writes back the real PostOnce state. It is the only source that says «se publica solo».
// Profiles in Workspace/contenido/perfiles.json are edited here and changed on the network by the owner; their https links provide measured links for each post, while TF keeps its comparator.
import * as profile from './profile';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {createHash} from 'node:crypto';
import {DatabaseSync} from 'node:sqlite';
import {VAULT} from './cartera';
import {ruta,texto,lista} from './config';
import {ANALISIS_DIAS_VIGENTE} from '../../alfred-os/lib/office-limits';
import {tasa} from '../../alfred-os/lib/contenido-tasas';
import {avisoSinEnlace,baseEnlaceValida,etiquetaEnlace,sinEnlaceHaceDias,sinEnlaceMedido} from '../../alfred-os/lib/contenido-enlace';

export {avisoSinEnlace,baseEnlaceValida,etiquetaEnlace,avisoEnlacePerfil,primerPerfilSinEnlace,ofreceLanzado,perfilParaLanzar,sinEnlaceMedido,sinEnlaceHaceDias} from '../../alfred-os/lib/contenido-enlace';

// Every source is optional and set in configuracion.json (config.ts); an empty one is simply not read.
const DB=ruta('contenido.maquina','PROMPTED_OS_RF_DB');
const FV_OUT=ruta('contenido.fullVisual','PROMPTED_OS_FV_OUT');
const REELS_DIR=ruta('contenido.reelsCurso','PROMPTED_OS_COURSE_REELS');
const PLAN_DIR=texto('contenido.plan','','PROMPTED_OS_CONTENT_PLAN');
// The day's videos of the Plan de contenido único, as the content system delivers them: Reels/<fecha>/kc-1030-h1.mp4 + .txt
const DAY_DIR=ruta('contenido.reelsDia','PROMPTED_OS_DAY_REELS');
const MEDIA_ROOTS=()=>[FV_OUT,REELS_DIR,DAY_DIR,DB?path.join(path.dirname(DB),'media'):''].filter(Boolean);
// The accounts whose content this calendar follows (configuracion.json → contenido.marcas):
//   [{"id": "mi-marca", "nombre": "Mi marca", "prefijo": "mm", "claves": ["mi-marca"], "perfiles": [{"red": "instagram", "usuario": "…", "nombre": "…", "bio": "…", "enlace": "…", "nota": "…"}]}]
// prefijo names the day's files (mm-1030-h1.mp4); claves tell a loose reel's folder or text apart; contenido.planMarca is the
// account the vault plan belongs to (by default the first).
// trial: false for an account that can't run Instagram Trial Reels (it gets normal reels from the calendar, no Trial Reels slots).
type Marca={id:string;nombre:string;prefijo:string;claves:string[];perfiles:any[];trial:boolean};
const MARCAS:Marca[]=lista('contenido.marcas').filter((m:any)=>m&&typeof m.id==='string'&&/^[a-z0-9-]{2,40}$/.test(m.id)).map((m:any)=>({
 id:m.id,nombre:String(m.nombre||m.id),prefijo:/^[a-z]{1,6}$/.test(m.prefijo||'')?m.prefijo:'',
 claves:(Array.isArray(m.claves)?m.claves:[m.id]).filter((c:unknown)=>typeof c==='string'&&c.trim()).map((c:string)=>c.toLowerCase()),perfiles:Array.isArray(m.perfiles)?m.perfiles:[],trial:m.trial!==false}));
export const BRANDS:Record<string,string>=Object.fromEntries(MARCAS.map(m=>[m.id,m.nombre]));
export const marcas=()=>MARCAS.map(m=>({id:m.id,nombre:m.nombre}));
const PLAN_BRAND=texto('contenido.planMarca',MARCAS[0]?.id||'');
const TZ='Europe/Madrid';

export type Video={nombre:string;path:string};
export type Medida={vistas:number;visitas:number;clics:number;compras:number;hora:string};
export type Item={id:string;fuente:'maquina'|'plan'|'video'|'ritmo'|'calendario';brand:string;marca:string;canal:string;tipo:string;fecha:string|null;
 titulo:string;gancho:string;texto:string;nota:string;estado:'publicado'|'programado'|'postonce'|'tu'|'falta'|'fallo'|'plan'|'sin-fecha';
 detalle:string;url:string;enlace:string;videos:Video[];portadas:string[];elegido:number;horaEditable:boolean;marcable:boolean;medida:Medida|null;porMedir:boolean};
export type Perfil={brand:string;red:string;usuario:string;nombre:string;bio:string;enlace:string;nota:string};

// ---------- small helpers ----------
const sha=(s:string)=>createHash('sha1').update(s).digest('hex').slice(0,10);
export function enlaceMedido(it:{id:string;brand:string;canal:string},base=''):string{
 const slug=it.id.toLowerCase().replace(/[^a-z0-9]+/g,'-').slice(0,60).replace(/^-+|-+$/g,'');
 if(it.brand==='beta')return 'https://beta.com/comparar.html?utm_source='+encodeURIComponent(it.canal)+'&utm_medium=social&utm_campaign=reel&utm_content='+slug;
 if(!baseEnlaceValida(base))return '';
 const url=new URL(base.trim());
 url.searchParams.set('utm_source',it.canal);
 url.searchParams.set('utm_medium','social');
 url.searchParams.set('utm_campaign','reel');
 url.searchParams.set('utm_content',slug);
 return url.toString();
}
/** «2026-10-21» + «20:00» in Madrid → ISO in UTC (handles the October and March clock changes). */
export function madrid(day:string,hm:string):string{
 const [y,m,d]=day.split('-').map(Number),[h,mi]=hm.split(':').map(Number),guess=Date.UTC(y,m-1,d,h,mi);
 const off=(t:number)=>{const p=Object.fromEntries(new Intl.DateTimeFormat('en-GB',{timeZone:TZ,hourCycle:'h23',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).formatToParts(new Date(t)).map(x=>[x.type,x.value]));
  return Date.UTC(+p.year,+p.month-1,+p.day,+p.hour,+p.minute)-t;};
 let t=guess-off(guess);t=guess-off(t);return new Date(t).toISOString();
}
const workspace=(dataDir:string)=>path.join(dataDir,'contenido');
async function readJson<T>(file:string,fallback:T):Promise<T>{try{return JSON.parse(await fs.promises.readFile(file,'utf8'));}catch{return fallback;}}
async function writeJson(file:string,data:unknown){await fs.promises.mkdir(path.dirname(file),{recursive:true});const tmp=file+'.tmp';await fs.promises.writeFile(tmp,JSON.stringify(data,null,1));await fs.promises.rename(tmp,file);}
type Agenda=Record<string,{fecha?:string;publicado?:string;url?:string;oculto?:boolean}>;
const agendaFile=(dataDir:string)=>path.join(workspace(dataDir),'agenda.json');
const calendarioFile=(dataDir:string)=>path.join(workspace(dataDir),'calendario.json');
const medidasFile=(dataDir:string)=>path.join(workspace(dataDir),'medidas.json');

// ---------- daily analysis of our reels ----------
type ResultadoAnalisis={titulo:string;red:string;vistas:number;x_mediana:number;url:string};
type AnalisisMarca={resumen:string;ganadores:ResultadoAnalisis[];perdedores:ResultadoAnalisis[];
 ganchos_que_ganan:string[];conceptos_que_ganan:string[];evitar:string[];
 exprimir:{concepto:string;por_que:string;variantes:{gancho:string;angulo:string}[]}|null;
 validados:string[];repetir:string[];conclusiones:string[]};
export type Analisis={fecha:string;marcas:Record<string,AnalisisMarca>};
function tiempoAnalisis(fecha:unknown):number|null{
 if(typeof fecha!=='string')return null;
 // Keep the report's date, not the file's modification time. A date without a time starts at midnight in Madrid.
 if(!/^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2}))?$/.test(fecha))return null;
 const day=fecha.slice(0,10),parsed=Date.parse(day+'T00:00:00Z');
 if(!Number.isFinite(parsed)||new Date(parsed).toISOString().slice(0,10)!==day)return null;
 const time=Date.parse(fecha.length===10?madrid(fecha,'00:00'):fecha);
 return Number.isFinite(time)&&time<=Date.now()?time:null;
}
export async function fechaAnalisis(dataDir:string):Promise<string|null>{
 const raw=await readJson<unknown>(path.join(workspace(dataDir),'analisis','ultimo.json'),null);
 if(!raw||typeof raw!=='object'||Array.isArray(raw)||!('fecha' in raw))return null;
 return typeof raw.fecha==='string'&&tiempoAnalisis(raw.fecha)!==null?raw.fecha:null;
}
export async function analisis(dataDir:string):Promise<Analisis|null>{
 const raw=await readJson<unknown>(path.join(workspace(dataDir),'analisis','ultimo.json'),null);
 const objeto=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
 if(!objeto(raw)||typeof raw.fecha!=='string'||!objeto(raw.marcas))return null;
 const fecha=raw.fecha,time=tiempoAnalisis(fecha);
 if(time===null||Date.now()-time>ANALISIS_DIAS_VIGENTE*86400_000)return null;
 const text=(v:unknown,max=400):string=>{if(typeof v!=='string')throw new Error('Texto no válido');return v.trim().slice(0,max);};
 // The scheduled task leaves out the lists a brand does not use (TF has no «validados», Norte no «repetir»): absent is empty, a wrong type is rejected.
 const list=<T,>(v:unknown,parse:(x:unknown)=>T):T[]=>{if(v==null)return [];if(!Array.isArray(v))throw new Error('Lista no válida');return v.map(parse).slice(0,20);};
 const number=(v:unknown):number=>{if(typeof v!=='number'||!Number.isFinite(v)||v<0)throw new Error('Cifra no válida');return v;};
 const result=(v:unknown):ResultadoAnalisis=>{
  if(!objeto(v))throw new Error('Resultado no válido');
  const url=text(v.url,2000);
  return {titulo:text(v.titulo,160),red:text(v.red,40),vistas:number(v.vistas),x_mediana:number(v.x_mediana),url:/^https?:\/\//i.test(url)?url:''};
 };
 const marca=(m:unknown):AnalisisMarca=>{
  if(!objeto(m))throw new Error('Marca no válida');
  let exprimir:AnalisisMarca['exprimir']=null;
  if(m.exprimir!=null){
   const e=m.exprimir;if(!objeto(e))throw new Error('Exprimir no válido');
   exprimir={concepto:text(e.concepto,200),por_que:text(e.por_que),variantes:list(e.variantes,v=>{
    if(!objeto(v))throw new Error('Variante no válida');return {gancho:text(v.gancho,200),angulo:text(v.angulo,300)};
   })};
  }
  return {resumen:text(m.resumen),ganadores:list(m.ganadores,result),perdedores:list(m.perdedores,result),
   ganchos_que_ganan:list(m.ganchos_que_ganan,v=>text(v)),conceptos_que_ganan:list(m.conceptos_que_ganan,v=>text(v)),
   evitar:list(m.evitar,v=>text(v)),exprimir,validados:list(m.validados,v=>text(v)),repetir:list(m.repetir,v=>text(v)),conclusiones:list(m.conclusiones,v=>text(v))};
 };
 // A malformed brand is left out on its own; the others still show.
 const marcas:Record<string,AnalisisMarca>={};
 for(const id of Object.keys(BRANDS)){
  if(!Object.hasOwn(raw.marcas,id))continue;
  try{marcas[id]=marca(raw.marcas[id]);}catch{/* only this brand is skipped */}
 }
 return Object.keys(marcas).length?{fecha,marcas}:null;
}

// ---------- the content machine ----------
function open(write=false){
 if(!fs.existsSync(DB))return null;
 const db=new DatabaseSync(DB,write?{}:{readOnly:true});db.exec('PRAGMA busy_timeout=4000');return db;
}
function machineState(status:string,error:string|null,fv:any):{estado:Item['estado'];detalle:string}{
 if(status==='published')return {estado:'publicado',detalle:'Publicado'};
 if(status==='sent')return {estado:'postonce',detalle:'En PostOnce, a la espera de su hora'};
 if(status==='failed')return {estado:'fallo',detalle:'Falló al publicar: '+(error||'sin motivo')};
 if(fv?.state==='manual'||/a mano/i.test(error||''))return {estado:'tu',detalle:'Lo subes tú'};
 if(status==='blocked')return {estado:'falta',detalle:/media/i.test(error||'')?'Falta el vídeo':(error||'Bloqueado')};
 return {estado:'plan',detalle:'En la máquina de contenido: no se publica solo (no está en el calendario de publicación)'};
}
function machine():{items:Item[];proyectos:Set<string>}{
 const db=open(),proyectos=new Set<string>();if(!db)return {items:[],proyectos};
 try{
  const rows=db.prepare(`select u.id pub,coalesce(u.channel,p.channel) channel,u.publish_at,u.status,u.error,u.url,p.id piece,p.brand,p.format,p.title,p.hook,p.caption,p.media_path,p.meta
   from pieces p left join publications u on p.id=u.piece_id
   where u.id is not null or (p.status='approved' and json_extract(p.meta,'$.fv.project') is not null)`).all() as any[];
  const items=rows.filter(r=>BRANDS[r.brand]).map(r=>{
   let meta:any={};try{meta=r.meta?JSON.parse(r.meta):{};}catch{}
   const fv=meta.fv||{};
   if(fv.project)proyectos.add(fv.project);
   const s=r.pub?machineState(r.status,r.error,fv):
    fv.videos?.length?{estado:'tu' as const,detalle:'Vídeo listo: revisa las aperturas antes de publicarlo'}:
    fv.error?{estado:'fallo' as const,detalle:'Producción pendiente: '+String(fv.error).slice(0,160)}:
    {estado:'falta' as const,detalle:'Pendiente de producción, sin publicación programada'};
   const vids:string[]=Array.isArray(fv.videos)?fv.videos:r.media_path?[r.media_path]:[];
   const chosen=r.media_path?Math.max(0,vids.indexOf(r.media_path)):Math.max(0,(Number(fv.chosen)||1)-1);
   const nota=String(fv.note||'');
   return {id:r.pub?'rf:'+r.pub:'rf-pieza:'+r.piece,fuente:'maquina',brand:r.brand,marca:BRANDS[r.brand],canal:r.channel,tipo:r.format==='tiktok'?'Vídeo':r.format==='reel'?'Reel':r.format,
    fecha:r.publish_at,titulo:r.title||r.hook||'(sin título)',gancho:r.hook||'',texto:r.caption||'',nota,...s,url:r.url||'',enlace:'',
    videos:vids.map((v,i)=>({nombre:vids.length>1?'Apertura '+(i+1):'Vídeo',path:v})),portadas:[],elegido:chosen,
    horaEditable:!!r.pub&&['queued','blocked','failed'].includes(r.status),marcable:!!r.pub&&r.status!=='sent',medida:null,porMedir:false} as Item;
  });
  return {items,proyectos};
 }finally{db.close();}
}

// ---------- the publishing calendar ----------
type Entrada={id:string;fecha:string;hora:string;marca:string;red:string;video?:string;texto?:string;titulo?:string;origen?:string;
 estado:'pendiente'|'programado'|'publicado'|'fallo'|'cancelado';url?:string|null;nota?:string};
/** Whether the scheduled task that publishes the calendar exists: Owner switched it off on 31/10/2026, and then a pending
 *  entry must not claim it goes out by itself. */
const publicaSolo=()=>fs.existsSync(ruta('contenido.tareaPublicar','PROMPTED_OS_TAREA_PUBLICAR',path.join(os.homedir(),'.claude','scheduled-tasks','publicar-reels-diarios','SKILL.md')));
const CAL_ESTADO:Record<string,(e:Entrada,auto:boolean)=>{estado:Item['estado'];detalle:string}>={
 pendiente:(e,auto)=>({estado:'programado',detalle:auto?`Se publica solo el ${e.fecha.slice(8)}/${e.fecha.slice(5,7)} a las ${e.hora}`
  :`Para el ${e.fecha.slice(8)}/${e.fecha.slice(5,7)} a las ${e.hora}. La publicación automática está apagada: lo subes tú`}),
 programado:e=>({estado:'postonce',detalle:`En PostOnce: sale a las ${e.hora}`}),
 publicado:()=>({estado:'publicado',detalle:'Publicado'}),
 fallo:e=>({estado:'fallo',detalle:'Falló al publicar: '+(e.nota||'sin motivo')}),
};
export async function calendario(dataDir:string):Promise<Item[]>{
 const cal=await readJson<{entradas?:Entrada[]}>(calendarioFile(dataDir),{});
 const out:Item[]=[],auto=publicaSolo();
 for(const e of Array.isArray(cal.entradas)?cal.entradas:[]){
  if(!e||typeof e.id!=='string'||!BRANDS[e.marca]||!validDay(e.fecha)||!/^\d{2}:\d{2}$/.test(e.hora||'')||!CAL_ESTADO[e.estado])continue;
  const texto=e.texto?await fs.promises.readFile(e.texto,'utf8').catch(()=>''):'';
  const titulo=(e.titulo||texto.split('\n')[0]||'(sin título)').trim().slice(0,120);
  out.push({id:'cal:'+e.id,fuente:'calendario',brand:e.marca,marca:BRANDS[e.marca],canal:CANAL[e.red]||e.red,tipo:'Reel',fecha:madrid(e.fecha,e.hora),
   titulo,gancho:'',texto:texto.trim(),nota:e.origen||'',...CAL_ESTADO[e.estado](e,auto),url:e.url||'',enlace:'',
   videos:e.video?[{nombre:'Vídeo',path:e.video}]:[],portadas:[],elegido:0,horaEditable:false,marcable:false,medida:null,porMedir:false});
 }
 return out;
}

// ---------- the posting plan in the vault ----------
const CANAL:Record<string,string>={instagram:'instagram',x:'x',telegram:'telegram',discord:'discord',youtube:'youtube',email:'email',tiktok:'tiktok'};
async function planFile():Promise<string|null>{
 if(!VAULT||!PLAN_DIR)return null;
 try{const fs_=(await fs.promises.readdir(path.join(VAULT,PLAN_DIR))).filter(f=>/^plan-.*\.md$/.test(f)).sort();return fs_.length?path.join(VAULT,PLAN_DIR,fs_[fs_.length-1]):null;}catch{return null;}
}
/** The plan's days (### Lun 19 (2026-10-19)) and posts (#### Red · Tipo · HH:MM: Título), with their text blocks. */
export function parsePlan(md:string,reels:string[]=[]):Item[]{
 const out:Item[]=[];let day='';let cur:{red:string;tipo:string;hm:string;titulo:string;lines:string[]}|null=null;
 const flush=()=>{
  if(!cur||!day)return;
  const blocks:{label:string;text:string}[]=[];let label='',inCode=false,buf:string[]=[];const notes:string[]=[];
  for(const l of cur.lines){
   if(l.startsWith('```')){if(inCode){blocks.push({label,text:buf.join('\n').trim()});buf=[];label='';}inCode=!inCode;continue;}
   if(inCode){buf.push(l);continue;}
   const b=l.match(/^\*\*(.+?)\*\*\s*$/);if(b){label=b[1];continue;}
   const n=l.match(/^_(.+)_\s*$/);if(n)notes.push(n[1]);
  }
  const red=cur.red.toLowerCase(),canal=CANAL[red]||red;
  const gancho=blocks.find(b=>/gancho|texto en pantalla|título/i.test(b.label))?.text||'';
  const texto=(blocks.find(b=>/caption|mensaje|texto|descripción|post|#/i.test(b.label)&&b.text!==gancho)||blocks.find(b=>b.text!==gancho)||blocks[0])?.text||'';
  const n=cur.titulo.match(/^Reel (\d+)\b/);
  const vids=n?reels.filter(f=>f.startsWith(`reel${n[1]}-`)).sort((a,b)=>Number(/corto/.test(a))-Number(/corto/.test(b))):[];
  const id='plan:'+sha(day+'|'+cur.red+'|'+cur.tipo+'|'+cur.titulo);
  out.push({id,fuente:'plan',brand:PLAN_BRAND,marca:BRANDS[PLAN_BRAND]||'',canal,tipo:cur.tipo,fecha:madrid(day,cur.hm),titulo:cur.titulo,gancho,texto,
   nota:notes.join(' '),estado:'plan',detalle:'Del plan',url:'',enlace:'',
   videos:vids.map(f=>({nombre:/corto/.test(f)?'Versión corta':'Vídeo',path:path.join(REELS_DIR,f)})),portadas:[],elegido:0,horaEditable:true,marcable:true,medida:null,porMedir:false});
  cur=null;
 };
 for(const line of md.split('\n')){
  const d=line.match(/^### .*\((\d{4}-\d{2}-\d{2})\)/);
  if(d||/^## /.test(line)){flush();day=d?d[1]:(/^## Semana/.test(line)?day:'');continue;}
  const h=line.match(/^#### ([^·]+?) · ([^·]+?) · (\d{1,2}:\d{2}): (.+)$/);
  if(h){flush();cur={red:h[1].trim(),tipo:h[2].trim(),hm:h[3].padStart(5,'0'),titulo:h[4].trim(),lines:[]};continue;}
  if(cur)cur.lines.push(line);
 }
 flush();
 // «Tú · Revisión» is the owner's own work, not something that goes out
 return out.filter(i=>!['tú','tu','privado'].includes(i.canal));
}

// ---------- finished reels with no date ----------
async function looseReels(known:Set<string>):Promise<Item[]>{
 if(!FV_OUT)return [];
 let dirs:string[]=[];try{dirs=await fs.promises.readdir(FV_OUT);}catch{return [];}
 const out:Item[]=[];
 for(const d of dirs){
  if(known.has(d)||/prueba/.test(d))continue;
  const dir=path.join(FV_OUT,d);let files:string[]=[];try{files=await fs.promises.readdir(dir);}catch{continue;}
  const mp4=files.filter(f=>f.endsWith('.mp4')&&!/\.v\d+\.mp4$/.test(f)&&!/sin-cta/.test(f)).sort();if(!mp4.length)continue;
  const capFile=files.filter(f=>/^caption.*\.txt$/.test(f)).sort((a,b)=>b.localeCompare(a))[0];
  const texto=capFile?await fs.promises.readFile(path.join(dir,capFile),'utf8').catch(()=>''):'';
  const where=(d+'\n'+texto).toLowerCase();
  const brand=MARCAS.find(m=>m.claves.some(c=>where.includes(c))||(m.prefijo&&d.includes('-'+m.prefijo+'-')))?.id||'';
  if(!brand)continue;
  // the caption's first line, unless it is the call to action («Comenta PALABRA…»)
  const titulo=(texto.split('\n').map(l=>l.trim()).filter(Boolean).find(l=>!/^comenta\b/i.test(l))||d.replace(/^\d{4}-\d{2}-\d{2}-full-visual-/,'').replace(/-/g,' ')).slice(0,90);
  out.push({id:'fv:'+d,fuente:'video',brand,marca:BRANDS[brand],canal:'instagram',tipo:'Reel',fecha:null,titulo,gancho:'',texto:texto.trim(),nota:'Hecho por el sistema de contenidos, todavía sin fecha.',
   estado:'sin-fecha',detalle:'Listo, sin fecha',url:'',enlace:'',videos:mp4.map(f=>({nombre:f.replace(/\.mp4$/,''),path:path.join(dir,f)})),
   portadas:files.filter(f=>/^cover.*\.jpg$/.test(f)&&!/\.v\d+\./.test(f)).sort().map(f=>path.join(dir,f)),elegido:0,horaEditable:true,marcable:true,medida:null,porMedir:false});
 }
 return out;
}

/** Covers next to a video (cover-h1.jpg for …-h1.mp4), for the machine's reels too. */
async function addCovers(items:Item[]){
 for(const it of items){
  if(it.portadas.length||!it.videos.length)continue;
  const dir=path.dirname(it.videos[0].path);
  try{const files=await fs.promises.readdir(dir);it.portadas=files.filter(f=>/^cover.*\.jpg$/.test(f)&&!/\.v\d+\./.test(f)).sort().map(f=>path.join(dir,f));}catch{}
 }
}

// The good reel at 20:00 of an account (ritmo:kc-2000:2026-10-28): the only slot of the rhythm that is marked and measured.
export const RITMO_BUENO=/^ritmo:([a-z]{1,6})-2000:(\d{4}-\d{2}-\d{2})$/;
export function esReelBueno(id:string):boolean{
 const m=id.match(RITMO_BUENO);return !!m&&!!PREFIX[m[1]];
}

export function porMedir(it:Item,ahora:number):boolean{
 return (it.fuente!=='ritmo'||esReelBueno(it.id))&&it.estado==='publicado'&&it.medida===null&&(!it.fecha||Date.parse(it.fecha)<=ahora-24*3600_000);
}

export const PREGUNTA_CIFRAS_INICIO='📊 Cifras de «';
export function preguntaCifras(it:Item):string{
 const fecha=it.fecha?', publicado el '+new Intl.DateTimeFormat('es-ES',{timeZone:TZ,day:'2-digit',month:'2-digit'}).format(new Date(it.fecha)):'';
 const etiqueta=typeof it.enlace==='string'?etiquetaEnlace(it.enlace):'';
 return `${PREGUNTA_CIFRAS_INICIO}${it.titulo}» (${it.marca} · ${it.canal}${fecha}). Responde a este mensaje con cuatro números: vistas, visitas, clics y compras. Por ejemplo: 1200 40 8 0${etiqueta?` Los clics y compras de esta publicación son los que lleguen con la etiqueta «${etiqueta}».`:''}`;
}

export function cifrasDeTexto(texto:string):{vistas:string;visitas:string;clics:string;compras:string}|null{
 const trozos=texto.trim().split(/[\s,;]+/);
 if(trozos.length!==4||trozos.some(t=>!/^\d+$/.test(t)&&!/^\d{1,3}(\.\d{3})+$/.test(t)))return null;
 const [vistas,visitas,clics,compras]=trozos.map(t=>t.replace(/\./g,''));
 return {vistas,visitas,clics,compras};
}

export function publicacionDeRespuesta(citado:string,items:Item[]):Item|null{
 return items.find(it=>it.porMedir===true&&preguntaCifras(it)===citado)||null;
}

export function siguientePorMedir(items:Item[],salvo=''):Item|null{
 return items.find(it=>it.porMedir===true&&it.id!==salvo)||null;
}

export function apuntadoCifras(it:Item,medida:Medida,quedan:number):string{
 const vistas=Number(medida.vistas),clics=Number(medida.clics),compras=Number(medida.compras);
 return `Apuntado: «${it.titulo}» — ${medida.vistas} vistas, ${medida.visitas} visitas, ${medida.clics} clics, ${medida.compras} compras.`+(vistas>0?` Clics por vista: ${tasa(clics,vistas)}.`:'')+(clics>0?` Compras por clic: ${tasa(compras,clics)}.`:'')+(quedan>0?` Quedan ${quedan}; te pregunto la siguiente.`:quedan===0?' No queda ninguna por medir.':'');
}

export function lineaPorMedir(items:Item[]):string[]{
 const n=items.filter(it=>it.porMedir).length;
 return n?[`apuntar lo medido de ${n} publicaci${n===1?'ón':'ones'} (/medir)`]:[];
}

export function lineaSinEnlace(items:Item[],ahora:number,previo?:{dias:number;n:number}|null):string[]{
 const t=avisoSinEnlace(items,ahora,previo);return t?[t]:[];
}

export async function lineaSinEnlaceMedida(dataDir:string,items:Item[],ahora:number):Promise<string[]>{
 const file=path.join(workspace(dataDir),'sin-enlace.json'),raw=await readJson<unknown>(file,{});
 const historial:Record<string,unknown>=raw&&typeof raw==='object'&&!Array.isArray(raw)?raw as Record<string,unknown>:{};
 const hoy=new Date(ahora).toLocaleDateString('sv-SE',{timeZone:TZ}),previo=sinEnlaceHaceDias(historial,hoy);
 const total=sinEnlaceMedido(items,ahora).reduce((n,g)=>n+g.n,0);
 const marcas=Object.fromEntries([...new Set(items.map(it=>it.brand))].map(brand=>[brand,sinEnlaceMedido(items.filter(it=>it.brand===brand),ahora).reduce((n,g)=>n+g.n,0)] as const).filter(([,n])=>n>0));
 historial[hoy]={total,marcas};
 const fechas=Object.keys(historial).filter(fecha=>/^\d{4}-\d{2}-\d{2}$/.test(fecha)).sort().reverse().slice(0,60);
 try{await writeJson(file,Object.fromEntries(fechas.map(fecha=>[fecha,historial[fecha]])));}
 catch{ // The morning reminder still works if its daily count cannot be saved.
  return lineaSinEnlace(items,ahora,previo);
 }
 return lineaSinEnlace(items,ahora,previo);
}

export function porRed(items:Item[]){
 const grupos=new Map<string,{brand:string;marca:string;red:string;publicadas:number;medidas:number;vistas:number;visitas:number;clics:number;compras:number}>();
 for(const it of items){
  if(it.estado!=='publicado'||it.fuente==='ritmo'&&!esReelBueno(it.id))continue;
  const key=JSON.stringify([it.brand,it.canal]);
  const grupo=grupos.get(key)||{brand:it.brand,marca:it.marca,red:it.canal,publicadas:0,medidas:0,vistas:0,visitas:0,clics:0,compras:0};
  grupo.publicadas++;
  if(it.medida){grupo.medidas++;grupo.vistas+=it.medida.vistas;grupo.visitas+=it.medida.visitas;grupo.clics+=it.medida.clics;grupo.compras+=it.medida.compras;}
  grupos.set(key,grupo);
 }
 return [...grupos.values()].sort((a,b)=>a.brand.localeCompare(b.brand)||a.red.localeCompare(b.red));
}

export async function read(dataDir:string){
 const motivo:string[]=[];
 let maquina:{items:Item[];proyectos:Set<string>}={items:[],proyectos:new Set()};
 try{maquina=machine();if(DB&&!fs.existsSync(DB))motivo.push('No encuentro la base de datos de la máquina de contenido.');}catch(e){motivo.push('No he podido leer la máquina de contenido: '+(e instanceof Error?e.message:String(e)).slice(0,120));}
 let plan:Item[]=[];
 const file=await planFile();
 if(file){let reels:string[]=[];try{reels=(await fs.promises.readdir(REELS_DIR)).filter(f=>f.endsWith('.mp4'));}catch{}
  plan=parsePlan(await fs.promises.readFile(file,'utf8').catch(()=>''),reels);}
 const sueltos=await looseReels(maquina.proyectos);
 const agenda=await readJson<Agenda>(agendaFile(dataDir),{});
 for(const it of [...plan,...sueltos]){
  const a=agenda[it.id];if(!a)continue;
  if(a.fecha){it.fecha=a.fecha;if(it.estado==='sin-fecha'){it.estado='tu';it.detalle='Lo subes tú';}}
  if(a.publicado){it.estado='publicado';it.detalle='Publicado';it.url=a.url||'';}
 }
 // The machine never publishes by itself: an old slot of it that didn't go out is history, not a promise (Owner 29/10).
 const hoyIso=madrid(new Date().toLocaleDateString('sv-SE',{timeZone:TZ}),'00:00');
 const maquinaVigente=maquina.items.filter(i=>i.estado==='publicado'||!i.fecha||i.fecha>=hoyIso);
 const items=[...await calendario(dataDir),...maquinaVigente,...plan,...sueltos.filter(i=>!agenda[i.id]?.oculto)];
 items.push(...await ritmo(dataDir,items));
 const medidas=await readJson<Record<string,Medida>>(medidasFile(dataDir),{});
 const cuentas=await perfiles(dataDir);
 const ahora=Date.now();
 for(const it of items){
  const base=cuentas.find(p=>p.brand===it.brand&&p.red===it.canal&&baseEnlaceValida(p.enlace))?.enlace.trim()
   ||cuentas.find(p=>p.brand===it.brand&&baseEnlaceValida(p.enlace))?.enlace.trim()||'';
  it.medida=medidas[it.id]||null;it.porMedir=porMedir(it,ahora);it.enlace=enlaceMedido(it,base);
 }
 await addCovers(items);
 const desde=ahora-14*86400_000;
 const visibles=items.filter(i=>i.porMedir||!i.fecha||Date.parse(i.fecha)>=desde||i.estado!=='publicado'&&Date.parse(i.fecha)>=ahora-30*86400_000)
  .sort((a,b)=>(a.fecha?Date.parse(a.fecha):9e15)-(b.fecha?Date.parse(b.fecha):9e15));
 return {disponible:items.length>0,motivo:motivo.join(' '),plan:file?path.relative(VAULT,file):'',items:visibles,perfiles:cuentas,marcas:marcas(),porRed:porRed(visibles),analisis:await analisis(dataDir),analisisFecha:await fechaAnalisis(dataDir)};
}

// ---------- the reel rhythm in the calendar ----------
// The plan's backbone (Owner, 22/10): every account, every day, 3 Trial Reels at 10:30, 3 at 17:30 and the good reel at 20:00.
// The calendar shows those slots for today and the next six days, whatever the machine has or hasn't delivered yet, so a missing
// reel is visible instead of silently replaced by the rest of the plan.
const RITMO_DIAS=7;
async function ritmo(dataDir:string,base:Item[]):Promise<Item[]>{
 if(!DAY_DIR||!Object.keys(PREFIX).length)return [];
 const out:Item[]=[],hoy=new Date().toLocaleDateString('sv-SE',{timeZone:TZ});
 const agenda=await readJson<Agenda>(agendaFile(dataDir),{});
 // The good reel: with yesterday's winner it can be marked as published (in agenda.json, like plan and loose reels) and measured.
 const bueno=(pre:string,brand:string,fecha:string,g:{titulo:string}|null,marcable:boolean):Item=>{
  const id=`ritmo:${pre}-2000:${fecha}`,a=marcable?agenda[id]:undefined;
  const st:{estado:Item['estado'];detalle:string}=a?.publicado?{estado:'publicado',detalle:'Publicado'}:g?{estado:'tu',detalle:'Lo subes tú a la cuenta principal'}
   :fecha<=hoy?{estado:'falta',detalle:'Falta elegir el ganador de ayer'}:{estado:'plan',detalle:'Sale del ganador del día anterior'};
  return {id,fuente:'ritmo',brand,marca:BRANDS[brand],canal:'instagram',tipo:'Reel bueno',fecha:madrid(fecha,'20:00'),
   titulo:g?`El ganador de ayer: «${g.titulo}»`:'Reel bueno de las 20:00',gancho:'',texto:'',nota:'El que mejor funcionó en los Trial Reels del día anterior',
   ...st,url:a?.publicado?a.url||'':'',enlace:'',medida:null,porMedir:false,videos:[],portadas:[],elegido:0,horaEditable:false,marcable};
 };
 // Past days: only the good reels marked as published in the last 14 days, so they can still be measured (never past Trial Reels).
 const desde=new Date(Date.now()-14*86400_000).toLocaleDateString('sv-SE',{timeZone:TZ});
 for(const [id,a] of Object.entries(agenda)){
  const m=id.match(RITMO_BUENO);if(!m||!esReelBueno(id)||!a?.publicado||m[2]<desde||m[2]>=hoy)continue;
  const fecha=m[2],brand=PREFIX[m[1]],antes=new Date(Date.parse(fecha+'T12:00:00Z')-86400_000).toISOString().slice(0,10);
  const g=(await readJson<Record<string,{titulo:string}>>(path.join(DAY_DIR,antes,'ganadores.json'),{}))[brand]||null;
  out.push(bueno(m[1],brand,fecha,g,true));
 }
 for(let n=0;n<RITMO_DIAS;n++){
  const fecha=new Date(Date.now()+n*86400_000).toLocaleDateString('sv-SE',{timeZone:TZ});
  let d;try{d=await dia(dataDir,fecha,base);}catch{continue;}
  for(const c of d.cuentas){
   const pre=Object.entries(PREFIX).find(([,b])=>b===c.brand)?.[0];if(!pre||!TRIAL.has(c.brand))continue;
   for(const slot of SLOTS){
    const f=c.franjas.find(x=>x.name===`${pre}-${slot}`),hora=slot.slice(0,2)+':'+slot.slice(2);
    const st:{estado:Item['estado'];detalle:string}=f?.estado==='subido'?{estado:'publicado',detalle:'Subidos'}
     :f?.estado==='listo'?{estado:'tu',detalle:`Listo: ${f.aperturas.length} aperturas para subir como Trial Reels`}
     :f?.estado==='haciendose'?{estado:'programado',detalle:'La máquina lo está haciendo'}
     :f?.estado==='en-cola'?{estado:'programado',detalle:'En la cola de la noche'}
     :fecha<=hoy?{estado:'falta',detalle:'Falta: la máquina no lo ha entregado'}:{estado:'plan',detalle:'Lo prepara la máquina la noche antes'};
    out.push({id:`ritmo:${pre}-${slot}:${fecha}`,fuente:'ritmo',brand:c.brand,marca:c.marca,canal:'instagram',tipo:'Trial Reels',fecha:madrid(fecha,hora),
     titulo:f?.titulo&&f.titulo!==f.name?f.titulo:`Trial Reels de las ${hora}`,gancho:'',texto:f?.aperturas[0]?.texto||'',nota:'3 aperturas como Trial Reels en Instagram: las subes tú. Lo que va a otras redes está en el calendario de publicación.',
     ...st,url:'',enlace:'',medida:null,porMedir:false,videos:(f?.aperturas||[]).map(a=>({nombre:path.basename(a.video,'.mp4'),path:a.video})),portadas:[],elegido:0,horaEditable:false,marcable:false});
   }
  }
 }
 return out;
}

// ---------- changes ----------
export async function medir(dataDir:string,id:string,m:any){
 if(!/^(rf|plan|fv):/.test(id)&&!esReelBueno(id)&&!(id.startsWith('cal:')&&(await calendario(dataDir)).some(it=>it.id===id&&it.estado==='publicado')))throw new Error('No sé qué es eso.');
 const valores=['vistas','visitas','clics','compras'].map(k=>m?.[k]);
 if(valores.some(v=>typeof v!=='number'&&(typeof v!=='string'||!/^\d+$/.test(v.trim()))))throw new Error('Las cifras tienen que ser números enteros, sin decimales ni negativos.');
 const cifras=valores.map(Number);
 if(cifras.some(n=>!Number.isInteger(n)||n<0||n>1000000000))throw new Error('Las cifras tienen que ser números enteros, sin decimales ni negativos.');
 const [vistas,visitas,clics,compras]=cifras;
 const medida:Medida={vistas,visitas,clics,compras,hora:new Date().toISOString()};
 const medidas=await readJson<Record<string,Medida>>(medidasFile(dataDir),{});
 medidas[id]=medida;await writeJson(medidasFile(dataDir),medidas);return {ok:true,medida};
}
export async function reprogramar(dataDir:string,id:string,fecha:string){
 const t=Date.parse(fecha);if(!Number.isFinite(t))throw new Error('Fecha no válida.');
 const iso=new Date(t).toISOString();
 if(id.startsWith('rf:')){
  const db=open(true);if(!db)throw new Error('No encuentro la máquina de contenido.');
  try{const r=db.prepare("update publications set publish_at=?,updated_at=datetime('now') where id=? and status in ('queued','blocked','failed')").run(iso,Number(id.slice(3)));
   if(!r.changes)throw new Error('Esa publicación ya ha salido hacia PostOnce o está publicada: su hora no se cambia aquí.');}finally{db.close();}
  return {ok:true};
 }
 if(!/^(plan|fv):/.test(id))throw new Error('No sé qué es eso.');
 const a=await readJson<Agenda>(agendaFile(dataDir),{});a[id]={...a[id],fecha:iso};await writeJson(agendaFile(dataDir),a);return {ok:true};
}
export async function ocultar(dataDir:string,id:string){
 if(!id.startsWith('fv:'))throw new Error('Solo se ocultan reels sin fecha.');
 const a=await readJson<Agenda>(agendaFile(dataDir),{});a[id]={...a[id],oculto:true};await writeJson(agendaFile(dataDir),a);return {ok:true};
}
export async function marcar(dataDir:string,id:string,publicado:boolean,url=''){
 url=/^https?:\/\//.test(url)?url.slice(0,500):'';
 if(id.startsWith('rf:')){
  const db=open(true);if(!db)throw new Error('No encuentro la máquina de contenido.');
  try{
   const pub=db.prepare('select u.id,u.piece_id,u.status,p.meta from publications u join pieces p on p.id=u.piece_id where u.id=?').get(Number(id.slice(3))) as any;
   if(!pub)throw new Error('Esa publicación ya no existe.');
   if(pub.status==='sent')throw new Error('Está en PostOnce: se marcará sola cuando salga.');
   if(publicado){
    db.prepare("update publications set status='published',url=coalesce(?,url),error=null,provider=coalesce(provider,'manual'),updated_at=datetime('now') where id=?").run(url||null,pub.id);
    db.prepare("update pieces set status='published',updated_at=datetime('now') where id=?").run(pub.piece_id);
   }else{
    let manual=false;try{manual=JSON.parse(pub.meta||'{}')?.fv?.state==='manual';}catch{}
    db.prepare("update publications set status=?,error=?,updated_at=datetime('now') where id=?").run(manual?'blocked':'queued',manual?'Se publica a mano: la máquina no lo sube':null,pub.id);
    db.prepare("update pieces set status='scheduled',updated_at=datetime('now') where id=?").run(pub.piece_id);
   }
  }finally{db.close();}
  return {ok:true};
 }
 if(!/^(plan|fv):/.test(id)&&!esReelBueno(id))throw new Error('No sé qué es eso.');
 const a=await readJson<Agenda>(agendaFile(dataDir),{});const cur={...a[id]};
 if(publicado){cur.publicado=new Date().toISOString();if(url)cur.url=url;}else{delete cur.publicado;delete cur.url;}
 a[id]=cur;await writeJson(agendaFile(dataDir),a);return {ok:true};
}

// ---------- profiles ----------
// Each account's profiles come from its marca in the configuration; edits are kept per copy in perfiles.json.
const PERFILES:Perfil[]=MARCAS.flatMap(m=>m.perfiles.filter((p:any)=>p&&typeof p.red==='string').map((p:any)=>({brand:m.id,red:p.red,usuario:String(p.usuario||''),nombre:String(p.nombre||m.nombre),bio:String(p.bio||''),enlace:String(p.enlace||''),nota:String(p.nota||'')})));
const perfilesFile=(dataDir:string)=>path.join(workspace(dataDir),'perfiles.json');
export async function perfiles(dataDir:string):Promise<Perfil[]>{
 const saved=await readJson<Perfil[]>(perfilesFile(dataDir),[]);
 return PERFILES.map(p=>({...p,...(saved.find(s=>s.brand===p.brand&&s.red===p.red)||{})}));
}
export async function guardarPerfil(dataDir:string,p:Partial<Perfil>){
 const base=PERFILES.find(x=>x.brand===p.brand&&x.red===p.red);if(!base)throw new Error('Ese perfil no existe.');
 const clean=(s:unknown,n:number)=>String(s??'').slice(0,n);
 const saved=(await readJson<Perfil[]>(perfilesFile(dataDir),[])).filter(s=>!(s.brand===p.brand&&s.red===p.red));
 const next:Perfil={...base,usuario:clean(p.usuario??base.usuario,40).replace(/^@/,''),nombre:clean(p.nombre??base.nombre,64),bio:clean(p.bio??base.bio,160),enlace:clean(p.enlace??base.enlace,300),nota:base.nota};
 saved.push(next);await writeJson(perfilesFile(dataDir),saved);return next;
}

// ---------- media (video and covers), only from the content folders ----------
export async function media(p:string):Promise<{file:string;size:number;type:string}|null>{
 if(!p||!/\.(mp4|mov|jpg|jpeg|png|webp)$/i.test(p))return null;
 let real:string;try{real=await fs.promises.realpath(p);}catch{return null;}
 const roots=await Promise.all(MEDIA_ROOTS().map(r=>fs.promises.realpath(r).catch(()=>'')));
 if(!roots.some(r=>r&&real.startsWith(r+path.sep)))return null;
 const st=await fs.promises.stat(real).catch(()=>null);if(!st?.isFile())return null;
 const ext=path.extname(real).toLowerCase();
 return {file:real,size:st.size,type:ext==='.mp4'?'video/mp4':ext==='.mov'?'video/quicktime':ext==='.png'?'image/png':ext==='.webp'?'image/webp':'image/jpeg'};
}

// ---------- the day of the plan: 6 Trial Reels and 1 good reel per account ----------
// Trial Reels: one video per slot (10:30, 17:30) with 3 openings, delivered by the content system to Reels/<fecha>/.
// Their state comes from the machine (queued, running, rendered); «subido» and the views of each opening are noted here
// (until they upload and measure themselves); the winner of each account is written next to the videos as ganadores.json,
// where the content system and the owner's iPhone see it, and becomes the next day's good reel.
const PREFIX:Record<string,string>=Object.fromEntries(MARCAS.filter(m=>m.prefijo).map(m=>[m.prefijo,m.id]));
const TRIAL=new Set(MARCAS.filter(m=>m.trial).map(m=>m.id));
const SLOT_NAME=new RegExp(`^(${Object.keys(PREFIX).join('|')||'(?!)'})-(1030|1730)$`);
const OPENING_NAME=new RegExp(`^(${Object.keys(PREFIX).join('|')||'(?!)'})-(1030|1730)-h[123]$`);
const SLOTS=['1030','1730'];
type DiaNotas={subido?:Record<string,string>;vistas?:Record<string,number>};
const notasFile=(dataDir:string)=>path.join(workspace(dataDir),'dia.json');
export type Apertura={h:string;video:string;texto:string;vistas:number|null};
export type Franja={name:string;brand:string;hora:string;titulo:string;estado:'listo'|'haciendose'|'en-cola'|'sin-video'|'subido';aperturas:Apertura[];subido:string|null};
const validDay=(f:string)=>/^\d{4}-\d{2}-\d{2}$/.test(f);
export async function dia(dataDir:string,fecha:string,base?:Item[]){
 if(!validDay(fecha))throw new Error('Fecha no válida.');
 const dir=path.join(DAY_DIR,fecha);
 let files:string[]=[];if(DAY_DIR)try{files=await fs.promises.readdir(dir);}catch{}
 const piezas=new Map<string,{titulo:string;estado:string}>();
 const db=open();
 if(db){try{
  for(const r of db.prepare("select title,meta from pieces where json_extract(meta,'$.deliver.date')=?").all(fecha) as any[]){
   let m:any={};try{m=JSON.parse(r.meta||'{}');}catch{}
   if(m.deliver?.name)piezas.set(m.deliver.name,{titulo:r.title||'',estado:m.fv?.state||'queued'});
  }}finally{db.close();}}
 const notas=(await readJson<Record<string,DiaNotas>>(notasFile(dataDir),{}))[fecha]||{};
 const ganadores=DAY_DIR?await readJson<Record<string,{name:string;h:string;titulo:string;vistas:number|null}>>(path.join(dir,'ganadores.json'),{}):{};
 const cuentas=[] as {brand:string;marca:string;franjas:Franja[]}[];
 for(const [pre,brand] of Object.entries(PREFIX)){
  if(!TRIAL.has(brand))continue;   // no Trial Reels in this account: its reels live in the publishing calendar
  const franjas:Franja[]=[];
  for(const slot of SLOTS){
   const name=`${pre}-${slot}`,p=piezas.get(name);
   const aperturas:Apertura[]=[];
   for(const h of ['h1','h2','h3']){
    const mp4=`${name}-${h}.mp4`;if(!files.includes(mp4))continue;
    const texto=await fs.promises.readFile(path.join(dir,`${name}-${h}.txt`),'utf8').catch(()=>'');
    aperturas.push({h,video:path.join(dir,mp4),texto:texto.trim(),vistas:notas.vistas?.[`${name}-${h}`]??null});
   }
   if(!aperturas.length&&!p)continue;
   const subido=notas.subido?.[name]||null;
   const first=(aperturas[0]?.texto.split('\n').map(l=>l.trim()).find(l=>l&&!/^comenta\b/i.test(l))||name);
   const titulo=p?.titulo||((first.match(/^.{12,80}?[.!?](\s|$)/)?.[0]||first).trim().replace(/[.]$/,'').slice(0,80));
   const estado:Franja['estado']=subido?'subido':aperturas.length?'listo':p?.estado==='running'?'haciendose':p?'en-cola':'sin-video';
   franjas.push({name,brand,hora:slot.slice(0,2)+':'+slot.slice(2),titulo,estado,aperturas,subido});
  }
  cuentas.push({brand,marca:BRANDS[brand],franjas});
 }
 // The good reel at 20:00: whatever the calendar has for that day at 20:00 in Instagram.
 const todo=base?{items:base}:await read(dataDir);
 const buenos=todo.items.filter(i=>i.fecha&&i.canal==='instagram'&&new Date(i.fecha).toLocaleDateString('sv-SE',{timeZone:TZ})===fecha&&new Date(i.fecha).toLocaleTimeString('es-ES',{timeZone:TZ,hour:'2-digit',minute:'2-digit'})==='20:00');
 return {fecha,cuentas:cuentas.map(c=>({...c,bueno:buenos.find(i=>i.brand===c.brand)||null,ganador:ganadores[c.brand]||null}))};
}
export async function marcarSubido(dataDir:string,fecha:string,name:string,subido:boolean){
 if(!validDay(fecha)||!SLOT_NAME.test(name))throw new Error('Franja no válida.');
 const all=await readJson<Record<string,DiaNotas>>(notasFile(dataDir),{});const d=all[fecha]||{};d.subido={...d.subido};
 if(subido)d.subido[name]=new Date().toISOString();else delete d.subido[name];
 all[fecha]=d;await writeJson(notasFile(dataDir),all);return {ok:true};
}
export async function apuntarVistas(dataDir:string,fecha:string,clave:string,vistas:number|null){
 if(!validDay(fecha)||!OPENING_NAME.test(clave))throw new Error('Apertura no válida.');
 if(vistas!==null&&!(Number.isInteger(vistas)&&vistas>=0&&vistas<1e9))throw new Error('Escribe las visitas como un número.');
 const all=await readJson<Record<string,DiaNotas>>(notasFile(dataDir),{});const d=all[fecha]||{};d.vistas={...d.vistas};
 if(vistas===null)delete d.vistas[clave];else d.vistas[clave]=vistas;
 all[fecha]=d;await writeJson(notasFile(dataDir),all);return {ok:true};
}
/** The proposed winner of an account for a day: the opening with most views among its Trial Reels. */
export function propuesto(c:{franjas:Franja[]}){
 let best:{name:string;h:string;titulo:string;vistas:number}|null=null;
 for(const f of c.franjas)for(const a of f.aperturas)if(a.vistas!=null&&(!best||a.vistas>best.vistas))best={name:f.name,h:a.h,titulo:f.titulo,vistas:a.vistas};
 return best;
}
export async function elegirGanador(dataDir:string,fecha:string,brand:string,name:string,h:string){
 if(!DAY_DIR)throw new Error('La carpeta de los vídeos del día no está configurada.');
 if(!validDay(fecha)||!BRANDS[brand]||!SLOT_NAME.test(name)||!/^h[123]$/.test(h)||PREFIX[name.split('-')[0]]!==brand)throw new Error('Ganador no válido.');
 const d=await dia(dataDir,fecha),f=d.cuentas.find(c=>c.brand===brand)?.franjas.find(x=>x.name===name),a=f?.aperturas.find(x=>x.h===h);
 if(!f||!a)throw new Error('Ese vídeo no está en la carpeta del día.');
 const file=path.join(DAY_DIR,fecha,'ganadores.json'),g=await readJson<Record<string,unknown>>(file,{});
 g[brand]={name,h,titulo:f.titulo,vistas:a.vistas,video:path.basename(a.video),elegido:new Date().toISOString(),por:profile.load().name};
 await writeJson(file,g);return {ok:true};
}

// ---------- 19:30: the winner of the day, proposed by Telegram ----------
// Once the Trial Reels have had their day, each account's best opening (by the views noted in Contenido → Hoy) is proposed
// as tomorrow's good reel, with «Sí, esa» (the same as choosing it in the app). Without views, a reminder to note them.
// Each account hears once a day; a reminder can still be followed by the proposal once the views are in.
export const HORA_GANADOR='19:30';
const avisosFile=(dataDir:string)=>path.join(workspace(dataDir),'avisos.json');
type Aviso={brand:string;texto:string;botones?:{text:string;callback_data:string}[][]};
export const BOTON_GANADOR='w:';
export async function avisosGanador(dataDir:string,fecha:string):Promise<Aviso[]>{
 const d=await dia(dataDir,fecha),sent=(await readJson<Record<string,Record<string,string>>>(avisosFile(dataDir),{}))[fecha]||{},out:Aviso[]=[];
 for(const c of d.cuentas){
  if(c.ganador||sent[c.brand]==='propuesto')continue;
  const tiene=c.franjas.some(f=>f.aperturas.length);if(!tiene)continue;
  const p=propuesto(c);
  if(p){
   const n=Number(p.h.slice(1)),hora=p.name.slice(-4,-2)+':'+p.name.slice(-2);
   out.push({brand:c.brand,texto:`🏆 ${c.marca}: hoy ganó la apertura ${n} de las ${hora}, «${p.titulo}» (${p.vistas.toLocaleString('es-ES')} visitas). ¿Es el reel bueno de mañana?`,
    botones:[[{text:'Sí, esa',callback_data:`${BOTON_GANADOR}${p.name}:${p.h}`},{text:'Ahora no',callback_data:`${BOTON_GANADOR}no`}]]});
  }else if(!sent[c.brand])out.push({brand:c.brand,texto:`📊 ${c.marca}: apunta las visitas de los Trial Reels de hoy en Contenido → Hoy y te digo cuál gana.`});
 }
 return out;
}
export async function marcarAviso(dataDir:string,fecha:string,a:Aviso){
 const all=await readJson<Record<string,Record<string,string>>>(avisosFile(dataDir),{});
 for(const f of Object.keys(all))if(f<fecha&&Object.keys(all).length>14)delete all[f];
 all[fecha]={...all[fecha],[a.brand]:a.botones?'propuesto':'visitas'};await writeJson(avisosFile(dataDir),all);
}
/** «w:kc-1730:h2» → the account, slot and opening to choose; «w:no» → not now. */
export function ganadorBoton(data:string):{brand:string;name:string;h:string}|'no'|null{
 if(data===BOTON_GANADOR+'no')return 'no';
 const m=data.match(/^w:([a-z0-9]+-(?:1030|1730)):(h[123])$/);if(!m||!SLOT_NAME.test(m[1]))return null;
 const brand=PREFIX[m[1].split('-')[0]];return brand?{brand,name:m[1],h:m[2]}:null;
}
