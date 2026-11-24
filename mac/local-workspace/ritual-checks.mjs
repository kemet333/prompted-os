// The ritual greeting without Claude or ElevenLabs: synthetic data folder, local lines, the prepared line and its limits.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';

const root=path.dirname(fileURLToPath(import.meta.url));
const require=createRequire(process.env.PROMPTED_OS_DEPS_DIR?path.join(process.env.PROMPTED_OS_DEPS_DIR,'package.json'):path.resolve(root,'../../alfred-os/package.json'));
const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'ritual-checks-')),data=path.join(tmp,'data'),vault=path.join(tmp,'vault');
fs.mkdirSync(data);fs.mkdirSync(path.join(vault,'d'),{recursive:true});
fs.writeFileSync(path.join(vault,'d','diario.md'),'### 2026-11-09 · Pierna\n- REALIZADO: prensa 100 kg.\n');
fs.writeFileSync(path.join(data,'configuracion.json'),JSON.stringify({vault,vida:{diarioEntrenamiento:'d/diario.md'}}));
process.env.PROMPTED_OS_DATA_DIR=data;process.env.PROMPTED_OS_VAULT_DIR=vault;process.env.PROMPTED_OS_HEALTH_SHORTCUT_DIR=path.join(tmp,'sin-atajo');
await require('esbuild').build({entryPoints:[path.join(root,'ritual.ts')],outfile:path.join(tmp,'r.mjs'),bundle:true,platform:'node',format:'esm',target:'node26',external:['@anthropic-ai/claude-agent-sdk'],logLevel:'warning'});
const r=await import(pathToFileURL(path.join(tmp,'r.mjs')).href);
let passed=0;const check=async(name,fn)=>{await fn();passed++;console.log('PASS '+name);};
const at=(s)=>new Date(s);

try{
 fs.mkdirSync(path.join(data,'hoy'));
 fs.writeFileSync(path.join(data,'hoy','2026-11-13.json'),JSON.stringify({fecha:'2026-11-13',agenda:'10:00 Dentista · 18:30 Gimnasio',items:[{t:'Llamar al gestor',hecho:true},{t:'Grabar el reel de TF',hecho:false}]}));
 const f=await r.gather(data,at('2026-11-13T17:05:00+01:00'));
 await check('the facts are the real ones of the moment',()=>{
  assert.equal(f.parte,'tarde');assert.equal(f.agenda,'10:00 Dentista · 18:30 Gimnasio');
  assert.deepEqual(f.tareas,['grabar el reel de TF']);
  assert.equal(r.hablado('Pon el enlace https en la bio de Norte: https://x.y/z'),'pon el enlace en la bio de Norte:');assert.equal(f.hechas,1);assert.deepEqual(f.entreno,{titulo:'Pierna',dias:4});
 });
 await check('local lines vary, fit the hour and only use those facts',()=>{
  let seed=1;const rnd=()=>((seed=seed*16807%2147483647)/2147483647);
  const lines=new Set(Array.from({length:40},()=>r.local(f,[],'',rnd)));
  assert.ok(lines.size>=8,'only '+lines.size+' different lines');
  for(const l of lines){assert.doesNotMatch(l,/Buenos días|Buenas noches/);assert.doesNotMatch(l,/Dentista/,'a past event is not announced');}
  assert.ok([...lines].some(l=>/18:30 tienes Gimnasio/.test(l)));
 });
 await check('a line just said is not said again',()=>{let n=0;const rnd=()=>(n++%7)/7;const a=r.local(f,[],'',rnd);for(let i=0;i<10;i++)assert.notEqual(r.local(f,[a],'',rnd),a);});
 await check('the prompt carries the facts, the owner\'s tone and the lines to avoid',()=>{
  const p=r.prompt(f,['Buenas tardes, Owner. Tú mandas.'],'¿Qué pasa, Jefe?');
  assert.match(p,/18:30 Gimnasio/);assert.match(p,/Jefe/);assert.match(p,/Buenas tardes, Owner\. Tú mandas\./);assert.match(p,/no inventes/);
 });
 await check('Claude\'s reply is cleaned, and nonsense refused',()=>{
  assert.equal(r.clean('«Buenas tardes, Owner. El gimnasio te espera a las seis y media.»'),'Buenas tardes, Owner. El gimnasio te espera a las seis y media.');
  assert.equal(r.clean('Hola'),null);assert.equal(r.clean('palabra '.repeat(60)),null);
 });
 await check('a prepared line is used once, only while it fits the moment',async()=>{
  fs.mkdirSync(path.join(data,'ritual'),{recursive:true});
  const ready=(o)=>fs.writeFileSync(path.join(data,'ritual','siguiente.json'),JSON.stringify({text:'Buenas tardes, Owner. Gimnasio a las seis y media.',fecha:'2026-11-13',parte:'tarde',hecho:new Date().toISOString(),...o}));
  ready({parte:'mañana'});
  assert.equal((await r.greeting(data,'',at('2026-11-13T17:06:00+01:00'))).source,'local','a morning line in the afternoon is not used');
  ready({});
  const g=await r.greeting(data,'',at('2026-11-13T17:07:00+01:00'));
  assert.deepEqual(g,{text:'Buenas tardes, Owner. Gimnasio a las seis y media.',source:'claude'});
  assert.equal((await r.greeting(data,'',at('2026-11-13T17:08:00+01:00'))).source,'local','used once');
  assert.equal(r.history(data).length,3);
 });
 console.log('RITUAL CHECKS PASSED: '+passed);
}finally{fs.rmSync(tmp,{recursive:true,force:true});}
process.exit(0);   // greeting() schedules the next preparation; never run it here
