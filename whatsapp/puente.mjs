#!/usr/bin/env node
// Alfred en WhatsApp: a linked device of Alfred's own number (not Owner's: if WhatsApp ever blocks an unofficial device,
// only that number is lost). It listens to one group (configuracion.json → whatsapp.grupo, «Alfred» by default) where
// Owner and Alfred's number are, and only to Owner. What it does with each message is in logica.mjs.
//
//   node puente.mjs vincular [+34…]   once, in a terminal: QR (or a code for «Vincular con número de teléfono»)
//   node puente.mjs                   the service (launchd com.prompted.os.whatsapp, whatsapp/instalar.sh)
//
// The session lives in Workspace/whatsapp/sesion (mode 700). Nothing is ever sent outside that group.
// It also delivers what the app and the team send Owner (Workspace/whatsapp/salida, see avisos.mjs): buttons become polls and
// each vote goes back to the app as the same button tap as in Telegram (kept for later if the app is closed). estado.json
// says it is connected, so the app and the engine send here instead of Telegram; if it stops, they go back to Telegram.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {execFile} from 'node:child_process';
import {randomBytes} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import makeWASocket,{useMultiFileAuthState,DisconnectReason,Browsers,downloadMediaMessage,normalizeMessageContent,jidNormalizedUser,fetchLatestBaileysVersion,decryptPollVote,BufferJSON,S_WHATSAPP_NET} from '@whiskeysockets/baileys';
import pino from 'pino';
import QRCode from 'qrcode';
import {crearPuente,colaEnCarpeta,deConexion} from './logica.mjs';
import {plan,voto,agentBar,agentBarAviso} from './avisos.mjs';

const AQUI=path.dirname(fileURLToPath(import.meta.url));
const DATA=process.env.PROMPTED_OS_DATA_DIR||path.join(os.homedir(),'Library/Application Support/com.prompted.os/Workspace');
const CARPETA=path.join(DATA,'whatsapp'),SESION=path.join(CARPETA,'sesion'),VISTOS=path.join(CARPETA,'vistos.json'),REGISTRO=path.join(CARPETA,'registro.log'),GRUPO_ID=path.join(CARPETA,'grupo.json');
const COLA=path.join(CARPETA,'cola'),SALIDA=path.join(CARPETA,'salida'),ENCUESTAS=path.join(CARPETA,'encuestas.json'),MENSAJES=path.join(CARPETA,'mensajes.json'),ESTADO=path.join(CARPETA,'estado.json'),PENDIENTES=path.join(CARPETA,'botones-pendientes.jsonl');
const leer=(f,d)=>{try{return JSON.parse(fs.readFileSync(f,'utf8'));}catch{return d;}};
const CONFIG=leer(path.join(DATA,'configuracion.json'),{});
const GRUPO=(CONFIG.whatsapp?.grupo||'Alfred').trim();
const PYTHON='/usr/bin/python3',INBOX=path.join(AQUI,'..','inbox','inbox.py');
const logger=pino({level:'silent'});
let ADJUNTOS='';
fs.mkdirSync(CARPETA,{recursive:true,mode:0o700});

function log(t){fs.appendFileSync(REGISTRO,`${new Date().toISOString().slice(0,19)} ${t}\n`);}
function py(args,entrada,timeout=600_000){
 return new Promise((resolve,reject)=>{const p=execFile(PYTHON,[INBOX,...args],{timeout,maxBuffer:4_000_000,env:{...process.env,PROMPTED_OS_DATA_DIR:DATA}},(e,out)=>{if(e)return reject(e);try{resolve(JSON.parse(out));}catch(x){reject(x);}});if(entrada!==undefined)p.stdin.end(JSON.stringify(entrada));});
}

// A video link → its «Para rehacer» note (inbox/enlaces.py, with the team's Python: it analyses through equipo.claude).
const EQUIPO_PY=process.env.PROMPTED_OS_EQUIPO_PYTHON||[path.join(os.homedir(),'Equipo/venv/bin/python')].find(f=>fs.existsSync(f))||PYTHON;
function enlace(url,id,pedido=''){
 return new Promise((resolve)=>execFile(EQUIPO_PY,[path.join(AQUI,'..','inbox','enlaces.py'),'procesar',url,id,...(pedido?[pedido]:[])],{timeout:30*60_000,maxBuffer:4_000_000,env:{...process.env,PROMPTED_OS_DATA_DIR:DATA}},
  (e,out)=>{try{resolve(JSON.parse(String(out).trim().split('\n').pop()));}catch{resolve({error:e?(e.killed?'ha tardado demasiado':'ha fallado'):'respuesta ilegible'});}}));
}

// A PDF → read and filed in its project (inbox/documentos.py, same Python and same way out as the videos).
function documento(archivo,id,nombre='',pedido=''){
 return new Promise((resolve)=>execFile(EQUIPO_PY,[path.join(AQUI,'..','inbox','documentos.py'),'procesar',archivo,id,nombre,...(pedido?[pedido]:[])],{timeout:10*60_000,maxBuffer:4_000_000,env:{...process.env,PROMPTED_OS_DATA_DIR:DATA}},
  (e,out)=>{try{resolve(JSON.parse(String(out).trim().split('\n').pop()));}catch{resolve({error:e?(e.killed?'ha tardado demasiado':'ha fallado'):'respuesta ilegible'});}}));
}

// ---------- Alfred, through the app (its server writes Workspace/whatsapp/llave when it starts) ----------
function dormido(){const e=Error('la app está cerrada');e.dormido=true;return e;}
async function app(ruta,cuerpo){
 const port=leer(path.join(DATA,'runtime.json'),{}).port;let llave='';
 try{llave=fs.readFileSync(path.join(CARPETA,'llave'),'utf8').trim();}catch{}
 if(!port||!llave)throw dormido();
 let r;
 try{r=await fetch(`http://127.0.0.1:${port}${ruta}`,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+llave},body:JSON.stringify(cuerpo)});}
 catch{throw dormido();}
 if(r.status===401||r.status===403)throw dormido();   // another run of the app, with another key: it will write its own
 if(!r.ok)throw Error('la app ha respondido '+r.status);
 return r;
}
async function alfred(texto,alEvento){
 const r=await app('/api/whatsapp/alfred',{texto});
 const dec=new TextDecoder();let resto='';
 for await(const trozo of r.body){
  resto+=dec.decode(trozo,{stream:true});let i;
  while((i=resto.indexOf('\n'))>=0){const linea=resto.slice(0,i).trim();resto=resto.slice(i+1);if(linea)await alEvento(JSON.parse(linea));}
 }
}
/** 05/11, Owner: «quiero poder hablar con él normal». With the app closed, open it in the background (`open -g`: no window in front;
 *  the ritual only starts with two snaps) and wait until its server answers with a fresh key. → true when Alfred is awake. */
async function despertar(){
 const desde=Date.now();log('app cerrada: la abro para Alfred');
 // by path, never by bundle id: 06/11 «-b» opened Codex's hidden backup (/Applications/.Prompted OS.before-codex-…app),
 // which never writes the key, and Alfred said he could not open the app
 execFile('open',['-g',CONFIG.whatsapp?.app||'/Applications/Prompted OS.app'],(e)=>{if(e)log('no he podido abrir la app: '+e.message);});
 while(Date.now()-desde<120_000){
  await new Promise(r=>setTimeout(r,2000));
  const port=leer(path.join(DATA,'runtime.json'),{}).port;
  let fresca=false;try{fresca=fs.statSync(path.join(CARPETA,'llave')).mtimeMs>=desde-1000;}catch{}
  if(!port||!fresca)continue;
  try{await fetch(`http://127.0.0.1:${port}/`);await new Promise(r=>setTimeout(r,1500));return true;}catch{}
 }
 log('la app no ha arrancado en 2 minutos');return false;
}
const permiso=(id,si)=>void app('/api/whatsapp/permiso',{id,si}).catch(e=>log('permiso no enviado: '+e.message));
const permisoTarde=(id)=>app('/api/whatsapp/permiso-tarde',{id}).then(r=>r.json()).then(j=>j?.ok===true).catch(e=>{log('permiso tardío no enviado: '+e.message);return false;});
const ordenApp=(texto,ref)=>app('/api/whatsapp/orden',{texto,...(ref?{ref}:{})}).then(()=>{});
/** A vote = a button tap. With the app closed it waits in botones-pendientes.jsonl; the app does them when it opens. */
async function boton(data,ref,avisar){
 const ab=agentBar(data);   // a vote on an Agent Bar alert: straight to that Claude Code conversation
 if(ab){if(avisar)await avisar(agentBarAviso[ab]);return;}
 try{await app('/api/whatsapp/boton',{data,ref});}
 catch(e){if(!e.dormido)return log('voto no enviado: '+e.message);
  fs.appendFileSync(PENDIENTES,JSON.stringify({data,ref,hora:new Date().toISOString()})+'\n',{mode:0o600});
  if(avisar)await avisar('Apuntado. Lo hago en cuanto abras la app del Mac.');}
}

// ---------- what the app and the team send (Workspace/whatsapp/salida) ----------
const guardarJSON=(f,v)=>fs.writeFileSync(f,JSON.stringify(v),{mode:0o600});
const marcarEstado=(conectado)=>guardarJSON(ESTADO,{conectado,hora:new Date().toISOString(),agentBar:true});   // agentBar: this bridge answers Agent Bar alerts
let encuestas=leer(ENCUESTAS,{}),mensajes=leer(MENSAJES,{}),enviando=false;
function podar(){
 const limite=Date.now()-14*86400e3;
 for(const [id,r] of Object.entries(encuestas))if(Date.parse(r.creado||0)<limite)delete encuestas[id];
 for(const [id,r] of Object.entries(mensajes))if(Date.parse(r.creado||0)<limite)delete mensajes[id];
}
async function repartir(sock,grupo){
 if(enviando||!grupo())return;enviando=true;
 try{
  fs.mkdirSync(SALIDA,{recursive:true,mode:0o700});
  for(const nombre of fs.readdirSync(SALIDA).filter(f=>f.endsWith('.json')).sort()){
   const f=path.join(SALIDA,nombre);let e;
   try{e=JSON.parse(fs.readFileSync(f,'utf8'));}catch{continue;}   // still being written
   const p=plan(e),creado=new Date().toISOString(),ids=[];
   try{
    if(p.texto)ids.push((await sock.sendMessage(grupo().id,{text:p.texto})).key.id);
    if(p.encuesta){
     const secreto=randomBytes(32),r=await sock.sendMessage(grupo().id,{poll:{...p.encuesta,messageSecret:secreto}});
     encuestas[r.key.id]={secreto:secreto.toString('base64'),opciones:p.opciones,lista:!!p.lista,ref:e.ref||null,seleccion:[],creado};ids.push(r.key.id);
    }
   }catch(err){
    log('aviso no enviado: '+err.message);
    if(Date.now()-fs.statSync(f).mtimeMs>86400e3)fs.rmSync(f,{force:true});   // a day late is no longer news
    break;
   }
   for(const id of ids)mensajes[id]={ref:e.ref||null,responder:!!e.responder,creado};
   podar();guardarJSON(ENCUESTAS,encuestas);guardarJSON(MENSAJES,mensajes);fs.rmSync(f,{force:true});
  }
 }finally{enviando=false;}
}
/** A vote on one of Alfred's polls: decrypted with the poll's own secret (WhatsApp signs it with whichever ids it used then). */
async function votar(sock,grupo,msg,enviar){
 const pu=msg.message.pollUpdateMessage,id=pu.pollCreationMessageKey?.id,reg=id&&encuestas[id];
 if(!reg||!pu.vote)return;
 const creadores=[sock.user?.id,sock.user?.lid].filter(Boolean).map(jidNormalizedUser);
 const votantes=[msg.key.participant,msg.key.participantAlt].filter(Boolean).map(jidNormalizedUser);
 let elegidas=null;
 for(const pollCreatorJid of creadores)for(const voterJid of votantes){
  if(elegidas)break;
  try{elegidas=decryptPollVote(pu.vote,{pollCreatorJid,pollMsgId:id,pollEncKey:Buffer.from(reg.secreto,'base64'),voterJid}).selectedOptions||[];}catch{}
 }
 if(!elegidas)return log('voto que no he podido descifrar');
 const r=voto(reg,elegidas,reg.seleccion);reg.seleccion=r.seleccion;guardarJSON(ENCUESTAS,encuestas);
 for(const data of r.pulsar)await boton(data,reg.ref,reg.lista?null:enviar);
}

// ---------- WhatsApp ----------
let vistos=leer(VISTOS,[]),latido=null;
function visto(id){if(vistos.includes(id))return true;vistos=[...vistos.slice(-499),id];fs.writeFileSync(VISTOS,JSON.stringify(vistos));return false;}

// 08/11: one bridge for the whole run and always the socket of the moment. Before, each reconnection made a new socket while what
// was being processed kept the old, closed one: on 07/11 at 23:30 three texts and a video died with «Connection Closed».
let sockActual=null,abierto=false,grupoActual=null,puente=null,fallos=0;   // fallos: closes in a row without connecting
const esperando=new Set();
const enLinea=()=>abierto&&!!grupoActual;
function avisarEnLinea(){if(enLinea())for(const f of [...esperando])f();}
/** Waits (up to `ms` of the Mac awake: timers stop while it sleeps) until WhatsApp is connected and the group known. */
function esperarConexion(ms=180_000){
 if(enLinea())return Promise.resolve();
 return new Promise((resolve,reject)=>{
  const listo=()=>{clearTimeout(t);esperando.delete(listo);resolve();};
  const t=setTimeout(()=>{esperando.delete(listo);reject(Error('Connection Closed: sin conexión con WhatsApp'));},ms);
  esperando.add(listo);
 });
}
/** fn(socket, group) once connected; if the connection drops right then, again on the next one (twice at most). */
async function conSocket(fn){
 for(let i=0;;i++){
  await esperarConexion();
  try{return await fn(sockActual,grupoActual);}
  catch(e){if(i>=2||!deConexion(e))throw e;await new Promise(r=>setTimeout(r,1000));}
 }
}
// The Mac on battery sleeps and wakes for a few seconds every ~17 min (pmset: DarkWake «SleepService»); WhatsApp has dropped the
// socket meanwhile, but Baileys only notices after 30 s of the Mac awake (its keepalive runs on timers that stop while it sleeps),
// which can take several wakes: the 408 every 15–20 min. When the clock jumps, a ping with a short timeout finds out at once.
function comprobarAlDespertar(sock,dormido){
 sock.query({tag:'iq',attrs:{to:S_WHATSAPP_NET,type:'get',xmlns:'w:p'},content:[{tag:'ping',attrs:{}}]},5000).catch(()=>{
  if(sock!==sockActual)return;
  log(`el Mac ha dormido ${Math.round(dormido/60_000)} min y WhatsApp no contesta: reconecto`);
  sock.end(Object.assign(Error('Connection was lost'),{output:{statusCode:DisconnectReason.connectionLost}}));
 });
}

async function conectar({vincular=false,numero=''}={}){
 const {state,saveCreds}=await useMultiFileAuthState(SESION);
 // 16/11: with a slow network GitHub never answered and the bridge waited forever before connecting; 10 s, then Baileys' own version
 let version;try{({version}=await Promise.race([fetchLatestBaileysVersion(),new Promise((_,no)=>setTimeout(()=>no(Error('tarde')),10_000))]));}catch{}
 const sock=makeWASocket({auth:state,logger,browser:Browsers.macOS('Chrome'),markOnlineOnConnect:false,syncFullHistory:false,...(version?{version}:{})});
 sockActual=sock;abierto=false;
 sock.ev.on('creds.update',saveCreds);
 return new Promise((resolve)=>{
  let pedido=false;
  sock.ev.on('connection.update',async({connection,lastDisconnect,qr})=>{
   if(qr&&vincular){
    if(numero&&!pedido){pedido=true;const c=await sock.requestPairingCode(numero.replace(/\D/g,''));console.log(`\nEn el móvil de Alfred: WhatsApp → Dispositivos vinculados → Vincular con número de teléfono → ${c}\n`);}
    else if(!numero)console.log('\nEscanea con el móvil de Alfred (WhatsApp → Dispositivos vinculados → Vincular un dispositivo):\n\n'+await QRCode.toString(qr,{type:'terminal',small:true}));
   }else if(qr){log('sin vincular: ejecuta «node puente.mjs vincular»');process.exit(0);}
   if(connection==='open'){
    if(vincular){console.log('Vinculado. Ahora instala el servicio: whatsapp/instalar.sh');setTimeout(()=>process.exit(0),3000);return;}
    const g=await buscarGrupo(sock);if(g!==undefined)grupoActual=g;   // WhatsApp not answering keeps the group already known
    if(sock!==sockActual)return;
    abierto=true;fallos=0;
    log(grupoActual?`conectado; escuchando el grupo ${grupoActual.id}`:g===undefined?'conectado; WhatsApp no deja leer los grupos, reintento en 30 s':`conectado, pero no hay ningún grupo llamado «${GRUPO}»`);
    if(g===undefined)setTimeout(async()=>{const otro=await buscarGrupo(sock);if(otro!==undefined){grupoActual=otro;log(grupoActual?`escuchando «${GRUPO}»`:`no hay ningún grupo llamado «${GRUPO}»`);avisarEnLinea();}},30_000);
    marcarEstado(!!grupoActual);clearInterval(latido);
    avisarEnLinea();
    const n=puente.pendientes();if(n)log(`reconectado: reintento ${n} mensaje${n>1?'s':''} en cola`);
    void puente.reanudar({todo:true});   // what failed while the connection was down, or before a restart
    let tic=Date.now(),vuelta=0;
    latido=setInterval(()=>{
     const ahora=Date.now(),dormido=ahora-tic;tic=ahora;
     if(dormido>45_000)comprobarAlDespertar(sock,dormido);
     marcarEstado(!!grupoActual);void repartir(sock,()=>grupoActual).catch(e=>log('salida: '+e.message));
     if(++vuelta%20===0&&enLinea())void puente.reanudar();   // every minute: a retry that is due without a reconnection
    },3000);
   }
   if(connection==='close'){
    if(sock===sockActual)abierto=false;
    clearInterval(latido);if(!vincular)marcarEstado(false);
    const code=lastDisconnect?.error?.output?.statusCode;
    if(code===DisconnectReason.loggedOut){log('WhatsApp ha cerrado la sesión de Alfred: vuelve a vincular');fs.rmSync(SESION,{recursive:true,force:true});process.exit(0);}
    if(vincular&&code===DisconnectReason.restartRequired){resolve(conectar({vincular,numero}));return;}   // normal right after pairing
    // 1 s, not 5: the Mac on battery is only awake for ~8 s at a time, and a reconnection that waits for the next wake is 17 min deaf.
    // Failing again without connecting (no network): 5, 10, 20… s, up to a minute.
    const espera=fallos++===0?1000:Math.min(60_000,5000*2**(fallos-2));
    log('desconectado ('+(code||'sin código')+'), reconecto');setTimeout(()=>resolve(conectar({vincular,numero})),espera);
   }
  });
  // A group renamed or created after start-up is picked up as soon as anything happens in it.
  sock.ev.on('groups.update',async()=>{const g=await buscarGrupo(sock,{siHaceFalta:true});if(g!==undefined){grupoActual=g;avisarEnLinea();}});
  sock.ev.on('messages.upsert',async({messages})=>{
   if(vincular)return;
   for(const msg of messages){
    if(!grupoActual||msg.key.remoteJid!==grupoActual.id){
     if(!grupoActual&&msg.key.remoteJid?.endsWith('@g.us')){const g=await buscarGrupo(sock,{siHaceFalta:true});if(g!==undefined){grupoActual=g;avisarEnLinea();}if(grupoActual?.id!==msg.key.remoteJid)continue;}else continue;
    }
    msg.message=normalizeMessageContent(msg.message);
    if(msg.message?.pollUpdateMessage){
     const d=puente.deps;
     if(!msg.key.fromMe&&d.esDueno(msg.key)&&!visto(msg.key.id))await votar(sock,()=>grupoActual,msg,(t)=>d.enviar(t)).catch(e=>log('voto: '+e.message));
     continue;
    }
    puente.recibir(msg);
   }
  });
 });
}

// undefined: WhatsApp did not answer (03/11: «rate-overlimit» right after linking), so the group already found stays.
// null: there is no group with that name.
let ultimaBusqueda=0;
async function buscarGrupo(sock,{siHaceFalta=false}={}){
 if(siHaceFalta&&Date.now()-ultimaBusqueda<60_000)return undefined;   // groups.update comes in bursts while syncing
 ultimaBusqueda=Date.now();
 let todos={};try{todos=await sock.groupFetchAllParticipating();}catch(e){log('no he podido leer los grupos: '+e.message);return undefined;}
 // the group it already found is kept by its id, so renaming it in WhatsApp doesn't leave Alfred deaf (06/11);
 // the name only finds it the first time
 const recordado=leer(GRUPO_ID,{}).id;
 const g=(recordado&&todos[recordado])||Object.values(todos).find(x=>(x.subject||'').trim().toLowerCase()===GRUPO.toLowerCase());
 if(!g)return null;
 if(g.id!==recordado)try{fs.writeFileSync(GRUPO_ID,JSON.stringify({id:g.id,nombre:g.subject}));}catch{}
 const yo=[sock.user?.id,sock.user?.lid].filter(Boolean).map(jidNormalizedUser);
 const ids=(p)=>[p.id,p.lid,p.phoneNumber].filter(Boolean).map(jidNormalizedUser);
 // Owner: whoever created the group; in a group of two, the other one. Nobody else, even if added later.
 const dueno=new Set([g.owner,g.ownerPn].filter(Boolean).map(jidNormalizedUser).filter(j=>!yo.includes(j)));
 if(!dueno.size&&g.participants.length===2)for(const p of g.participants)if(!ids(p).some(j=>yo.includes(j)))ids(p).forEach(j=>dueno.add(j));
 return {id:g.id,dueno,yo};
}

// Everything that talks to WhatsApp waits for the connection of the moment (conSocket), never a socket that has closed.
function dependencias(){
 return {
  get soy(){return grupoActual?.yo||[];},
  esDueno:(key)=>[key.participant,key.participantAlt].filter(Boolean).map(jidNormalizedUser).some(j=>grupoActual?.dueno.has(j)),
  enviar:(texto,citado)=>conSocket((s,g)=>s.sendMessage(g.id,{text:texto},citado?{quoted:citado}:{})),
  reaccionar:(key,emoji)=>conSocket((s,g)=>s.sendMessage(g.id,{react:{text:emoji,key}})),
  descargar:(msg)=>downloadMediaMessage(msg,'buffer',{},{logger,reuploadRequest:(m)=>sockActual.updateMediaMessage(m)}),
  guardar:(buf,nombre)=>{const dir=ADJUNTOS;fs.mkdirSync(dir,{recursive:true});const f=path.join(dir,nombre);fs.writeFileSync(f,buf);return f;},
  inbox:(datos)=>py(['apuntar'],datos,60_000),
  enlace:(url,id,pedido)=>enlace(url,id,pedido),
  documento:(archivo,id,nombre,pedido)=>documento(archivo,id,nombre,pedido),
  transcribir:(f)=>py(['transcribir',f]).then(r=>r.texto||''),
  alfred,permiso,permisoTarde,visto,log,despertar,
  // a reply quoting an Agent Bar alert goes to that Claude Code conversation; everything else, to the app as before
  orden:(texto,ref)=>{const ab=agentBar(ref,texto);return ab?conSocket((s,g)=>s.sendMessage(g.id,{text:agentBarAviso[ab]})).then(()=>{}):ordenApp(texto,ref);},
  pregunta:(id)=>mensajes[id]?.responder?mensajes[id].ref:null,
  cola:colaEnCarpeta(COLA,{replacer:BufferJSON.replacer,reviver:BufferJSON.reviver}),   // Workspace/whatsapp/cola: survives a restart
 };
}

const [orden,numero]=process.argv.slice(2);
const estado=await py(['--estado']).catch(()=>({}));
if(!estado.adjuntos){log('el inbox no está configurado (configuracion.json → inbox.nota)');if(orden!=='vincular')process.exit(0);}
ADJUNTOS=estado.adjuntos||path.join(CARPETA,'adjuntos');
{const deps=dependencias();puente=crearPuente(deps);puente.deps=deps;}
await conectar({vincular:orden==='vincular',numero});
