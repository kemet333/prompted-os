// End to end, without Claude: a workout told in the WhatsApp group reaches Alfred, Alfred may write it in the training diary
// without asking (the gym, 09–10/11/2026), the line he writes in the format the butler prompt gives him is what Vida reads,
// and the loads come out per exercise. Synthetic vault and data only; never Owner's real diary.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';

const root=path.dirname(fileURLToPath(import.meta.url));
const require=createRequire(process.env.PROMPTED_OS_DEPS_DIR?path.join(process.env.PROMPTED_OS_DEPS_DIR,'package.json'):path.resolve(root,'../../alfred-os/package.json'));
const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'entreno-checks-')),vault=path.join(tmp,'vault'),data=path.join(tmp,'data');
const relativo='Personal/Salud y deporte/01-Registros/Diario-de-entrenamiento.md',diario=path.join(vault,relativo);
fs.mkdirSync(path.dirname(diario),{recursive:true});fs.mkdirSync(data);
fs.writeFileSync(diario,'# Diario de entrenamiento\n\n### 2026-11-07 · Empuje\n- REALIZADO: press inclinado mancuernas: 26 kg, 3×8.\n');
fs.writeFileSync(path.join(data,'configuracion.json'),JSON.stringify({vault,vida:{diarioEntrenamiento:relativo}}));
process.env.PROMPTED_OS_DATA_DIR=data;process.env.PROMPTED_OS_VAULT_DIR=vault;process.env.PROMPTED_OS_ANSWER_WINDOW_MS='30';
process.env.PROMPTED_OS_HEALTH_SHORTCUT_DIR=path.join(tmp,'sin-atajo');

const entry=path.join(tmp,'entry.ts');
fs.writeFileSync(entry,[
 `export * as voice from ${JSON.stringify(path.join(root,'voice.ts'))};`,
 `export {MAYORDOMO} from ${JSON.stringify(path.join(root,'mayordomo.ts'))};`,
 `export {lifeView} from ${JSON.stringify(path.join(root,'life.ts'))};`,
 `export {activityRecords} from ${JSON.stringify(path.resolve(root,'../../alfred-os/lib/life-activity.ts'))};`,
 `export {exerciseProgress,daysSinceGroups} from ${JSON.stringify(path.resolve(root,'../../alfred-os/lib/workout-progress.ts'))};`,
].join('\n'));
await require('esbuild').build({entryPoints:[entry],outfile:path.join(tmp,'e.mjs'),bundle:true,platform:'node',format:'esm',target:'node26',external:['@anthropic-ai/claude-agent-sdk'],logLevel:'warning'});
const m=await import(pathToFileURL(path.join(tmp,'e.mjs')).href);
const {crearPuente}=await import(pathToFileURL(path.resolve(root,'../../whatsapp/logica.mjs')).href);
let passed=0;const check=async(name,fn)=>{await fn();passed++;console.log('PASS '+name);};

try{
 // The example the prompt gives Alfred is the contract: Vida must read it.
 const ejemplo=m.MAYORDOMO.match(/«(- Press inclinado[^»]+)»/)?.[1];
 await check('the butler prompt carries the diary format, with an example line',()=>assert.ok(ejemplo&&/kg/.test(ejemplo)&&/\d×\d/.test(ejemplo),ejemplo));

 const permisos=[],enviados=[];
 const T=Math.floor(Date.parse('2026-11-10T13:48:00+01:00')/1000);
 const turn={id:'t',emit:e=>{if(e.t==='permission')permisos.push(e);},buffer:'',spoken:'',done:()=>{},urls:[]};
 // Alfred, as the real one would after reading the prompt: an Edit at the end of the diary, through the same permission rule.
 const alfred=async(texto,emit)=>{
  assert.match(texto,/press inclinado/i);
  m.voice.forChecks.setTurn(turn);
  const nuevo=`### 2026-11-10 · Torso (dictado por WhatsApp)\n- REALIZADO: dominadas, peso corporal ×10; +5 kg ×6–7; +10 kg ×4.\n${ejemplo}\n- Press militar máquina: 45 y 41 kg, 3×8–10.\n`;
  const antes=fs.readFileSync(diario,'utf8'),viejo=antes.trimEnd().split('\n').at(-1);
  const r=await m.voice.forChecks.canUseTool('Edit',{file_path:diario,old_string:viejo,new_string:viejo+'\n'+nuevo},{signal:new AbortController().signal});
  m.voice.forChecks.setTurn(null);
  if(r.behavior!=='allow'){await emit({t:'say',texto:'No he podido apuntarlo.'});return;}
  fs.writeFileSync(diario,antes.replace(viejo,viejo+'\n'+nuevo));
  await emit({t:'say',texto:'Apuntado.'});
 };
 const p=crearPuente({soy:['alfred@s'],esDueno:k=>k.participant==='owner@s',ahora:()=>T+30,
  enviar:async t=>{enviados.push(t);},reaccionar:async()=>{},descargar:async()=>Buffer.from(''),guardar:()=>'',
  inbox:async()=>({escrita:true}),transcribir:async()=>'',alfred,permiso:()=>{},visto:()=>false,log:()=>{},
  orden:async()=>{},pregunta:()=>null});
 await p.recibir({key:{id:'E1',remoteJid:'g@g.us',participant:'owner@s',fromMe:false},messageTimestamp:T,
  message:{conversation:'Bueno te digo mi entreno: dominadas +10 kg ×4, press inclinado 28-30-30 kg 3x8 y press militar 45 y 41 kg'}});
 await p.esperar();

 await check('a workout told by WhatsApp is written in the diary without asking for permission',()=>{
  assert.deepEqual(permisos,[],'no permission request reached the phone');
  assert.deepEqual(enviados,['Apuntado.']);
  assert.ok(fs.readFileSync(diario,'utf8').includes('### 2026-11-10 · Torso'));
 });
 const v=await m.lifeView(data),records=m.activityRecords(v,'diary');
 await check('Vida reads it as a performed strength session',()=>{
  assert.equal(v.training.status,'ready');
  assert.deepEqual([records[0].day,records[0].status,records[0].kind],['2026-11-10','REALIZADO','strength']);
 });
 await check('and shows each load, compared with the last time',()=>{
  const by=Object.fromEntries(m.exerciseProgress(records).map(x=>[x.id,x]));
  assert.equal(by['press-inclinado'].last.kg,30);assert.equal(by['press-inclinado'].previous.kg,26);assert.equal(by['press-inclinado'].record,true);
  assert.equal(by['press-militar'].last.kg,45);assert.equal(by.dominadas.last.kg,10);
  assert.deepEqual(m.daysSinceGroups(records,'2026-11-11').map(g=>g.days),[1,1,null]);
 });
 await check('another note of the vault still asks',async()=>{
  m.voice.forChecks.setTurn(turn);
  const r=await m.voice.forChecks.canUseTool('Edit',{file_path:path.join(vault,'Personal/otra.md'),old_string:'a',new_string:'b'},{signal:new AbortController().signal});
  m.voice.forChecks.setTurn(null);
  assert.equal(r.behavior,'deny');assert.equal(permisos.length,1);
 });
 console.log('ENTRENO CHECKS PASSED: '+passed);
}finally{fs.rmSync(tmp,{recursive:true,force:true});}
