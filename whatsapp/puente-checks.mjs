// The WhatsApp bridge against a fake WhatsApp and a fake Alfred: ideas reach the real inbox (inbox/inbox.py on a temporary
// vault, never Owner's), «Alfred, …» goes to Alfred, permissions are answered with «sí»/«no», strangers are ignored.
// Run: node --test whatsapp/puente-checks.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {crearPuente,contenido,colaEnCarpeta,MAX_INTENTOS} from './logica.mjs';
import {plan,voto,agentBar} from './avisos.mjs';
import crypto from 'node:crypto';

const AQUI=path.dirname(fileURLToPath(import.meta.url)),INBOX=path.join(AQUI,'..','inbox','inbox.py');
const raiz=fs.mkdtempSync(path.join(os.tmpdir(),'whatsapp-checks-')),data=path.join(raiz,'Workspace'),vault=path.join(raiz,'vault');
fs.mkdirSync(data,{recursive:true});fs.mkdirSync(vault);
fs.writeFileSync(path.join(data,'configuracion.json'),JSON.stringify({vault,inbox:{nota:'Ideas/inbox.md'}}));
const nota=path.join(vault,'Ideas','inbox.md'),adjuntos=path.join(vault,'Ideas','inbox-adjuntos');
const inbox=(datos)=>JSON.parse(execFileSync('/usr/bin/python3',[INBOX,'apuntar'],{input:JSON.stringify(datos),env:{...process.env,PROMPTED_OS_DATA_DIR:data}}));
test.after(()=>fs.rmSync(raiz,{recursive:true,force:true}));

const OWNER='34600000000@s.whatsapp.net',ALFRED='34611111111@s.whatsapp.net',T=1793380260;   // 30/10/2026 18:11 Madrid
let n=0;
const msg=(message,{de=OWNER,t=T,id}={})=>({key:{id:id||'ID'+(++n),remoteJid:'1203@g.us',participant:de,fromMe:false},messageTimestamp:t,message});

function montar({alfred,sinNombre,dormido=false,preguntas={},enlace,documento,reloj,despertar,oido,enviar,descargar,cola,tarde}={}){
 const enviados=[],ordenes=[],reacciones=[],permisos=[],vistos=new Set(),log=[];
 const p=crearPuente({
  soy:[ALFRED],esDueno:(k)=>k.participant===OWNER,ahora:reloj||(()=>T+30),
  enviar:async(texto,citado)=>{if(enviar)await enviar(texto);enviados.push({texto,cita:citado?.key.id});},
  reaccionar:async(key,emoji)=>{if(enviar)await enviar(emoji);reacciones.push({id:key.id,emoji});},
  descargar:descargar||(async(m)=>Buffer.from('bytes de '+m.key.id)),
  guardar:(buf,nombre)=>{fs.mkdirSync(adjuntos,{recursive:true});const f=path.join(adjuntos,nombre);fs.writeFileSync(f,buf);return f;},
  inbox:async(d)=>inbox(d),
  transcribir:async()=>oido||'la entrada no es lo importante',
  // a message he was not named in: like the real Alfred with an idea, he only answers «📥», unless the test gives `sinNombre`
  alfred:async(texto,emit)=>{if(dormido){const e=Error('cerrada');e.dormido=true;throw e;}
   if(texto.includes('[Owner no te ha nombrado')){if(sinNombre)return sinNombre(texto,emit);await emit({t:'say',texto:'📥'});return;}
   await alfred(texto,emit);},
  permiso:(id,si)=>permisos.push({id,si}),
  permisoTarde:async(id)=>{permisos.push({id,tarde:true});return tarde!==false;},
  visto:(id)=>{if(vistos.has(id))return true;vistos.add(id);return false;},
  log:(t)=>log.push(t),
  orden:async(texto,ref)=>{if(dormido){const e=Error('cerrada');e.dormido=true;throw e;}ordenes.push({texto,ref});},
  pregunta:(id)=>preguntas[id]??null,
  ...(enlace?{enlace}:{}),
  ...(documento?{documento}:{}),
  ...(despertar?{despertar}:{}),
  ...(cola?{cola}:{}),
 });
 return {p,enviados,reacciones,permisos,log,ordenes};
}
const leer=()=>fs.existsSync(nota)?fs.readFileSync(nota,'utf8'):'';

test('una idea de texto entra en el inbox con fecha y hora y 📥, sin duplicados',async()=>{
 const {p,reacciones,enviados}=montar();
 const m=msg({conversation:'Sauron: fondo de IA que se mejora solo'});
 await p.recibir(m);await p.recibir(m);await p.esperar();
 assert.match(leer(),/- \[ \] 2026-10-30 18:11 · Sauron: fondo de IA que se mejora solo <!-- wa:ID\d+ -->\n/);
 assert.equal(leer().split('Sauron').length-1,1);
 assert.deepEqual(reacciones,[{id:m.key.id,emoji:'📥'}]);
 assert.deepEqual(enviados,[]);
});

test('nota de voz: transcrita, guardada y solo con 📥 (05/11: «que simplemente me responda el emoji»)',async()=>{
 const {p,enviados,reacciones}=montar();
 const m=msg({audioMessage:{mimetype:'audio/ogg; codecs=opus',ptt:true}});
 await p.recibir(m);await p.esperar();
 assert.ok(leer().includes(`· 🎙️ la entrada no es lo importante\n  audio: [[inbox-adjuntos/2026-10-30-1811-wa-${m.key.id}.ogg]] <!-- wa:${m.key.id} -->`));
 assert.deepEqual(enviados,[]);assert.deepEqual(reacciones,[{id:m.key.id,emoji:'📥'}]);
 assert.equal(fs.readFileSync(path.join(adjuntos,`2026-10-30-1811-wa-${m.key.id}.ogg`),'utf8'),'bytes de '+m.key.id);
});

test('foto con pie y enlace',async()=>{
 const {p}=montar();
 const f=msg({imageMessage:{caption:'setup brutal estilo Idiocracy',mimetype:'image/jpeg'}}),e=msg({extendedTextMessage:{text:'https://example.com/reel'}});
 await p.recibir(f);await p.recibir(e);await p.esperar();
 assert.ok(leer().includes(`· setup brutal estilo Idiocracy\n  ![[inbox-adjuntos/2026-10-30-1811-wa-${f.key.id}.jpg]] <!-- wa:${f.key.id} -->`));
 assert.ok(leer().includes(`· https://example.com/reel <!-- wa:${e.key.id} -->`));
});

test('«Alfred, …» va a Alfred y no al inbox; la respuesta sale en el grupo',async()=>{
 const pedidos=[];
 const {p,enviados}=montar({alfred:async(texto,emit)=>{pedidos.push(texto);await emit({t:'say',texto:'Tienes 2 cosas hoy: grabar y llamar.'});await emit({t:'fin'});}});
 const antes=leer();
 await p.recibir(msg({conversation:'Alfred, ¿qué tengo hoy?'}));await p.esperar();
 assert.deepEqual(pedidos,['Alfred, ¿qué tengo hoy?']);
 assert.deepEqual(enviados.map(e=>e.texto),['Tienes 2 cosas hoy: grabar y llamar.']);
 assert.equal(leer(),antes);
});

test('responder a un mensaje de Alfred también es hablar con él; una nota de voz que empieza por «Alfred» también',async()=>{
 const pedidos=[];
 const {p}=montar({alfred:async(texto)=>{pedidos.push(texto);}});
 await p.recibir(msg({extendedTextMessage:{text:'¿y mañana?',contextInfo:{participant:ALFRED}}}));
 const voz=montar({alfred:async(texto)=>{pedidos.push(texto);}});
 voz.p.recibir(msg({audioMessage:{}}));   // «la entrada…» does not start with Alfred: inbox
 await p.esperar();await voz.p.esperar();
 assert.deepEqual(pedidos,['¿y mañana?']);
});

test('una pregunta o «contéstame» va a Alfred sin decir su nombre; una idea sigue al inbox (06/11)',async()=>{
 const pedidos=[];
 const voz=montar({alfred:async(texto)=>{pedidos.push(texto);},oido:'Voy a entrenar hoy, ¿qué puedo hacer suave?'});
 await voz.p.recibir(msg({audioMessage:{}}));await voz.p.esperar();
 const txt=montar({alfred:async(texto)=>{pedidos.push(texto);}});
 await txt.p.recibir(msg({conversation:'Pero contéstame a eso'}));
 const antes=leer();
 await txt.p.recibir(msg({conversation:'Idea: ¿y si el orbe cambia de color?'}));
 await txt.p.recibir(msg({conversation:'¿Qué opinas? https://example.com/x'}));await txt.p.esperar();
 assert.deepEqual(pedidos,['Voy a entrenar hoy, ¿qué puedo hacer suave?','Pero contéstame a eso']);
 assert.ok(leer().length>antes.length);
});

test('permiso: Alfred espera, «sí» lo concede aunque Alfred siga ocupado',async()=>{
 let soltar;const libre=new Promise(r=>soltar=r);
 const {p,enviados,permisos,reacciones}=montar({alfred:async(texto,emit)=>{await emit({t:'permiso',id:'perm-1',que:'leer una página',detalle:'https://x.com'});await libre;await emit({t:'say',texto:'Hecho.'});}});
 p.recibir(msg({conversation:'Alfred, lee esto'}));
 await new Promise(r=>setTimeout(r,20));
 assert.match(enviados[0].texto,/^Alfred quiere leer una página\.\n\nhttps:\/\/x\.com\n\nResponde «sí» o «no»\.$/);
 const si=msg({conversation:'Sí'});p.recibir(si);
 assert.deepEqual(permisos,[{id:'perm-1',si:true}]);
 soltar();await p.esperar();
 assert.deepEqual(reacciones,[{id:si.key.id,emoji:'✅'}]);
 assert.equal(enviados.at(-1).texto,'Hecho.');
 assert.ok(!leer().includes('<!-- wa:'+si.key.id));
});

test('permiso que caduca (11/11, desde el gimnasio): un «sí» más tarde hace que Alfred lo haga sin volver a pedirlo',async()=>{
 const pedidos=[];
 const {p,enviados,permisos,reacciones}=montar({alfred:async(texto,emit)=>{pedidos.push(texto);if(pedidos.length===1){await emit({t:'permiso',id:'perm-9',que:'cambiar 01-Registros / Diario.md',detalle:''});await emit({t:'say',texto:'No me has contestado a tiempo; con un «sí» lo apunto.'});}else await emit({t:'say',texto:'Apuntado.'});}});
 await p.recibir(msg({conversation:'Alfred, apunta mi entreno de pierna'}));await p.esperar();
 const si=msg({conversation:'Sí, guárdalo'});await p.recibir(si);await p.esperar();
 assert.deepEqual(permisos,[{id:'perm-9',tarde:true}],'the late «sí» goes to the app as a late approval, not as a live answer');
 assert.match(pedidos[1],/ha dicho «sí», tarde, a lo que le pediste: «cambiar 01-Registros \/ Diario\.md»/);
 assert.deepEqual(reacciones,[{id:si.key.id,emoji:'✅'}]);
 assert.equal(enviados.at(-1).texto,'Apuntado.');
 const otra=msg({conversation:'Sí'});await p.recibir(otra);await p.esperar();
 assert.equal(permisos.length,1,'only once: a second «sí» is an ordinary message');
});
test('permiso caducado: «si mañana voy…» no lo concede y «no» lo cierra',async()=>{
 const pedidos=[];
 const {p,permisos,reacciones}=montar({alfred:async(texto,emit)=>{pedidos.push(texto);if(pedidos.length===1)await emit({t:'permiso',id:'perm-7',que:'ejecutar un comando',detalle:''});}});
 await p.recibir(msg({conversation:'Alfred, haz eso'}));await p.esperar();
 await p.recibir(msg({conversation:'Alfred, si mañana voy al gym recuérdamelo'}));await p.esperar();
 assert.deepEqual(permisos,[],'a sentence that starts with «si» is not a yes');
 assert.ok(!pedidos[1].includes('tarde'));
 const {p:p2,permisos:permisos2,reacciones:r2}=montar({alfred:async(texto,emit)=>{await emit({t:'permiso',id:'perm-8',que:'x',detalle:''});}});
 await p2.recibir(msg({conversation:'Alfred, haz eso'}));await p2.esperar();
 const no=msg({conversation:'No'});await p2.recibir(no);await p2.esperar();
 assert.deepEqual(permisos2,[]);assert.deepEqual(r2,[{id:no.key.id,emoji:'❌'}]);
});

test('ráfaga (12/11): mensajes seguidos son una idea, y el enlace suelto se lee con lo que dijo antes',async()=>{
 const pedidos=[];
 const {p}=montar({enlace:async(url,id,pedido)=>{pedidos.push(pedido);return {nota:'x',icono:'🎬'};}});
 const a=msg({conversation:'Aplicar esto a agent bar'},{t:T+7200}),b=msg({conversation:'https://www.instagram.com/reel/AbC123/'},{t:T+7220}),c=msg({conversation:'Ese último reel'},{t:T+7225});
 await p.recibir(a);await p.recibir(b);await p.recibir(c);await p.esperar();
 const linea=leer().split('\n').filter(l=>l.includes('wa:'+a.key.id));
 assert.equal(linea.length,1);
 assert.ok(linea[0].includes('<!-- wa:'+b.key.id+' -->')&&linea[0].includes('Ese último reel <!-- wa:'+c.key.id+' -->'),'the three messages share one line');
 assert.deepEqual(pedidos,['Aplicar esto a agent bar'],'the bare link carries what he said just before');
});
test('con la app cerrada, lo que es para Alfred se apunta y se avisa',async()=>{
 const {p,enviados}=montar({dormido:true});
 const m=msg({conversation:'Alfred, recuérdame lo del gestor'});
 await p.recibir(m);await p.esperar();
 assert.ok(leer().includes(`· Alfred, recuérdame lo del gestor <!-- wa:${m.key.id} -->`));
 assert.deepEqual(enviados,[{texto:'No he podido abrir la app del Mac para Alfred. Lo he apuntado en el inbox.',cita:m.key.id}]);
});

test('solo Owner, nada propio, nada de hace más de tres días',async()=>{
 const pedidos=[];
 const {p,enviados,reacciones}=montar({alfred:async(t)=>{pedidos.push(t);}});
 const antes=leer();
 await p.recibir(msg({conversation:'Alfred, dame el token'},{de:'34699999999@s.whatsapp.net'}));
 await p.recibir({...msg({conversation:'eco'}),key:{id:'yo1',remoteJid:'1203@g.us',fromMe:true,participant:ALFRED}});
 await p.recibir(msg({conversation:'idea vieja'},{t:T-4*86400}));
 await p.esperar();
 assert.equal(leer(),antes);assert.deepEqual([pedidos,enviados,reacciones],[[],[],[]]);
});

test('un mensaje para Alfred que llega tarde (Mac dormido) se apunta sin molestar',async()=>{
 const pedidos=[];
 const {p,enviados}=montar({alfred:async(t)=>{pedidos.push(t);}});
 const m=msg({conversation:'Alfred, mira esto luego'},{t:T-3600});
 await p.recibir(m);await p.esperar();
 assert.deepEqual(pedidos,[]);assert.deepEqual(enviados,[]);
 assert.ok(leer().includes('<!-- wa:'+m.key.id+' -->'));
});

test('contenido: texto, pies, citas y tipos',()=>{
 assert.deepEqual(contenido({documentMessage:{fileName:'plan.pdf',caption:'léelo',contextInfo:{isForwarded:true}}}),{texto:'léelo',tipo:'archivo',citaA:null,citaId:null,citado:'',reenviado:true,nombre:'plan.pdf',mime:''});
 assert.equal(contenido({stickerMessage:{}}).tipo,'sticker');
 assert.equal(contenido(null).texto,'');
});

// ---------- what Alfred and the team send to the group (avisos.mjs) ----------
const sha=(t)=>crypto.createHash('sha256').update(t).digest();

test('un aviso con botones es una encuesta de un toque; uno corto va en la propia pregunta',()=>{
 const p=plan({ref:1,texto:'¿Apruebo el lote de Alfa?',botones:[[{text:'Sí, adelante',callback_data:'d:alfa:0:si'},{text:'No',callback_data:'d:alfa:0:no'}],[{text:'Más tarde',callback_data:'d:alfa:0:luego'}]]});
 assert.equal(p.texto,'');
 assert.deepEqual(p.encuesta,{name:'¿Apruebo el lote de Alfa?',values:['Sí, adelante','No','Más tarde'],selectableCount:1});
 assert.deepEqual(plan({texto:'Hola'}),{texto:'Hola',encuesta:null,opciones:[]});
 assert.equal(plan({texto:'x',botones:[[{text:'✓ Aprobado',callback_data:'x'}]]}).encuesta,null);   // a spent button is not a poll
});

test('la to-do es una encuesta de varias opciones: marcar una tarea la tacha, desmarcar no hace nada',()=>{
 const p=plan({ref:7,texto:'Hoy, dom 1\n○ Grabar\n○ Llamar',botones:[[{text:'✓ Grabar',callback_data:'h:2026-11-01:0'}],[{text:'✓ Llamar',callback_data:'h:2026-11-01:1'}]]});
 assert.equal(p.texto,'Hoy, dom 1\n○ Grabar\n○ Llamar');
 assert.deepEqual(p.encuesta,{name:'Marca lo que ya has hecho',values:['Grabar','Llamar'],selectableCount:2});
 const reg={opciones:p.opciones,lista:true};
 let r=voto(reg,[sha('Llamar')],[]);
 assert.deepEqual(r,{pulsar:['h:2026-11-01:1'],seleccion:['Llamar']});
 r=voto(reg,[sha('Llamar'),sha('Grabar')],r.seleccion);
 assert.deepEqual(r.pulsar,['h:2026-11-01:0']);
 assert.deepEqual(voto(reg,[sha('Grabar')],r.seleccion).pulsar,[]);
});

test('una decisión vale una vez: cambiar el voto después no hace nada',()=>{
 const p=plan({texto:'¿Juntamos?',botones:[[{text:'Sí',callback_data:'f:alfa'},{text:'No',callback_data:'g:1:n'}]]});
 const reg={opciones:p.opciones,lista:false};
 const r=voto(reg,[sha('Sí')],[]);
 assert.deepEqual(r.pulsar,['f:alfa']);
 assert.deepEqual(voto(reg,[sha('No')],r.seleccion).pulsar,[]);
});

test('opciones repetidas o largas se ajustan a lo que WhatsApp acepta',()=>{
 const largo='Pedir afiliación a tres firmas de fondeo con los mensajes ya redactados en español y en inglés, y apuntar quién contesta';
 const p=plan({texto:'x'.repeat(300),botones:[[{text:largo,callback_data:'g:1:h'},{text:largo,callback_data:'g:2:h'}]]});
 assert.equal(p.encuesta.name,'¿Qué hacemos?');
 assert.equal(p.texto.length,300);
 assert.ok(p.encuesta.values.every(v=>v.length<=100));
 assert.equal(new Set(p.encuesta.values).size,2);
});

test('«/lote» en el grupo va a la app como orden; con la app cerrada, al inbox',async()=>{
 const {p,ordenes}=montar();
 await p.recibir(msg({conversation:'/lote'}));await p.esperar();
 assert.deepEqual(ordenes,[{texto:'/lote',ref:null}]);
 const cerrada=montar({dormido:true});
 await cerrada.p.recibir(msg({conversation:'/hoy'}));await cerrada.p.esperar();
 assert.equal(cerrada.ordenes.length,0);
 assert.match(cerrada.enviados.at(-1).texto,/No he podido abrir la app/);
});

test('responder a una pregunta de Alfred le llega como respuesta a esa pregunta, no como idea',async()=>{
 const {p,ordenes,reacciones}=montar({preguntas:{'WA-PREG':1793380000123}});
 await p.recibir(msg({extendedTextMessage:{text:'1200 40 8 0',contextInfo:{stanzaId:'WA-PREG',participant:ALFRED}}}));await p.esperar();
 assert.deepEqual(ordenes,[{texto:'1200 40 8 0',ref:1793380000123}]);
 assert.deepEqual(reacciones,[]);
});

test('lo que Owner cuenta sin nombrarle (un entreno, «actualiza todo») le llega a Alfred y contesta; una idea acaba en el inbox (08/11)',async()=>{
 fs.rmSync(nota,{force:true});
 const pedidos=[];
 const responde=async(t,emit)=>{pedidos.push(t);await emit({t:'say',texto:t.includes('dominadas')?'Apuntado en tu diario: dominadas 4 series.':'📥'});};
 const {p,enviados,reacciones}=montar({sinNombre:responde,alfred:responde});   // the idea comes while they are talking
 const e=msg({conversation:'He hecho dominadas 4 series con 5 kg'}),i=msg({conversation:'Eclipse animado con ascii para la landing'});
 await p.recibir(e);await p.recibir(i);await p.esperar();
 assert.equal(pedidos.length,2);
 assert.ok(pedidos[1].startsWith('[Seguís la conversación.'));
 assert.deepEqual(enviados,[{texto:'Apuntado en tu diario: dominadas 4 series.',cita:undefined}]);
 assert.ok(!leer().includes('dominadas'));
 assert.ok(leer().includes('· Eclipse animado con ascii para la landing'));
 assert.deepEqual(reacciones,[{id:i.key.id,emoji:'📥'}]);
 // forwarded or with a link: straight to the inbox, Alfred is not asked
 await p.recibir(msg({extendedTextMessage:{text:'mira esto',contextInfo:{isForwarded:true}}}));await p.esperar();
 assert.equal(pedidos.length,2);
});

// ---------- a video link becomes a «Para rehacer» note (inbox/enlaces.py) ----------
test('un reel va al inbox y además a su nota «Para rehacer»; la respuesta es solo un emoji',async()=>{
 const pedidos=[];
 const {p,enviados,reacciones}=montar({enlace:async(url,id,pedido)=>{pedidos.push({url,id,pedido});return {nota:'2026-11-03 Autor - Título',resumen:'Gancho contrario sobre impuestos'};}});
 const m=msg({conversation:'mira esto https://www.instagram.com/reel/AbC123/?igsh=x'});
 await p.recibir(m);await p.esperar();
 assert.deepEqual(pedidos,[{url:'https://www.instagram.com/reel/AbC123/?igsh=x',id:'wa:'+m.key.id,pedido:'mira esto'}]);
 assert.deepEqual(reacciones,[{id:m.key.id,emoji:'📥'},{id:m.key.id,emoji:'🎬'}]);
 assert.deepEqual(enviados,[]);
});

test('«esto de arriba» (16/11): lo que fue solo al inbox viaja una vez con el siguiente mensaje para Alfred',async()=>{
 const pedidos=[];let ahora=T+30;
 const {p}=montar({reloj:()=>ahora,enlace:async()=>({nota:'2026-11-16 DopamineDevs - raea',resumen:'Motor que renderiza vídeo con IA'}),
  sinNombre:async(texto,emit)=>{pedidos.push(texto);await emit({t:'say',texto:'Vale, lo paso a Granja.'});},alfred:async(texto,emit)=>{pedidos.push(texto);await emit({t:'say',texto:'Hecho.'});}});
 await p.recibir(msg({conversation:'https://www.instagram.com/reel/Dfih/'}));await p.esperar();
 await p.recibir(msg({conversation:'Hay que hacerlo YA en nuestra granja de reels'}));await p.esperar();
 assert.match(pedidos[0],/^\[Antes, sin hablarte, mandó al grupo .*instagram\.com\/reel\/Dfih\/ \(es «2026-11-16 DopamineDevs - raea»: Motor que renderiza vídeo con IA\)/);
 await p.recibir(msg({conversation:'Alfred, ¿y lo otro?'}));await p.esperar();
 assert.doesNotMatch(pedidos[1],/Antes, sin hablarte/,'only once');
 ahora+=3600;await p.recibir(msg({conversation:'https://x.com/a/status/1'},{t:ahora}));await p.esperar();
 ahora+=31*60;await p.recibir(msg({conversation:'Alfred, ¿qué tal?'},{t:ahora}));await p.esperar();
 assert.doesNotMatch(pedidos[2],/Antes, sin hablarte/,'half an hour later it is gone');
});

test('cualquier enlace se lee (una web va a su proyecto); lo que no se puede leer queda pendiente con ⚠️',async()=>{
 const pedidos=[];
 const {p,enviados,reacciones}=montar({enlace:async(url)=>{pedidos.push(url);return url.includes('elpais')?{nota:'2026-11-04 Artículo',resumen:'Qué dice el artículo',destino:'Ideas',icono:'🔗'}:{error:'no he podido abrirlo (¿es privado o pide sesión?); queda pendiente'};}});
 const a=msg({conversation:'https://elpais.com/economia/articulo.html).'});await p.recibir(a);
 const m=msg({conversation:'https://youtu.be/xyz987'});await p.recibir(m);await p.esperar();
 assert.deepEqual(pedidos,['https://elpais.com/economia/articulo.html','https://youtu.be/xyz987']);
 assert.deepEqual(enviados,[]);
 assert.deepEqual(reacciones.filter(r=>r.emoji!=='📥'),[{id:a.key.id,emoji:'🔗'},{id:m.key.id,emoji:'⚠️'}]);
});

test('en plena charla, una foto sin texto (un plato, la nevera) le llega a Alfred; fuera de la charla va al inbox',async()=>{
 let ahora=T+30;const pedidos=[];
 const {p}=montar({reloj:()=>ahora,alfred:async(texto,emit)=>{pedidos.push(texto);await emit({t:'say',texto:'Le falta verdura'});}});
 await p.recibir(msg({conversation:'Alfred, ¿qué ceno?'}));
 const f=msg({imageMessage:{mimetype:'image/jpeg'}});await p.recibir(f);await p.esperar();
 assert.equal(pedidos.length,2);assert.match(pedidos[1],new RegExp(`^\\[Te manda una foto; está en .*wa-${f.key.id}\\.jpg`));
 ahora+=3600;const g=msg({imageMessage:{mimetype:'image/jpeg'}},{t:ahora-10});await p.recibir(g);await p.esperar();
 assert.equal(pedidos.length,2);assert.ok(leer().includes(`wa-${g.key.id}.jpg`));
});

// ---------- a PDF is read and filed in its project (inbox/documentos.py) ----------
test('un PDF va al inbox y además se archiva en su proyecto, con 📄; otros archivos no',async()=>{
 const pedidos=[];
 const {p,enviados,reacciones}=montar({documento:async(archivo,id,nombre,pedido)=>{pedidos.push({archivo:path.basename(archivo),id,nombre,pedido});return {nota:'2026-11-04 Trading Info-Business Blueprint',proyecto:'norte-capital',resumen:'Sistema para monetizar una audiencia de traders'};}});
 const m=msg({documentMessage:{mimetype:'application/pdf',fileName:'Blueprint.pdf',caption:'para el Black Friday de Norte'}});
 await p.recibir(m);
 await p.recibir(msg({documentMessage:{mimetype:'text/csv',fileName:'operaciones.csv'}}));await p.esperar();
 assert.deepEqual(pedidos,[{archivo:`2026-10-30-1811-wa-${m.key.id}.pdf`,id:'wa:'+m.key.id,nombre:'Blueprint.pdf',pedido:'para el Black Friday de Norte'}]);
 assert.deepEqual(enviados,[]);assert.ok(reacciones.some(r=>r.id===m.key.id&&r.emoji==='📄'));
 assert.ok(leer().includes(`archivo: [[inbox-adjuntos/2026-10-30-1811-wa-${m.key.id}.pdf]]`));
});

test('responder a un mensaje de Alfred le pasa a Alfred lo que había escrito (para «rehazlo para Norte»)',async()=>{
 const pedidos=[];
 const {p}=montar({alfred:async(texto,emit)=>{pedidos.push(texto);await emit({t:'fin'});}});
 await p.recibir(msg({extendedTextMessage:{text:'rehazlo para Norte',contextInfo:{participant:ALFRED,stanzaId:'X1',quotedMessage:{conversation:'🎬 Gancho\n→ Para rehacer: 2026-11-03 Autor - Título'}}}}));await p.esperar();
 assert.equal(pedidos.length,1);
 assert.match(pedidos[0],/^\[Responde a tu mensaje: «🎬 Gancho\n→ Para rehacer: 2026-11-03 Autor - Título»\] rehazlo para Norte$/);
});

// ---------- a chat, not a command line (04/11: «Cómo vas Alfred», «?», «Pero no me contestas…» went to the inbox) ----------
test('su nombre en cualquier parte le llega; después la charla sigue 10 min sin repetirlo; «idea:» y los enlaces van al inbox',async()=>{
 let t=T+30;const pedidos=[];
 const {p,reacciones}=montar({reloj:()=>t,alfred:async(texto,emit)=>{pedidos.push(texto);await emit({t:'say',texto:'Aquí estoy.'});}});
 await p.recibir(msg({conversation:'Cómo vas Alfred'},{t:T}));await p.esperar();
 await p.recibir(msg({conversation:'?'},{t:T+20}));await p.esperar();
 await p.recibir(msg({conversation:'Pero no me contestas por aquí o qué'},{t:T+25}));await p.esperar();
 assert.deepEqual(pedidos.map(t=>t.replace(/^\[Seguís la conversación\.[^\]]*\] /,'')),['Cómo vas Alfred','?','Pero no me contestas por aquí o qué']);   // in a chat he is told it may be an idea
 await p.recibir(msg({conversation:'idea: app para gimnasios'},{t:T+28}));
 await p.recibir(msg({conversation:'https://elpais.com/x'},{t:T+29}));await p.esperar();
 assert.equal(pedidos.length,3);
 assert.equal(reacciones.filter(r=>r.emoji==='📥').length,2);
 t+=11*60;                                                                  // the chat is over: a plain line is an idea again
 await p.recibir(msg({conversation:'Comprar proteína'},{t:t-5}));await p.esperar();
 assert.equal(pedidos.length,3);
 assert.match(leer(),/Comprar proteína/);
});

test('«recuérdame…», «avísame…» o «añade al calendario…» le llegan a Alfred sin nombrarle; «apunta» sigue yendo al inbox',async()=>{
 const pedidos=[];
 const {p}=montar({alfred:async(texto,emit)=>{pedidos.push(texto);await emit({t:'fin'});}});
 for(const t of ['Recuérdame que tengo dentista el miércoles a las 9','avísame mañana a las 8','Añade al calendario la cena del viernes','apunta comprar huevos'])await p.recibir(msg({conversation:t}));
 await p.esperar();
 assert.deepEqual(pedidos,['Recuérdame que tengo dentista el miércoles a las 9','avísame mañana a las 8','Añade al calendario la cena del viernes']);
 assert.ok(leer().includes('apunta comprar huevos'));
});

test('con la app cerrada, el puente la abre y Alfred contesta igual; las órdenes también',async()=>{
 let abierta=false,veces=0;const pedidos=[],ordenes=[];
 const cerrada=()=>{const e=Error('cerrada');e.dormido=true;return e;};
 const {p,enviados}=montar({despertar:async()=>{veces++;abierta=true;return true;},
  alfred:async(texto,emit)=>{if(!abierta)throw cerrada();pedidos.push(texto);await emit({t:'say',texto:'Apuntado para el miércoles.'});}});
 await p.recibir(msg({conversation:'Recuérdame el dentista el miércoles a las 9'}));await p.esperar();
 assert.equal(veces,1);assert.deepEqual(pedidos,['Recuérdame el dentista el miércoles a las 9']);
 assert.deepEqual(enviados.map(e=>e.texto),['Apuntado para el miércoles.']);
 const falla=montar({dormido:true,despertar:async()=>false});
 const m=msg({conversation:'Alfred, ¿qué tengo hoy?'});await falla.p.recibir(m);await falla.p.esperar();
 assert.match(falla.enviados.at(-1).texto,/No he podido abrir la app/);assert.ok(leer().includes('<!-- wa:'+m.key.id));
});

test('«Alfredo» o una palabra que contiene «alfred» no le llaman',async()=>{
 const pedidos=[];
 const {p}=montar({alfred:async(texto)=>{pedidos.push(texto);}});
 await p.recibir(msg({conversation:'Llamar a Alfredo el lunes'}));await p.esperar();
 assert.deepEqual(pedidos,[]);
});

test('Agent Bar: a vote or a reply goes to the waiting Claude Code conversation, and only while it waits', () => {
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'ab-'));
 const f='123-456.json';
 assert.equal(agentBar('h:2026-11-06:1','',dir),null);                       // not Agent Bar's: untouched
 assert.equal(agentBar('ab:'+f+':c','',dir),'tarde');                         // nobody waiting
 fs.writeFileSync(path.join(dir,f+'.waiting'),'');
 assert.equal(agentBar('ab:'+f+':c','',dir),'enviado');
 assert.deepEqual(JSON.parse(fs.readFileSync(path.join(dir,f+'.decision'),'utf8')),{action:'continue',text:'continúa'});
 assert.equal(agentBar('ab:'+f,'usa Postgres',dir),'enviado');                // a reply quoting the message: its text
 assert.deepEqual(JSON.parse(fs.readFileSync(path.join(dir,f+'.decision'),'utf8')),{action:'continue',text:'usa Postgres'});
 assert.equal(agentBar('ab:'+f+':p','',dir),'parado');
 assert.deepEqual(JSON.parse(fs.readFileSync(path.join(dir,f+'.decision'),'utf8')),{action:'release'});
 assert.equal(agentBar('ab:../../etc/passwd:c','',dir),null);                  // no paths
 fs.rmSync(dir,{recursive:true,force:true});
});

// 07/11 23:30: the connection dropped while Alfred was answering (the Mac asleep on battery wakes for a few seconds) and three
// texts and a video were lost with «Connection Closed». Now they wait in the queue and go through when WhatsApp reconnects.
const cerrada=()=>Object.assign(Error('Connection Closed'),{output:{statusCode:428}});
test('la conexión se cae a mitad: el mensaje queda en cola en disco y Alfred contesta al reconectar, una sola vez',async()=>{
 const dir=fs.mkdtempSync(path.join(raiz,'cola-'));let caida=true;const pedidos=[];
 const alfred=async(texto,emit)=>{pedidos.push(texto);await emit({t:'say',texto:'Pierna: sentadilla 4×6 a 100 kg.'});};
 const a=montar({alfred,cola:colaEnCarpeta(dir),enviar:async()=>{if(caida)throw cerrada();}});
 const m=msg({conversation:'Alfred, ¿qué entreno hoy?'});
 await a.p.recibir(m);await a.p.esperar();
 assert.deepEqual(a.enviados,[]);assert.equal(a.p.pendientes(),1);
 assert.ok(fs.existsSync(path.join(dir,m.key.id+'.json')));
 assert.ok(a.log.some(l=>/^mensaje en cola \(1\/\d\), lo reintento al reconectar: Connection Closed$/.test(l)),a.log.join('\n'));
 assert.ok(!leer().includes('<!-- wa:'+m.key.id));
 caida=false;
 await a.p.reanudar({todo:true});await a.p.esperar();
 assert.deepEqual(a.enviados.map(e=>e.texto),['Pierna: sentadilla 4×6 a 100 kg.']);
 assert.equal(a.p.pendientes(),0);assert.deepEqual(fs.readdirSync(dir),[]);
 await a.p.recibir(m);await a.p.reanudar({todo:true});await a.p.esperar();   // WhatsApp delivering it again changes nothing
 assert.equal(a.enviados.length,1);assert.equal(pedidos.length,2);
 assert.ok(!leer().includes('<!-- wa:'+m.key.id));
});

test('la cola sobrevive a un reinicio del puente: lo pendiente se procesa en la siguiente conexión',async()=>{
 const dir=fs.mkdtempSync(path.join(raiz,'cola-'));
 const alfred=async(texto,emit)=>emit({t:'say',texto:'ejemplo.com: lo miro y te digo.'});
 const antes=montar({alfred,cola:colaEnCarpeta(dir),enviar:async()=>{throw cerrada();}});
 const m=msg({conversation:'Alfred, mira ejemplo.com'});
 await antes.p.recibir(m);await antes.p.esperar();   // the answer cannot be sent…
 assert.equal(fs.readdirSync(dir).length,1);
 const despues=montar({alfred,cola:colaEnCarpeta(dir)});   // …and the bridge restarts before it reconnects
 await despues.p.reanudar({todo:true});await despues.p.esperar();
 assert.deepEqual(despues.enviados.map(e=>e.texto),['ejemplo.com: lo miro y te digo.']);
 assert.deepEqual(fs.readdirSync(dir),[]);
 // an emoji that cannot be sent does not hold the message back: the idea is already in the inbox
 const idea=montar({cola:colaEnCarpeta(dir),enviar:async()=>{throw cerrada();}});
 const i=msg({conversation:'Que el puente guarde lo que no pudo mandar'});
 await idea.p.recibir(i);await idea.p.esperar();
 assert.ok(leer().includes('<!-- wa:'+i.key.id+' -->'));assert.deepEqual(fs.readdirSync(dir),[]);
 assert.ok(idea.log.includes('reacción no enviada: Connection Closed'));
});

test('un vídeo que no se descarga se reintenta; si nunca se puede, queda pendiente en el inbox con ⚠️',async()=>{
 let falla=1;
 const a=montar({descargar:async(m)=>{if(falla-->0)throw Error('fetch failed');return Buffer.from('vídeo '+m.key.id);}});
 const v=msg({videoMessage:{caption:'mira este formato',mimetype:'video/mp4'}});
 await a.p.recibir(v);await a.p.esperar();
 assert.ok(!leer().includes('<!-- wa:'+v.key.id));assert.equal(a.p.pendientes(),1);
 await a.p.reanudar({todo:true});await a.p.esperar();
 assert.ok(leer().includes(`· mira este formato\n  vídeo: [[inbox-adjuntos/2026-10-30-1811-wa-${v.key.id}.mp4]] <!-- wa:${v.key.id} -->`));
 assert.deepEqual(a.reacciones,[{id:v.key.id,emoji:'📥'}]);
 const nunca=montar({descargar:async()=>{throw Error('fetch failed');}});
 const w=msg({videoMessage:{mimetype:'video/mp4'}});
 await nunca.p.recibir(w);await nunca.p.esperar();
 for(let i=1;i<MAX_INTENTOS;i++){await nunca.p.reanudar({todo:true});await nunca.p.esperar();}
 assert.ok(leer().includes(`· ⚠️ vídeo sin descargar <!-- wa:${w.key.id} -->`),leer());
 assert.deepEqual(nunca.reacciones,[{id:w.key.id,emoji:'⚠️'}]);assert.equal(nunca.p.pendientes(),0);
});

test('si la conexión no vuelve, o el fallo es otro, el mensaje no se pierde: queda pendiente en el inbox con su marca',async()=>{
 const a=montar({alfred:async()=>{throw Error('la app ha respondido 500');}});
 const m=msg({conversation:'Alfred, apunta que mañana llamo al gestor'});
 await a.p.recibir(m);await a.p.esperar();
 assert.ok(leer().includes(`· Alfred, apunta que mañana llamo al gestor ⚠️ sin procesar <!-- wa:${m.key.id} -->`));
 assert.equal(a.p.pendientes(),0);
 const b=montar({alfred:async(t,emit)=>emit({t:'say',texto:'Vale.'}),enviar:async()=>{throw cerrada();}});
 const n=msg({conversation:'Alfred, ¿y el viernes?'});
 await b.p.recibir(n);await b.p.esperar();
 for(let i=1;i<MAX_INTENTOS;i++){await b.p.reanudar({todo:true});await b.p.esperar();}
 assert.ok(leer().includes(`· Alfred, ¿y el viernes? ⚠️ sin procesar <!-- wa:${n.key.id} -->`));
 assert.equal(b.p.pendientes(),0);
 assert.ok(b.log.includes('mensaje no procesado: Connection Closed'));
});
