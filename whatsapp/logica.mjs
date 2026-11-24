// What the WhatsApp bridge does with each message of Owner's group with Alfred, without WhatsApp itself (puente.mjs wires
// Baileys in; puente-checks.mjs drives this with a fake one).
//   «Alfred, …» (or a reply to one of Alfred's messages)  → Alfred answers in the group, through the app (same brain as Telegram)
//   «sí» / «no» while Alfred waits for a permission          → that permission
//   «/lote», «/hoy»… or a reply to a question Alfred asked    → the same order or answer as in Telegram, through the app
//   any other text, voice note or captioned photo            → Alfred too; if he answers only «📥» it is an idea → the inbox
//   links, files, forwarded messages, «idea: …»              → the ideas inbox (inbox/inbox.py), with 📥 as acknowledgement
//   …and a reel, TikTok or YouTube link is also turned into a «Para rehacer» note (inbox/enlaces.py), answered in one line
// With the app closed, a message for Alfred goes to the inbox too and the group is told he is asleep.
// 08/11: every message waits in a persistent queue (deps.cola) until it is done. What fails because the connection dropped (the
// Mac asleep on battery wakes for ~8 s every ~17 min) is tried again when WhatsApp reconnects; after MAX_INTENTOS, or on any
// other failure, it is written to the inbox as pending with ⚠️ instead of being lost (the 07/11 night: three texts and a video).
import fs from 'node:fs';
import path from 'node:path';

// The same videos inbox/enlaces.py handles (VIDEO there).
export const VIDEO=/https?:\/\/(?:www\.|m\.|vm\.|vt\.)?(?:instagram\.com\/(?:reel|reels|p|tv)\/|tiktok\.com\/|youtube\.com\/(?:shorts\/|watch\?)|youtu\.be\/)[^\s<>"]+/i;
export const PARA_ALFRED=/^\s*@?alfred\b[\s,:.!¡¿?-]*/i;
// 04/11: «Cómo vas Alfred», «?», «Pero no me contestas por aquí o qué» went to the inbox: only a message *starting* with
// «Alfred» reached him. Now his name anywhere does, and for CHARLA minutes after he answers the chat carries on without it.
// «idea: …» or «apunta: …» is always for the inbox; links and files too, unless they name him.
export const MENCIONA=/(^|[^\p{L}])alfred([^\p{L}]|$)/iu;
export const PARA_INBOX=/^\s*(idea|apunta|apúntalo|nota)\b[\s,:.-]*/i;
// 05/11: «Recuérdame que tengo dentista el miércoles a las 9» (nota de voz, sin nombrarle) fue al inbox y nadie lo agendó.
// Pedir un recordatorio, una alarma o algo en el calendario es para Alfred aunque no le nombre.
export const RECADO=/^\s*(recu[eé]rdame|recordadme|av[ií]same|pon(me)?\s+(un\s+|una\s+)?(recordatorio|alarma|aviso)|(a[nñ]ade|mete|pon|ap[uú]nta(me)?)\s+(\S+\s+){0,3}(en\s+)?(el\s+|mi\s+)?(calendario|agenda|recordatorios))/iu;
// 06/11: a question or «contéstame» is for Alfred even without his name (Owner asked what to train at the gym in a voice note and got 📥)
export const PREGUNTA=/[¿?]|^\s*(qu[eé]|c[oó]mo|cu[aá]ndo|d[oó]nde|cu[aá]l(es)?|cu[aá]nto|por\s*qu[eé]|puedes|podr[ií]as|me\s+ayudas)\b|\b(cont[eé]st(a|ame)|resp[oó]nde(me)?|dime)\b/iu;
const CHARLA=10*60;
// (?=…) and not \b: \b does not see the end of «sí» (í is not a word character for JavaScript without the u flag)
// A «sí» after the request ran out only counts when the whole message is that (not «si mañana voy…»).
const SOLO_SI=/^\s*(s[ií]+|sip|vale|ok|okay|adelante|dale|hazlo|permitido)([\s,.!]+(s[ií]|vale|ok|adelante|dale|hazlo|gu[aá]rdalo|ap[uú]ntalo|venga))*[\s.!👍]*$/iu,SOLO_NO=/^\s*(no|nop|cancela|d[eé]jalo)[\s.!]*$/iu;
const TARDE=12*3600;   // seconds a request that ran out stays answerable (voice.ts keeps it as long)
const FIN='(?=$|[\\s,.!¡?¿])',SI=new RegExp('^\\s*(s[ií]|sip|vale|ok|okay|adelante|dale|hazlo|permitido)'+FIN,'i'),NO=new RegExp('^\\s*(no|nop|para|cancela)'+FIN,'i');
const MAX_DIAS=3;
export const MAX_INTENTOS=5;
// Baileys closes with «Connection Closed» (428) or «Connection was lost» (408); a media download fails with «fetch failed»
const CONEXION=/connection (closed|lost|was lost|failure|terminated)|timed? ?out|ECONNRESET|ETIMEDOUT|ECONNREFUSED|ENOTFOUND|EAI_AGAIN|ENETUNREACH|socket hang up|fetch failed|other side closed/i;
export const deConexion=(e)=>!!e&&(e.reintentar===true||[408,428,503,515].includes(e.output?.statusCode)||CONEXION.test(e.message||String(e)));

/** The queue on disk: one file per WhatsApp id in `dir` (puente.mjs passes Baileys' BufferJSON so media keys survive a restart). */
export function colaEnCarpeta(dir,{replacer,reviver}={}){
 const f=(id)=>path.join(dir,String(id).replace(/[^\w.-]/g,'_')+'.json');
 return {
  poner(id,e){fs.mkdirSync(dir,{recursive:true,mode:0o700});const t=f(id)+'.tmp';fs.writeFileSync(t,JSON.stringify(e,replacer),{mode:0o600});fs.renameSync(t,f(id));},
  quitar(id){fs.rmSync(f(id),{force:true});},
  todas(){
   let nombres=[];try{nombres=fs.readdirSync(dir).filter(x=>x.endsWith('.json'));}catch{}
   return nombres.map(x=>{try{return JSON.parse(fs.readFileSync(path.join(dir,x),'utf8'),reviver);}catch{return null;}}).filter(e=>e?.msg?.key?.id);
  },
 };
}
function colaEnMemoria(){const m=new Map();return {poner:(id,e)=>void m.set(id,e),quitar:(id)=>void m.delete(id),todas:()=>[...m.values()]};}

/** The useful part of a Baileys message (already unwrapped with normalizeMessageContent). */
export function contenido(m){
 if(!m)return {texto:'',tipo:null,citaA:null};
 const tipo=m.audioMessage?'audio':m.imageMessage?'foto':m.videoMessage||m.ptvMessage?'vídeo':m.documentMessage?'archivo':m.stickerMessage?'sticker':null;
 const media=m.audioMessage||m.imageMessage||m.videoMessage||m.ptvMessage||m.documentMessage;
 const texto=(m.conversation||m.extendedTextMessage?.text||media?.caption||'').trim();
 const ctx=(m.extendedTextMessage||media)?.contextInfo;
 const q=ctx?.quotedMessage,citado=(q?.conversation||q?.extendedTextMessage?.text||q?.pollCreationMessage?.name||q?.pollCreationMessageV3?.name||'').trim();
 return {texto,tipo,citaA:ctx?.participant||null,citaId:ctx?.stanzaId||null,citado,reenviado:!!ctx?.isForwarded,nombre:m.documentMessage?.fileName||'',mime:media?.mimetype||''};
}
const ext=(c)=>c.tipo==='audio'?'.ogg':c.tipo==='foto'?'.jpg':c.tipo==='vídeo'?'.mp4':((c.nombre.match(/\.[A-Za-z0-9]{1,8}$/)||[''])[0].toLowerCase()||'.bin');
const pad=(n)=>String(n).padStart(2,'0');
function sello(seg){const d=new Date(seg*1000),f=new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Madrid',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(d),p=Object.fromEntries(f.map(x=>[x.type,x.value]));return `${p.year}-${p.month}-${p.day}-${p.hour}${p.minute}`;}

/**
 * deps: soy (Alfred's own ids), esDueno(key) → bool, enviar(texto, citado?) → Promise, reaccionar(key, emoji),
 *       descargar(msg) → Buffer, guardar(buffer, nombre) → absolute path, inbox(datos) → {escrita, resumen},
 *       transcribir(path) → text, alfred(texto, onEvento) → Promise (rejects with .dormido when the app is closed),
 *       permiso(id, si) → bool, visto(id) → bool (and remembers it), log(texto), ahora() → seconds,
 *       despertar() → Promise<bool> (optional: opens the closed app; then alfred/orden are tried once more),
 *       orden(texto, ref?) → Promise (an order or the answer to question `ref`; rejects with .dormido like alfred),
 *       pregunta(id) → ref of the question Alfred asked in that WhatsApp message, or null,
 *       enlace(url, id, pedido) → Promise<{nota, resumen, destino, icono} | {error}> (any link → its note: inbox/enlaces.py; optional),
 *       documento(archivo, id, nombre, pedido) → Promise<{nota, resumen, proyecto} | {error}> (a PDF → filed in its project; optional)
 *       cola: {poner(id, entrada), quitar(id), todas()} (optional, in memory by default: colaEnCarpeta keeps it across restarts)
 */
export function crearPuente(deps){
 let cadena=Promise.resolve(),pendiente=null;   // pendiente: the permission Alfred is waiting for
 let pidio=null,sinRespuesta=null;               // what Alfred asked for; the last request nobody answered in time ({id,que,hora})
 let videos=Promise.resolve();
 let charla=0;                                    // when Alfred last answered: the conversation goes on for CHARLA seconds                    // one video at a time, outside the chain: a minute of work must not hold the chat
 const cola=deps.cola||colaEnMemoria(),enCurso=new Set();
 // an emoji is only an acknowledgement: if it cannot be sent, what it acknowledges is still done
 const reaccion=(key,emoji)=>Promise.resolve().then(()=>deps.reaccionar(key,emoji)).catch(e=>deps.log('reacción no enviada: '+(e?.message||e)));

 function aVideo(msg,url,pedido,visto=null){
  if(!deps.enlace)return;
  videos=videos.then(async()=>{
   const r=await deps.enlace(url,'wa:'+msg.key.id,pedido).catch(e=>({error:e?.message||String(e)}));
   if(visto&&r?.nota)visto.texto+=` (es «${r.nota}»${r.resumen?': '+r.resumen:''})`;
   // 05/11, Owner: lo que solo hay que guardar se contesta con un emoji, sin texto (🎬 🐦 ▶️ 🔗 🖼️; ⚠️ = no se pudo leer, sigue en el inbox)
   await reaccion(msg.key,r?.nota?(r.icono||'🎬'):'⚠️');
   if(!r?.nota)deps.log(`enlace sin leer (${url}): ${r?.error||'sin respuesta'}`);
  }).catch(e=>deps.log('vídeo no procesado: '+(e?.message||e)));
  return videos;
 }
 // a PDF (04/11, «hazlo con los PDFs también»): read and filed in its project's research folder, on the same one-at-a-time queue
 function aDocumento(msg,archivo,nombre,pedido){
  if(!deps.documento)return;
  videos=videos.then(async()=>{
   const r=await deps.documento(archivo,'wa:'+msg.key.id,nombre,pedido).catch(e=>({error:e?.message||String(e)}));
   await reaccion(msg.key,r?.nota?'📄':'⚠️');
   if(!r?.nota)deps.log(`PDF sin leer (${nombre}): ${r?.error||'sin respuesta'}`);
  }).catch(e=>deps.log('documento no procesado: '+(e?.message||e)));
  return videos;
 }
 const ahora=deps.ahora||(()=>Date.now()/1000);
 // 16/11: «Estoy hay que hacerlo YA! En nuestra granja de reels» → «Esto de arriba»: the reel above had gone only to the inbox and
 // Alfred never saw it. What went only to the inbox in the last half hour travels, once, with the next message he gets.
 const RECIENTE=30*60;let recientes=[];
 function recordar(texto){recientes=recientes.filter(x=>ahora()-x.hora<RECIENTE).slice(-4);const r={hora:ahora(),texto};recientes.push(r);return r;}
 function contexto(){
  const r=recientes.filter(x=>ahora()-x.hora<RECIENTE);recientes=[];
  return r.length?`[Antes, sin hablarte, mandó al grupo (ya está en su inbox): ${r.map(x=>'«'+x.texto.slice(0,400)+'»').join(' · ')}. Si dice «esto», «lo de arriba» o algo así, habla de eso.] `:'';
 }

 async function aInbox(msg,c,oido,archivo){
  const falta=c.tipo&&c.tipo!=='sticker'&&!archivo;   // the last try could not download it: pending, with ⚠️
  const r=await deps.inbox({id:'wa:'+msg.key.id,fecha:Number(msg.messageTimestamp)||Math.floor(ahora()),texto:falta?`${c.texto} ⚠️ ${c.tipo} sin descargar`.trim():c.texto,oido,
   origen:c.reenviado?'otro chat':'',adjuntos:archivo?[{tipo:c.tipo,archivo}]:[]});
  if(!r?.escrita)return r;
  // 05/11, Owner: «que simplemente me responda el emoji» cuando solo hay que guardarlo (también las notas de voz)
  await reaccion(msg.key,falta?'⚠️':'📥');
  return r;
 }

 // 05/11, Owner: «quiero poder hablar con él normal»: con la app cerrada, el puente la abre (deps.despertar) y vuelve a intentarlo una vez
 async function conApp(fn){
  try{return await fn();}
  catch(err){if(!err?.dormido||!deps.despertar||!await deps.despertar().catch(()=>false))throw err;}
  return await fn();
 }

 // guardar: Owner did not address him (08/11); Alfred's answer is held and, if it is only «📥», the message goes to the inbox instead
 async function aAlfred(msg,texto,guardar=null){
  const dicho=[];
  try{
   const antes=contexto();if(antes)texto=antes+texto;
   await conApp(()=>deps.alfred(texto,async e=>{
    if(e.t==='say'&&e.texto){if(guardar)dicho.push(e.texto);else await deps.enviar(e.texto);}
    else if(e.t==='permiso'){pendiente=e.id;pidio={id:e.id,que:e.que};await deps.enviar(`Alfred quiere ${e.que}.${e.detalle?'\n\n'+e.detalle:''}\n\nResponde «sí» o «no».`);}
    else if(e.t==='error'&&e.texto)await deps.enviar(e.texto);
   }));
  }catch(err){
   if(!err?.dormido)throw err;
   return false;
  }finally{if(pendiente&&pidio?.id===pendiente)sinRespuesta={...pidio,hora:ahora()};pendiente=null;}
  if(guardar){
   const todo=dicho.join('\n').trim();
   if(!todo||/^📥\s*$/u.test(todo)){await guardar();return true;}
   for(const t of dicho)await deps.enviar(t);
  }
  charla=ahora();
  return true;
 }

 // e: its entry in the queue (what an earlier try already got is kept there); ultimo: the last try, which never throws for the download
 async function procesar(msg,e={},ultimo=true){
  const c=contenido(msg.message);
  if(!c.tipo&&!c.texto)return;
  if(e.tarde){   // 11/11: a «sí» that came after the request ran out (from the gym): Alfred does it now, without asking again
   const vale=await Promise.resolve(deps.permisoTarde?.(e.tarde.id)).catch(()=>false);
   await reaccion(msg.key,'✅');
   const pedido=vale?`[Owner ha dicho «sí», tarde, a lo que le pediste: «${e.tarde.que}». Ya tienes su permiso para eso: hazlo ahora tal cual y dile en una línea que está hecho.]`
    :`[Owner ha dicho «sí» a lo que le pediste antes («${e.tarde.que}»), pero ese permiso ya no vale: vuelve a hacerlo; si te pide permiso otra vez, lo contestará.]`;
   if(await aAlfred(msg,pedido))return;
   await deps.enviar('No he podido abrir la app del Mac para Alfred.',msg);
   return;
  }
  let archivo='',oido='';
  if(c.tipo&&c.tipo!=='sticker'){
   try{archivo=deps.guardar(await deps.descargar(msg),`${sello(Number(msg.messageTimestamp)||ahora())}-wa-${msg.key.id}${ext(c)}`);e.archivo=archivo;}
   catch(err){
    deps.log('adjunto no descargado: '+(err?.message||err));
    if(!ultimo){const x=Error('adjunto no descargado: '+(err?.message||err));x.reintentar=true;throw x;}
   }
   if(archivo&&c.tipo==='audio')oido=e.oido||(e.oido=await deps.transcribir(archivo).catch(()=>''));
  }else if(c.tipo==='sticker'&&!c.texto)return;
  const dicho=c.texto||oido,citaAlfred=c.citaA&&deps.soy.includes(c.citaA);
  const ref=c.citaId&&deps.pregunta?deps.pregunta(c.citaId):null;
  if(c.texto&&!c.tipo&&deps.orden&&(ref||/^\/[a-záéíóúñ]+(\s|$)/i.test(c.texto))){
   try{await conApp(()=>deps.orden(c.texto,ref));return;}
   catch(err){if(!err?.dormido)throw err;}
   await aInbox(msg,c,oido,archivo);
   await deps.enviar('No he podido abrir la app del Mac para Alfred. Lo he apuntado en el inbox.',msg);
   return;
  }
  const sinEnlace=!/https?:\/\//i.test(dicho||'')&&(!c.tipo||c.tipo==='audio'||c.tipo==='foto');   // a photo in the chat (a dish, the fridge) is for Alfred too
  const enCharla=sinEnlace&&ahora()-charla<CHARLA&&!PARA_INBOX.test(dicho||'');
  if((dicho||(enCharla&&c.tipo==='foto'&&archivo))&&(PARA_ALFRED.test(dicho||'')||RECADO.test(dicho||'')||(sinEnlace&&!PARA_INBOX.test(dicho||'')&&PREGUNTA.test(dicho||''))||MENCIONA.test((dicho||'').replace(/https?:\/\/\S+/g,''))||citaAlfred||(enCharla&&!dicho))){   // in a chat, text he is not named in goes the way below: it may still be an idea
   const viejo=ahora()-(Number(msg.messageTimestamp)||ahora())>15*60;   // waited too long (Mac asleep): an idea, not a chat
   let pedido=dicho;
   if(citaAlfred&&c.citado)pedido=`[Responde a tu mensaje: «${c.citado.slice(0,600)}»] ${pedido}`;
   if(c.tipo==='foto'&&archivo)pedido=`[Te manda una foto; está en ${archivo}. Ábrela con Read antes de responder.] ${dicho}`;
   else if(c.tipo==='archivo'&&archivo)pedido=`[Te manda un archivo; está en ${archivo}. Ábrelo con Read antes de responder.] ${dicho}`;
   if(!viejo&&await aAlfred(msg,pedido))return;
   await aInbox(msg,c,oido,archivo);
   if(!viejo)await deps.enviar('No he podido abrir la app del Mac para Alfred. Lo he apuntado en el inbox.',msg);
   return;
  }
  // 08/11, Owner: «arregla que Alfred conteste aunque no le nombre». The training he dictated set by set, «Entreno done actualiza todo»
  // and the dentist went to the inbox. Now any text, voice note or captioned photo without a link reaches Alfred; he answers, or
  // only «📥» when it is just an idea to keep, and then it goes to the inbox as before. Links, files, forwarded messages and
  // «idea: …» still go straight to the inbox; so does everything when the app cannot answer or the message is old.
  const viejo=ahora()-(Number(msg.messageTimestamp)||ahora())>15*60;
  if(dicho&&sinEnlace&&!c.reenviado&&!PARA_INBOX.test(dicho)&&!viejo){
   let pedido=ahora()-charla<CHARLA?`[Seguís la conversación. Si esto es solo una idea o una nota para guardar, responde únicamente «📥» y se guarda en su inbox.] ${dicho}`:`[Owner no te ha nombrado: puede ser algo para ti (una orden, algo que te cuenta para apuntar, como un entreno, o una pregunta) o solo una idea para guardar. Si es solo una idea o una nota, responde únicamente «📥» y se guarda en su inbox. Si no, hazlo y contesta corto.] ${dicho}`;
   if(c.tipo==='foto'&&archivo)pedido=`[Te manda una foto; está en ${archivo}. Ábrela con Read antes de responder.] ${pedido}`;
   if(await aAlfred(msg,pedido,()=>aInbox(msg,c,oido,archivo)))return;
  }
  const apuntado=await aInbox(msg,c,oido,archivo);
  const visto=recordar([c.reenviado?'(reenviado)':'',c.texto||oido,c.tipo&&c.tipo!=='sticker'?`[${c.tipo}${archivo?': '+archivo:''}]`:''].filter(Boolean).join(' '));
  // 04/11: every link is read (reels and Shorts → «Para rehacer»; tweets, YouTube, web pages → filed in their project)
  const url=(c.texto.match(/https?:\/\/[^\s<>"'«»]+/)?.[0]||'').replace(/[).,;:!?]+$/,'');
  // «para Norte» guides the remake; a bare link sent in a burst (12/11: «Aplicar esto a agent bar» + reel) gets the burst's words
  const sinUrl=t=>(t||'').split(/\s+/).filter(w=>!/^https?:\/\//.test(w)).join(' ').replace(/(\s*·\s*)+/g,' · ').replace(/^\s*·\s*|\s*·\s*$/g,'').trim();
  if(url)void aVideo(msg,url,sinUrl(c.texto)||sinUrl(apuntado?.contexto),visto);
  else if(c.tipo==='archivo'&&archivo&&(/pdf/i.test(c.mime)||/\.pdf$/i.test(c.nombre)))void aDocumento(msg,archivo,c.nombre,c.texto);
 }

 // Nothing is lost: a failed try stays in the queue for the next connection; the last one, or any other failure, ends in the inbox.
 async function intentar(e){
  const id=e.msg.key.id,ultimo=e.intentos+1>=MAX_INTENTOS;
  try{await procesar(e.msg,e,ultimo);cola.quitar(id);return;}
  catch(err){
   const motivo=err?.message||String(err);
   e.intentos++;e.error=motivo;e.proximo=ahora()+30*2**Math.min(e.intentos,6);   // 1, 2, 4… min if the connection never drops
   if(!ultimo&&deConexion(err)){cola.poner(id,e);deps.log(`mensaje en cola (${e.intentos}/${MAX_INTENTOS}), lo reintento al reconectar: ${motivo}`);return;}
   deps.log('mensaje no procesado: '+motivo);
   try{await rescatar(e);cola.quitar(id);}
   catch(x){   // not even the inbox: kept for a while longer
    deps.log('tampoco he podido apuntarlo en el inbox: '+(x?.message||x));
    if(e.intentos<MAX_INTENTOS*2)cola.poner(id,e);else{cola.quitar(id);deps.log('mensaje perdido: wa:'+id);}
   }
  }
 }
 // the inbox line with its wa: mark (inbox.py never writes the same one twice), with whatever an earlier try got
 async function rescatar(e){
  const msg=e.msg,c=contenido(msg.message);
  const texto=[c.texto||(c.tipo&&!e.archivo?`(${c.tipo} sin descargar)`:''),'⚠️ sin procesar'].filter(Boolean).join(' ');
  const r=await deps.inbox({id:'wa:'+msg.key.id,fecha:Number(msg.messageTimestamp)||Math.floor(ahora()),texto,oido:e.oido||'',
   origen:c.reenviado?'otro chat':'',adjuntos:e.archivo?[{tipo:c.tipo,archivo:e.archivo}]:[]});
  if(r?.escrita)await reaccion(msg.key,'⚠️');
 }
 function encolar(e){
  const id=e.msg.key.id;
  if(enCurso.has(id))return cadena;
  enCurso.add(id);
  cadena=cadena.then(()=>intentar(e)).catch(x=>deps.log('mensaje no procesado: '+(x?.message||x))).finally(()=>enCurso.delete(id));
  return cadena;
 }

 return {
  /** Every message of the group, in order. Permission answers go through at once (Alfred is waiting for them). */
  recibir(msg){
   if(!msg?.key?.id||msg.key.fromMe||!deps.esDueno(msg.key))return;
   if(ahora()-(Number(msg.messageTimestamp)||ahora())>MAX_DIAS*86400)return;
   const c=contenido(msg.message);
   if(pendiente&&!c.tipo&&(SI.test(c.texto)||NO.test(c.texto))){
    if(deps.visto(msg.key.id))return;
    const si=SI.test(c.texto),id=pendiente;pendiente=null;
    deps.permiso(id,si);void Promise.resolve(deps.reaccionar(msg.key,si?'✅':'❌')).catch(()=>{});
    return;
   }
   if(deps.visto(msg.key.id))return;
   const e={msg,intentos:0,desde:ahora()};
   if(!pendiente&&sinRespuesta&&!c.tipo&&ahora()-sinRespuesta.hora<TARDE&&(SOLO_SI.test(c.texto)||SOLO_NO.test(c.texto))){
    const r=sinRespuesta;sinRespuesta=null;
    if(SOLO_NO.test(c.texto)){void reaccion(msg.key,'❌');return;}
    e.tarde={id:r.id,que:r.que};
   }
   cola.poner(msg.key.id,e);
   return encolar(e);
  },
  /** What is still in the queue, oldest first: all of it when WhatsApp reconnects (todo), otherwise only what is due. */
  reanudar({todo=false}={}){
   const pendientes=cola.todas().sort((a,b)=>(Number(a.msg.messageTimestamp)||0)-(Number(b.msg.messageTimestamp)||0));
   for(const e of pendientes)if(todo||!(e.proximo>ahora()))encolar({intentos:0,...e});
   return cadena;
  },
  pendientes:()=>cola.todas().length,
  esperar:()=>cadena.then(()=>videos),
 };
}
