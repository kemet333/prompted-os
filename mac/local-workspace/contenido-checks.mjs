// Contenido checks, on a fake content-machine database, a fake vault plan and a fake full-visual folder (never the real ones):
// the calendar joins the three sources, times and «ya publicado» are written where they belong, and media is served only
// from the content folders.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath, pathToFileURL } from 'node:url';
import {createRequire} from 'node:module';
import {DatabaseSync} from 'node:sqlite';

const root=path.dirname(fileURLToPath(import.meta.url));
const require=createRequire(process.env.PROMPTED_OS_DEPS_DIR?path.join(process.env.PROMPTED_OS_DEPS_DIR,'package.json'):path.resolve(root,'../../alfred-os/package.json'));
const dir=fs.mkdtempSync(path.join(os.tmpdir(),'contenido-checks-')),vault=path.join(dir,'vault'),out=path.join(dir,'output'),reels=path.join(dir,'reels'),data=path.join(dir,'data');
const db=path.join(dir,'rf','machine.db');
for(const d of [path.join(vault,'01-Projects/alfa/contenido'),out,reels,data,path.dirname(db)])fs.mkdirSync(d,{recursive:true});
// a reel the machine knows (manual, two openings) and one it sent to PostOnce
const proj=path.join(out,'2026-10-17-full-visual-aa-bot'),loose=path.join(out,'2026-10-19-full-visual-bb-40-cuentas'),old=path.join(out,'2026-10-16-full-visual-prueba');
for(const d of [proj,loose,old])fs.mkdirSync(d);
for(const f of ['aa-bot-h1.mp4','aa-bot-h2.mp4','cover-h1.jpg'])fs.writeFileSync(path.join(proj,f),'x'.repeat(2000));
for(const f of ['bb-40-h1.mp4','bb-40-h1.v1.mp4','cover-h1.jpg'])fs.writeFileSync(path.join(loose,f),'v');
fs.writeFileSync(path.join(loose,'caption.txt'),'Comenta CASO y te mando el caso.\nUn trader ha quemado más de 40 cuentas.\n\nSigue a @beta.labs.');
fs.writeFileSync(path.join(old,'p.mp4'),'v');
fs.writeFileSync(path.join(reels,'reel1-2.9-stop.mp4'),'v');fs.writeFileSync(path.join(reels,'reel1-2.9-stop-corto.mp4'),'v');
fs.writeFileSync(path.join(dir,'secreto.jpg'),'no');
const sql=new DatabaseSync(db);
sql.exec(`CREATE TABLE pieces (id INTEGER PRIMARY KEY, brand TEXT, family TEXT, parent_id INTEGER, origin TEXT, origin_ref TEXT, channel TEXT, format TEXT, title TEXT, hook TEXT, script TEXT,
 caption TEXT, hashtags TEXT, media_brief TEXT, media_path TEXT, media_url TEXT, wanted_at TEXT, status TEXT, review_note TEXT, meta TEXT, created_at TEXT, updated_at TEXT);
 CREATE TABLE publications (id INTEGER PRIMARY KEY, piece_id INTEGER, brand TEXT, channel TEXT, publish_at TEXT, status TEXT DEFAULT 'queued', provider TEXT, provider_post_id TEXT,
 url TEXT, error TEXT, attempts INTEGER DEFAULT 0, idempotency_key TEXT, updated_at TEXT);`);
const fv={state:'manual',project:path.basename(proj),videos:[path.join(proj,'aa-bot-h1.mp4'),path.join(proj,'aa-bot-h2.mp4')],chosen:2,note:'Se suben h1 y h2 como Trial Reels'};
sql.prepare("insert into pieces (id,brand,channel,format,title,hook,caption,status,meta) values (1,'alfa','instagram','reel','No es un bot mágico','Esto no es un bot mágico.','Comenta BOT.','scheduled',?)").run(JSON.stringify({fv}));
sql.prepare("insert into pieces (id,brand,channel,format,title,caption,status) values (2,'beta','x','post','Hilo','Texto del hilo','scheduled')").run();
sql.prepare("insert into publications (id,piece_id,brand,channel,publish_at,status,provider,error,idempotency_key) values (10,1,'alfa','instagram','2099-10-19T18:30:00.000Z','blocked','postonce','Se publica a mano: la máquina no lo sube','a')").run();
sql.prepare("insert into publications (id,piece_id,brand,channel,publish_at,status,provider,idempotency_key) values (11,2,'beta','x','2099-10-20T07:30:00.000Z','sent','postonce','b')").run();
sql.prepare("insert into pieces (id,brand,channel,format,title,status,meta) values (20,'alfa','instagram','reel','Ready without publication','approved',?)").run(JSON.stringify({fv:{state:'rendered',project:'ready-without-publication',videos:[path.join(proj,'aa-bot-h1.mp4')]}}));
sql.prepare("insert into pieces (id,brand,channel,format,title,status,meta) values (21,'alfa','instagram','reel','Queued without publication','approved',?)").run(JSON.stringify({fv:{state:'queued',project:'queued-without-publication'}}));
sql.close();
fs.writeFileSync(path.join(vault,'01-Projects/alfa/contenido/plan-vuelta-2099-10.md'),`# Plan
## Semana 1
### Mié 21 (2099-10-21)
#### Instagram · Reel · 20:00: Reel 1 · El stop que siempre salta
**Gancho en pantalla**
\`\`\`text
Rompe el máximo.
\`\`\`
**Caption**
\`\`\`text
No es mala suerte.
\`\`\`
_Corte vertical del 2.9._
#### Tú · Revisión · 21:00: Revisión de la semana
#### X · Post · 9:05: Mapa del día
**Mensaje**
\`\`\`text
NQ · miércoles
\`\`\`
`);
// the day's folder of the plan: Alfa 10:30 with 3 openings, Beta 17:30 still in the queue
const days=path.join(dir,'Reels'),d22=path.join(days,'2099-10-22');fs.mkdirSync(d22,{recursive:true});
for(const h of ['h1','h2','h3']){fs.writeFileSync(path.join(d22,`aa-1030-${h}.mp4`),'v');fs.writeFileSync(path.join(d22,`aa-1030-${h}.txt`),`Absorption ${h}\n\nComenta ALFA.`);}
{const q=new DatabaseSync(db);q.prepare("insert into pieces (id,brand,channel,format,title,status,meta) values (5,'beta','instagram','reel','El stop a break-even','approved',?)").run(JSON.stringify({trial:true,deliver:{date:'2099-10-22',name:'bb-1730'},fv:{state:'queued'}}));q.close();}
// This copy's configuration: two example accounts (config.ts reads it on import).
process.env.PROMPTED_OS_DATA_DIR=data;
fs.writeFileSync(path.join(data,'configuracion.json'),JSON.stringify({contenido:{planMarca:'alfa',marcas:[
 {id:'alfa',nombre:'Alfa',prefijo:'aa',claves:['-aa-','alfa'],perfiles:[{red:'instagram',usuario:'alfa.studio',nombre:'Alfa | Estudio',bio:'Hecho con calma.',enlace:'alfa.example/links'},{red:'x',nombre:'Alfa'}]},
 {id:'beta',nombre:'Beta',prefijo:'bb',claves:['-bb-','beta'],perfiles:[{red:'instagram',usuario:'beta.labs',nombre:'Beta',bio:'',enlace:'beta.example/comparar?utm_campaign=bio'}]}]}}));
Object.assign(process.env,{PROMPTED_OS_VAULT_DIR:vault,PROMPTED_OS_RF_DB:db,PROMPTED_OS_FV_OUT:out,PROMPTED_OS_COURSE_REELS:reels,PROMPTED_OS_DAY_REELS:days,PROMPTED_OS_CONTENT_PLAN:'01-Projects/alfa/contenido'});
const bundle=path.join(dir,'contenido.mjs');
await require('esbuild').build({entryPoints:[path.join(root,'contenido.ts')],outfile:bundle,bundle:true,platform:'node',format:'esm',target:'node26',logLevel:'warning'});
const c=await import(pathToFileURL(bundle).href);
const ok=(cond,name)=>{console.log((cond?'PASS ':'FAIL ')+name);if(!cond)process.exitCode=1;};
const pub=id=>{const s=new DatabaseSync(db,{readOnly:true});try{return s.prepare('select * from publications where id=?').get(id);}finally{s.close();}};

// Daily analysis lives only in this test's temporary Workspace.
const analisisFile=path.join(data,'contenido','analisis','ultimo.json');
ok(await c.analisis(data)===null,'missing daily analysis stays hidden');
ok(await c.fechaAnalisis(data)===null,'missing daily analysis has no date');
fs.mkdirSync(path.dirname(analisisFile),{recursive:true});
const informeMarca={resumen:'Las aperturas concretas ganan.',
 ganadores:[{titulo:'El stop',red:'instagram',vistas:1200,x_mediana:2.4,url:'https://instagram.com/reel/ejemplo'}],perdedores:[],
 ganchos_que_ganan:['Un dato concreto'],conceptos_que_ganan:['El stop'],evitar:[],
 exprimir:{concepto:'El stop',por_que:'Supera la mediana',variantes:[{gancho:'Dónde ponerlo',angulo:'Un ejemplo'}]},
 validados:['El stop'],repetir:['Apertura concreta'],conclusiones:['Repetir el concepto']};
const informe={fecha:new Date().toISOString(),marcas:{alfa:informeMarca,beta:{...informeMarca,exprimir:null},desconocida:{resumen:42}}};
const escribirAnalisis=value=>fs.writeFileSync(analisisFile,JSON.stringify(value));
escribirAnalisis(informe);
let analizado=await c.analisis(data);
ok(analizado?.fecha===informe.fecha&&analizado.marcas.alfa.ganadores[0].vistas===1200&&analizado.marcas.alfa.exprimir.variantes[0].angulo==='Un ejemplo','daily analysis retains its date, results and variants');
ok(Object.keys(analizado?.marcas||{}).join(',')==='alfa,beta','daily analysis only includes configured brands, ignoring unknown data');
ok((await c.read(data)).analisis?.marcas.alfa.validados[0]==='El stop','read exposes the daily analysis');
escribirAnalisis({...informe,marcas:{alfa:{...informeMarca,resumen:'x'.repeat(1000),conclusiones:['y'.repeat(900)],
 ganadores:[{...informeMarca.ganadores[0],titulo:'z'.repeat(500),url:'javascript:alert(1)'}]}}});
analizado=await c.analisis(data);
ok(analizado?.marcas.alfa.resumen.length===400&&analizado.marcas.alfa.conclusiones[0].length===400&&analizado.marcas.alfa.ganadores[0].titulo.length===160&&analizado.marcas.alfa.ganadores[0].url==='','analysis bounds long text and drops non-web links');
const fechaAntigua=new Date(Date.now()-4*86400_000).toISOString();
escribirAnalisis({...informe,fecha:fechaAntigua});
ok(await c.analisis(data)===null&&(await c.read(data)).analisis===null,'analysis older than three days stays hidden');
ok((await c.read(data)).analisisFecha===fechaAntigua,'read retains the date of analysis older than three days');
escribirAnalisis({...informe,fecha:new Date().toLocaleDateString('sv-SE',{timeZone:'Europe/Madrid'})});
ok((await c.analisis(data))?.marcas.alfa.resumen===informeMarca.resumen,'a report dated today without a time is accepted');
for(const malformed of [null,[],{}, {...informe,fecha:'ayer'}, {...informe,fecha:'2026-02-30'}, {...informe,marcas:[]},
 {...informe,marcas:{alfa:{...informeMarca,resumen:42}}}, {...informe,marcas:{alfa:{...informeMarca,conclusiones:[{}]}}},
 {...informe,marcas:{alfa:{...informeMarca,ganadores:[{...informeMarca.ganadores[0],vistas:'1200'}]}}},
 {...informe,marcas:{alfa:{...informeMarca,exprimir:{...informeMarca.exprimir,variantes:[null]}}}}]){
 escribirAnalisis(malformed);ok(await c.analisis(data)===null,'malformed analysis is rejected: '+JSON.stringify(malformed).slice(0,100));
}
// The real format of the scheduled task: the first brand (Norte) without «repetir», the second (TF) without «exprimir» or «validados».
{const {repetir,...norte}=informeMarca,{exprimir,validados,...tf}=informeMarca;
 escribirAnalisis({fecha:new Date().toISOString(),marcas:{alfa:norte,beta:tf}});
 analizado=await c.analisis(data);
 ok(analizado?.marcas.alfa.repetir.length===0&&analizado.marcas.alfa.exprimir?.concepto==='El stop'&&analizado.marcas.beta.exprimir===null
  &&analizado.marcas.beta.validados.length===0&&analizado.marcas.beta.repetir[0]==='Apertura concreta','the real report format: missing lists are empty and a missing «exprimir» is none');}
escribirAnalisis({...informe,marcas:{alfa:{...informeMarca,repetir:'Apertura concreta'},beta:informeMarca}});
analizado=await c.analisis(data);
ok(Object.keys(analizado?.marcas||{}).join(',')==='beta','a malformed brand is left out without hiding the others');
fs.writeFileSync(analisisFile,'{broken');
ok(await c.analisis(data)===null,'invalid analysis JSON stays hidden');
ok(await c.fechaAnalisis(data)===null,'invalid analysis JSON has no date');
fs.unlinkSync(analisisFile);

let r=await c.read(data);
const bot=r.items.find(i=>i.id==='rf:10'),hilo=r.items.find(i=>i.id==='rf:11');
ok(bot?.estado==='tu'&&bot.videos.length===2&&bot.elegido===1&&bot.portadas.length===1&&bot.horaEditable,'a manual reel of the machine: «lo subes tú», both openings, the chosen one and its cover');
ok(hilo?.estado==='postonce'&&!hilo.horaEditable&&!hilo.marcable,'something already in PostOnce cannot be moved or marked here');
const reel=r.items.find(i=>i.fuente==='plan'&&i.canal==='instagram');
ok(reel?.fecha==='2099-10-21T18:00:00.000Z'&&reel.gancho==='Rompe el máximo.'&&reel.texto==='No es mala suerte.'&&reel.nota==='Corte vertical del 2.9.','a plan post: Madrid time, on-screen hook, caption and note');
ok(reel?.videos[0]?.path===path.join(reels,'reel1-2.9-stop.mp4')&&reel.videos.length===2,'a plan reel finds its video in the Bootcamp reels folder (the full cut first)');
ok(!r.items.some(i=>/Revisión/.test(i.titulo))&&r.items.find(i=>i.canal==='x'&&i.fuente==='plan')?.fecha==='2099-10-21T07:05:00.000Z','the owner\'s own review is not content; «9:05» is read as 09:05');
const suelto=r.items.find(i=>i.fuente==='video');
ok(suelto?.brand==='beta'&&suelto.titulo==='Un trader ha quemado más de 40 cuentas.'&&suelto.videos.length===1&&!suelto.fecha&&!r.items.some(i=>i.id.includes('prueba')),'a finished reel with no date: its brand, a title that is not the call to action, no old versions or tests');
ok(!r.items.some(i=>i.id==='fv:'+path.basename(proj)),'a reel the machine already has is not listed twice');
const readyOnly=r.items.find(i=>i.id==='rf-pieza:20'),queuedOnly=r.items.find(i=>i.id==='rf-pieza:21');
ok(readyOnly?.estado==='tu'&&readyOnly.videos.length===1&&!readyOnly.horaEditable&&!readyOnly.marcable,'a rendered piece without a publication is visible without scheduling or publish controls');
ok(queuedOnly?.estado==='falta'&&queuedOnly.fecha===null,'queued production without a publication is visible and not presented as scheduled');

// changes
await c.reprogramar(data,'rf:10','2099-10-22T19:00:00.000Z');
ok(pub(10).publish_at==='2099-10-22T19:00:00.000Z','a new time is written to the machine\'s calendar');
let refused='';try{await c.reprogramar(data,'rf:11','2099-10-22T19:00:00.000Z');}catch(e){refused=e.message;}
ok(/PostOnce/.test(refused)&&pub(11).publish_at==='2099-10-20T07:30:00.000Z','a publication already sent to PostOnce keeps its time');
await c.marcar(data,'rf:10',true,'https://instagram.com/p/x');
ok(pub(10).status==='published'&&pub(10).url==='https://instagram.com/p/x','«ya está publicado» marks it in the machine, with its link');
await c.marcar(data,'rf:10',false);
ok(pub(10).status==='blocked'&&/a mano/.test(pub(10).error),'«pendiente» puts a manual reel back as the owner\'s to upload');
await c.reprogramar(data,suelto.id,'2099-10-23T10:30:00.000Z');await c.marcar(data,reel.id,true,'javascript:alert(1)');
r=await c.read(data);
ok(r.items.find(i=>i.id===suelto.id)?.fecha==='2099-10-23T10:30:00.000Z'&&r.items.find(i=>i.id===suelto.id)?.estado==='tu','a loose reel gets a date and becomes the owner\'s to upload');
ok(r.items.find(i=>i.id===reel.id)?.estado==='publicado'&&r.items.find(i=>i.id===reel.id)?.url==='','a plan post can be marked as published; a non-web link is dropped');
await c.ocultar(data,suelto.id);
ok(!(await c.read(data)).items.some(i=>i.id===suelto.id),'a loose reel can be hidden');
let bad='';try{await c.reprogramar(data,'rf:10','mañana');}catch(e){bad=e.message;}
ok(/no válida/.test(bad),'a wrong date is refused');

// profiles and media
ok((await c.perfiles(data)).find(p=>p.brand==='alfa'&&p.red==='instagram')?.bio==='Hecho con calma.','The configured bio is the starting point');
await c.guardarPerfil(data,{brand:'beta',red:'instagram',bio:'Compara prop firms. '.repeat(20),usuario:'@beta.labs'});
const tf=(await c.perfiles(data)).find(p=>p.brand==='beta');
ok(tf.bio.length===160&&tf.usuario==='beta.labs'&&tf.enlace.includes('utm_campaign=bio'),'a profile edit is saved (trimmed to the limit), the rest stays');
ok((await c.media(path.join(proj,'aa-bot-h1.mp4')))?.type==='video/mp4'&&(await c.media(path.join(proj,'cover-h1.jpg')))?.type==='image/jpeg','videos and covers in the content folders are served');
ok(await c.media(path.join(dir,'secreto.jpg'))===null&&await c.media(path.join(out,'..','secreto.jpg'))===null&&await c.media('/etc/hosts')===null,'nothing outside the content folders is served');
fs.symlinkSync(path.join(dir,'secreto.jpg'),path.join(out,'enlace.jpg'));
ok(await c.media(path.join(out,'enlace.jpg'))===null,'a symlink pointing outside is refused');
ok(c.madrid('2026-10-25','20:00')==='2026-10-25T19:00:00.000Z'&&c.madrid('2026-10-24','20:00')==='2026-10-24T18:00:00.000Z','Madrid times follow the October clock change');

// the day of the plan
let dd=await c.dia(data,'2099-10-22');
const kc=dd.cuentas.find(x=>x.brand==='alfa'),tfd=dd.cuentas.find(x=>x.brand==='beta');
ok(kc.franjas[0]?.name==='aa-1030'&&kc.franjas[0].aperturas.length===3&&kc.franjas[0].estado==='listo'&&kc.franjas[0].titulo==='Absorption h1','a slot with its 3 openings ready to upload, titled from its text');
ok(tfd.franjas[0]?.name==='bb-1730'&&tfd.franjas[0].estado==='en-cola'&&tfd.franjas[0].titulo==='El stop a break-even','a slot still in the night queue shows with the machine\'s title');
await c.marcarSubido(data,'2099-10-22','aa-1030',true);await c.apuntarVistas(data,'2099-10-22','aa-1030-h2',540);await c.apuntarVistas(data,'2099-10-22','aa-1030-h1',120);
dd=await c.dia(data,'2099-10-22');const k2=dd.cuentas.find(x=>x.brand==='alfa');
ok(k2.franjas[0].estado==='subido'&&k2.franjas[0].aperturas[1].vistas===540&&c.propuesto(k2)?.h==='h2','uploaded is noted, views are kept and the opening with most views is proposed');
// 19:30: the proposal by Telegram (alfa has views → proposal with buttons; beta has no openings yet → nothing)
let av=await c.avisosGanador(data,'2099-10-22');
ok(av.length===1&&av[0].brand==='alfa'&&/apertura 2 de las 10:30/.test(av[0].texto)&&/540 visitas/.test(av[0].texto)&&av[0].botones[0][0].callback_data==='w:aa-1030:h2','the day\'s winner is proposed with «Sí, esa»');
ok(JSON.stringify(c.ganadorBoton('w:aa-1030:h2'))==='{"brand":"alfa","name":"aa-1030","h":"h2"}'&&c.ganadorBoton('w:no')==='no'&&c.ganadorBoton('w:zz-1030:h2')===null&&c.ganadorBoton('w:aa-1030:h7')===null,'the winner buttons say which account, slot and opening');
await c.marcarAviso(data,'2099-10-22',av[0]);
ok((await c.avisosGanador(data,'2099-10-22')).length===0,'each account hears it once a day');
await c.elegirGanador(data,'2099-10-22','alfa','aa-1030','h2');
const g=JSON.parse(fs.readFileSync(path.join(d22,'ganadores.json'),'utf8'));
ok(g['alfa']?.video==='aa-1030-h2.mp4'&&(await c.dia(data,'2099-10-22')).cuentas.find(x=>x.brand==='alfa').ganador?.h==='h2','the winner is written next to the day\'s videos, where the content system reads it');
let refusedN=0;for(const f of [()=>c.elegirGanador(data,'2099-10-22','beta','aa-1030','h2'),()=>c.apuntarVistas(data,'2099-10-22','aa-1030-h9',1),()=>c.dia(data,'../x'),()=>c.apuntarVistas(data,'2099-10-22','aa-1030-h1',-3)])try{await f();}catch{refusedN++;}
ok(refusedN===4,'a winner from another account, a wrong opening, a strange date or negative views are refused');
ok((await c.media(path.join(d22,'aa-1030-h1.mp4')))?.type==='video/mp4','the day\'s videos are served');

// Weekly rhythm and measured publications coexist: a completed slot is not a second publication.
const hoy=new Date().toLocaleDateString('sv-SE',{timeZone:'Europe/Madrid'});
fs.mkdirSync(path.join(days,hoy),{recursive:true});
fs.writeFileSync(path.join(days,hoy,'aa-1030-h1.mp4'),'v');
await c.marcarSubido(data,hoy,'aa-1030',true);
r=await c.read(data);
const ritmoSubido=r.items.find(i=>i.fuente==='ritmo'&&i.estado==='publicado'&&i.brand==='alfa');
ok(!!ritmoSubido&&r.marcas.some(m=>m.id==='alfa')&&Array.isArray(r.porRed),'the weekly calendar and configured accounts survive alongside publication metrics');
ok(!!ritmoSubido&&!c.porMedir({...ritmoSubido,fecha:'2020-01-01T10:00:00Z'},Date.now())&&c.porRed([ritmoSubido]).length===0,'completed rhythm slots neither request metrics nor inflate publication totals');
await c.reprogramar(data,'rf:10',new Date(Date.now()-2*86400_000).toISOString());
await c.marcar(data,'rf:10',true,'https://instagram.com/p/medido');
r=await c.read(data);
ok(r.items.find(i=>i.id==='rf:10')?.porMedir,'a real publication older than a day still requests its metrics');
await c.medir(data,'rf:10',{vistas:1200,visitas:40,clics:8,compras:1});
r=await c.read(data);
const medido=r.items.find(i=>i.id==='rf:10');
ok(medido?.medida?.vistas===1200&&!medido.porMedir&&c.porRed([ritmoSubido,medido])[0]?.publicadas===1,'real measurements persist without double-counting the calendar slot');
let ritmoRefused=false;try{await c.medir(data,ritmoSubido.id,{vistas:1,visitas:0,clics:0,compras:0});}catch{ritmoRefused=true;}
ok(ritmoRefused,'virtual calendar slots cannot be measured as publications');

// The publishing calendar (calendario.json) is what the publisher follows: its real state shows as is, cancelled entries disappear.
const futuro=new Date(Date.now()+86400_000).toLocaleDateString('sv-SE',{timeZone:'Europe/Madrid'});
const vid=path.join(days,hoy,'aa-1030-h1.mp4'),txt=path.join(days,hoy,'aa-1030-h1.txt');fs.writeFileSync(txt,'Primera frase del reel.\n\nComenta ALFA.');
const entrada=(id,estado,extra={})=>({id,fecha:futuro,hora:'13:00',marca:'alfa',red:'instagram',video:vid,texto:txt,origen:'Pendiente del 22/10',estado,postonce_id:null,url:null,nota:'',...extra});
fs.writeFileSync(path.join(data,'contenido','calendario.json'),JSON.stringify({version:1,entradas:[entrada('p','pendiente'),entrada('s','programado'),
 entrada('d','publicado',{url:'https://instagram.com/reel/x'}),entrada('f','fallo',{nota:'vídeo demasiado largo'}),entrada('c','cancelado'),
 entrada('z','pendiente',{marca:'desconocida'}),entrada('h','pendiente',{hora:'25h'})]}));
const tarea=path.join(data,'tarea-publicar.md');fs.writeFileSync(tarea,'x');process.env.PROMPTED_OS_TAREA_PUBLICAR=tarea;
r=await c.read(data);
const cal=Object.fromEntries(r.items.filter(i=>i.fuente==='calendario').map(i=>[i.id,i]));
ok(cal['cal:p']?.estado==='programado'&&/Se publica solo/.test(cal['cal:p'].detalle)&&cal['cal:p'].titulo==='Primera frase del reel.'&&cal['cal:p'].videos[0]?.path===vid,'a pending calendar entry says it publishes by itself, with its video and text');
ok(cal['cal:s']?.estado==='postonce'&&cal['cal:d']?.estado==='publicado'&&cal['cal:d'].url==='https://instagram.com/reel/x'&&cal['cal:f']?.estado==='fallo'&&/demasiado largo/.test(cal['cal:f'].detalle),'scheduled, published and failed entries keep their real state');
ok(!cal['cal:c']&&!cal['cal:z']&&!cal['cal:h']&&!cal['cal:p'].marcable&&!cal['cal:p'].horaEditable,'cancelled, unknown-account and malformed entries stay out, and the app never edits what the publisher owns');
fs.rmSync(tarea);r=await c.read(data);
const apagada=r.items.find(i=>i.id==='cal:p');
ok(apagada?.estado==='programado'&&!/Se publica solo/.test(apagada.detalle)&&/apagada: lo subes tú/.test(apagada.detalle),'with the publishing task switched off, a pending entry says Owner uploads it, not that it goes out by itself');
ok(!r.items.some(i=>i.fuente==='ritmo'&&i.tipo==='Reel bueno'&&i.estado!=='publicado'),'no good reel is planned from yesterday\'s winner any more');

fs.rmSync(dir,{recursive:true,force:true});
console.log(process.exitCode?'CONTENIDO CHECKS FAILED':'CONTENIDO CHECKS PASSED');
