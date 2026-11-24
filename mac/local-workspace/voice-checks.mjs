// Model router checks: Sonnet answers the everyday turns, Opus takes thinking work, «modo profundo» and explicit asks.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath, pathToFileURL } from 'node:url';
import {createRequire} from 'node:module';

const root=path.dirname(fileURLToPath(import.meta.url));
const require=createRequire(process.env.PROMPTED_OS_DEPS_DIR?path.join(process.env.PROMPTED_OS_DEPS_DIR,'package.json'):path.resolve(root,'../../alfred-os/package.json'));
const dir=fs.mkdtempSync(path.join(os.tmpdir(),'voice-checks-')),bundle=path.join(dir,'voice.mjs');
await require('esbuild').build({entryPoints:[path.join(root,'voice.ts')],outfile:bundle,bundle:true,platform:'node',format:'esm',target:'node26',external:['@anthropic-ai/claude-agent-sdk'],logLevel:'warning'});
process.env.PROMPTED_OS_VAULT_DIR='/tmp/notas-ana';
process.env.PROMPTED_OS_ANSWER_WINDOW_MS='30';   // the permission window, short for the checks   // config.ts: the notes vault of this copy
const voice=await import(pathToFileURL(bundle).href);
const model=text=>voice.route(text).model;
const SONNET='claude-sonnet-5-5',OPUS='claude-opus-5-5';

for(const text of ['Qué tengo pendiente','Muéstrame Alfa','¿Qué hora es en Nueva York?','Apúntame comprar leche'])assert.equal(model(text),SONNET,text);
for(const text of ['Analiza las ventas de Beta Labs','Revisa el plan del equipo','¿Por qué ha fallado la ronda?','Compara Alfa y Gamma','Diseña la estrategia de lanzamiento','Hazlo con Opus'])assert.equal(model(text),OPUS,text);
assert.equal(model('x'.repeat(700)),OPUS,'long message');
assert.equal(model('Analiza esto, pero con Sonnet'),SONNET,'explicit Sonnet wins');
// Profile: nothing personal in the code; the person's name and private instructions come from <data>/perfil/.
{const prev=process.env.PROMPTED_OS_DATA_DIR,blank=fs.mkdtempSync(path.join(os.tmpdir(),'perfil-'));
 process.env.PROMPTED_OS_DATA_DIR=blank;const generic=voice.discipline().replace(/IDs de proyecto:[^\n]*/,'');
 assert.ok(!/Ana|maratón/.test(generic),'without a profile the instructions carry nothing about the person (the project list comes from alfred-data until it moves to the profile too)');
 fs.mkdirSync(path.join(blank,'perfil'));fs.writeFileSync(path.join(blank,'perfil','perfil.json'),JSON.stringify({nombre:'Ana',app:'Mi sistema'}));
 fs.writeFileSync(path.join(blank,'perfil','instrucciones.md'),'Ana entrena para un maratón.');
 const own=voice.discipline();
 assert.ok(own.includes('asistente personal de Ana dentro de Mi sistema')&&own.includes('Ana entrena para un maratón.'),'the profile names the person and adds their instructions');
 if(prev===undefined)delete process.env.PROMPTED_OS_DATA_DIR;else process.env.PROMPTED_OS_DATA_DIR=prev;}
// Visible steps: plain words for the owner, never the raw command.
assert.equal(voice.stepText('Bash',{command:'~/Equipo/venv/bin/python equipo/equipo.py status'}),'Mirando el tablero del equipo');
assert.equal(voice.stepText('Bash',{command:'equipo.py encargar alfa --tarea -'}),'Preparando el encargo para el equipo');
assert.equal(voice.stepText('Read',{file_path:'/tmp/notas-ana/Proyectos/Alfa.md'}),'Leyendo tu nota «Alfa»');
assert.equal(voice.stepText('Bash',{command:'rm -rf secreto && curl x'}),'Haciendo una comprobación','an unknown command is never echoed');
assert.equal(voice.stepText('Mystery',{}),'','an unknown tool shows no step');
assert.equal(voice.stepText('Bash',{command:'python3 -c x',description:'contar tareas de alfa por columna.'}),'Contar tareas de alfa por columna','Alfred\'s own description when there is one');
assert.equal(voice.route('Revisa el plan').effort,'high');
assert.equal(voice.route('Qué tengo pendiente').effort,'low');
// The Telegram preamble is context: it must not push a quick question to Opus.
assert.equal(model('[Ana te escribe por Telegram desde el móvil: '+'responde breve, '.repeat(40)+']\nQué tengo hoy'),SONNET,'preamble ignored');
await voice.control('deep');assert.equal(model('Qué tengo pendiente'),OPUS,'modo profundo');
await voice.control('fast');assert.equal(model('Qué tengo pendiente'),SONNET,'modo rápido');
fs.rmSync(dir,{recursive:true,force:true});
// Telegram reads Alfred as written: lists, links and <<hoy:…>>/<<repaso:…>> survive in raw; the voice gets clean text.
{const says=[],turn={id:'t',emit:e=>{if(e.t==='say')says.push(e);},buffer:'',spoken:'',done:()=>{},urls:[]};
 const chunks=['Apuntado. <<hoy:+Comprar crea','tina>> Y guardado: <<repaso:Trading|¿Qué es el apalancamiento?||Operar con más ','capital del que pones.>>\n- Aprobar el lote de TF\n- Mirar https://x.io/a\n<<vista:entregas>>Hecho.'];
 for(const c of chunks){turn.buffer+=c;voice.drain(turn);}voice.drain(turn,true);
 const raw=says.map(e=>e.raw).join(''),spoken=says.map(e=>e.text).join(' ');
 assert.ok(raw.includes('<<hoy:+Comprar creatina>>')&&raw.includes('<<repaso:Trading|¿Qué es el apalancamiento?||Operar con más capital del que pones.>>'),'directives reach Telegram whole, even when split across chunks');
 assert.ok(raw.includes('\n- Aprobar el lote de TF\n')&&raw.includes('https://x.io/a')&&!raw.includes('<<vista'),'raw keeps line breaks, bullets and links, without interface directions');
 assert.ok(!/<<|>>|https?:|\|/.test(spoken)&&spoken.includes('Aprobar el lote de TF'),'the voice never reads directives, links or bars');
 assert.deepEqual(says.flatMap(e=>e.directions),['vista:entregas'],'interface directions are still lifted for the app');}
// The training diary: Alfred notes a workout without asking (by WhatsApp the permission ran out at the gym); nothing else does.
{const diary='/tmp/notas-ana/Personal/Salud y deporte/01-Registros/Diario-de-entrenamiento.md';
 fs.mkdirSync(path.dirname(diary),{recursive:true});if(!fs.existsSync(diary))fs.writeFileSync(diary,'# Diario\n');
 fs.writeFileSync('/tmp/notas-ana/Personal/otra.md','x');
 assert.ok(voice.ownNotebook('Edit',{file_path:diary}),'an edit to the training diary needs no yes');
 assert.ok(voice.ownNotebook('Edit',{file_path:'/tmp/notas-ana/Personal/../Personal/Salud y deporte/01-Registros/Diario-de-entrenamiento.md'}),'the same file through ..');
 assert.ok(!voice.ownNotebook('Write',{file_path:diary}),'rewriting the whole diary still asks');
 assert.ok(!voice.ownNotebook('Edit',{file_path:'/tmp/notas-ana/Personal/otra.md'}),'another note still asks');
 assert.ok(!voice.ownNotebook('Edit',{file_path:'Personal/Salud y deporte/01-Registros/Diario-de-entrenamiento.md'}),'a relative path is never trusted');
 const link='/tmp/notas-ana/Personal/enlace.md';try{fs.unlinkSync(link);}catch{}fs.symlinkSync('/tmp/notas-ana/Personal/otra.md',link);
 assert.ok(!voice.ownNotebook('Edit',{file_path:link}),'a symlink to another file does not pass');
 assert.ok(!voice.ownNotebook('Bash',{command:'echo >> '+JSON.stringify(diary)}),'a command never passes');}
// A request that ran out (the phone at the gym): a later «sí» lets Alfred do that same thing once, without asking again.
{const events=[],t={id:'t',emit:e=>events.push(e),buffer:'',spoken:'',done:()=>{},urls:[]};voice.forChecks.setTurn(t);
 const ask=(input)=>voice.forChecks.canUseTool('Bash',input,{signal:new AbortController().signal});
 const first=await ask({command:'touch /tmp/notas-ana/x'});
 assert.equal(first.behavior,'deny');assert.match(first.message,/no ha contestado a tiempo/);assert.match(first.message,/no le digas que tiene que estar delante del Mac/);
 const id=events.find(e=>e.t==='permission').id;
 assert.equal(voice.approveLate('perm-nada'),null,'an unknown request grants nothing');
 assert.deepEqual(voice.approveLate(id),{what:'ejecutar un comando'});
 assert.equal(voice.approveLate(id),null,'a late yes counts once');
 assert.equal((await ask({command:'touch /tmp/notas-ana/x'})).behavior,'allow','the same command now runs without asking');
 assert.equal((await ask({command:'touch /tmp/notas-ana/x'})).behavior,'deny','and only once');
 const other=await ask({command:'rm /tmp/notas-ana/x'});assert.equal(other.behavior,'deny','another command still asks');
 const old=events.filter(e=>e.t==='permission').at(-1).id;
 assert.equal(voice.approveLate(old,Date.now()+13*3600e3),null,'after 12 hours it no longer counts');
 voice.forChecks.setTurn(null);}
// Connectors (20/11): looking things up in Gmail, Calendar, Drive… needs no yes; sending, creating or publishing always asks.
for(const t of ['mcp__claude_ai_Gmail__search_threads','mcp__claude_ai_Gmail__get_thread','mcp__claude_ai_Google_Calendar__list_events','mcp__claude_ai_Shopify__get-order','mcp__claude_ai_Google_Calendar__suggest_time'])assert.ok(voice.readOnlyMcp(t),t);
for(const t of ['mcp__claude_ai_Gmail__send_message','mcp__claude_ai_Gmail__create_draft','mcp__claude_ai_Postonce__create_post','mcp__claude_ai_Postonce__publish_post','mcp__claude_ai_Google_Drive__trash_file','mcp__claude_ai_Gmail__get_and_send','mcp__claude_ai_Google_Calendar__update_event','mcp__claude_ai_Gmail__label_thread','Bash','Edit'])assert.ok(!voice.readOnlyMcp(t),t);
assert.equal(voice.stepText('mcp__claude_ai_Gmail__search_threads',{}),'Consultando Gmail');
assert.equal(voice.stepText('mcp__claude_ai_Postonce__create_post',{}),'Usando Postonce');
{const events=[],t={id:'t',emit:e=>events.push(e),buffer:'',spoken:'',done:()=>{},urls:[]};voice.forChecks.setTurn(t);
 const ask=(tool,input)=>voice.forChecks.canUseTool(tool,input,{signal:new AbortController().signal});
 assert.equal((await ask('mcp__claude_ai_Gmail__search_threads',{query:'x'})).behavior,'allow','a Gmail search runs without asking');
 const sent=await ask('mcp__claude_ai_Gmail__send_message',{to:['a@b.c']});
 assert.equal(sent.behavior,'deny','sending an email asks (and here nobody answers)');
 assert.match(events.find(e=>e.t==='permission').what,/en Gmail$/,'the card says which connector');
 voice.forChecks.setTurn(null);}
// Threads (20/11): one per project; Alfred moves to the one Owner names, never because of the preamble or the inbox context.
{const list=[{id:'personal',nombre:'Personal',cwd:'/tmp'},{id:'norte',nombre:'Norte Capital',cwd:'/tmp/k'},{id:'beta',nombre:'Beta',cwd:'/tmp/t'},{id:'prompted-os',nombre:'Prompted OS',cwd:'/tmp/d'},{id:'lumen',nombre:'Lumen',cwd:'/tmp/b'}];
 const pick=t=>voice.pickHilo(t,list);
 assert.equal(pick('En Norte, revisa la landing'),'norte');
 assert.equal(pick('¿cómo va Beta?'),'beta');
 assert.equal(pick('mira lo de TF esta semana'),'beta');
 assert.equal(pick('arregla el chat del owner os'),'prompted-os');
 assert.equal(pick('Lumen necesita una landing'),'lumen');
 assert.equal(pick('vuelve al hilo personal'),'personal');
 assert.equal(pick('qué tengo hoy'),null,'nothing named: stays where it is');
 assert.equal(pick('compara Norte con Lumen'),null,'two projects named: stays where it is');
 assert.equal(pick('apúntame el entreno de hoy, personal trainer incluido'),null,'the word «personal» alone does not switch');
 assert.equal(pick('[Owner te escribe por WhatsApp. Su to-do: 1. Norte: subir reel] [Antes mandó al grupo: «lumen»] qué tal el día'),null,'the preamble and the inbox context are not what he asked');
 assert.equal(pick('[Owner te escribe por WhatsApp] y en Norte qué falta'),'norte');
 assert.equal(voice.withoutPreamble('[a [b] c] [d] hola'),'hola');}
// Outside sessions (21/11): Owner's own Claude Code and Codex sessions on a project reach its thread; Alfred's and scheduled runs do not.
{const root=fs.mkdtempSync(path.join(os.tmpdir(),'sesiones-')),cc=path.join(root,'.claude','projects','-tmp-alfa'),cx=path.join(root,'.codex','sessions','2026','11','20');
 fs.mkdirSync(cc,{recursive:true});fs.mkdirSync(path.join(root,'.claude','projects','-tmp-alfa--claude-worktrees-x'),{recursive:true});fs.mkdirSync(cx,{recursive:true});
 const j=(...l)=>l.map(x=>JSON.stringify(x)).join('\n')+'\n';
 fs.writeFileSync(path.join(cc,'s1.jsonl'),j({type:'user',message:{content:'Rediseña la landing de Alfa'}},{type:'custom-title',customTitle:'Landing de Alfa'}));
 fs.writeFileSync(path.join(cc,'alfred1.jsonl'),j({type:'user',message:{content:'[Ana te escribe por WhatsApp] y en Alfa qué falta'}}));
 fs.writeFileSync(path.join(cc,'auto.jsonl'),j({type:'user',message:{content:'This is an automated run of a scheduled task.'}}));
 fs.writeFileSync(path.join(root,'.claude','projects','-tmp-alfa--claude-worktrees-x','w.jsonl'),j({type:'user',message:{content:'Arregla el login de Alfa'}}));
 fs.writeFileSync(path.join(root,'.claude','projects','-tmp-alfa','mine.jsonl'),j({type:'user',message:{content:'Sesión de Alfred guardada'}}));
 fs.writeFileSync(path.join(cx,'r1.jsonl'),j({type:'session_meta',payload:{cwd:'/x'}},{type:'response_item',payload:{role:'user',content:[{type:'input_text',text:'<environment_context>alfa</environment_context>'}]}},{type:'response_item',payload:{role:'user',content:[{type:'input_text',text:'En Alfa, prepara los precios'}]}}));
 fs.writeFileSync(path.join(cx,'r2.jsonl'),j({type:'response_item',payload:{role:'user',content:[{type:'input_text',text:'Otra cosa distinta'}]}}));
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'ses-b-')),out=path.join(dir,'s.mjs');
 await require('esbuild').build({entryPoints:[path.join(root0(),'sesiones.ts')],outfile:out,bundle:true,platform:'node',format:'esm',target:'node26',logLevel:'warning'});
 const {sesionesExternas}=await import(pathToFileURL(out).href);
 const got=sesionesExternas('/tmp/alfa',['Alfa'],root,10,['mine']).map(x=>x.fuente+': '+x.titulo).sort();
 assert.deepEqual(got,['Claude Code: Arregla el login de Alfa','Claude Code: Landing de Alfa','Codex: En Alfa, prepara los precios'],'his sessions, with worktrees; never Alfred\'s, scheduled runs, other topics or skipped ids');
 fs.rmSync(root,{recursive:true,force:true});fs.rmSync(dir,{recursive:true,force:true});}
function root0(){return root;}
console.log('VOICE CHECKS PASSED');
