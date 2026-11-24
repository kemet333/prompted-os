// Synthetic records only. No real vault, Health export, credentials or provider calls.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {spawn,execFileSync} from 'node:child_process';
const root=path.dirname(fileURLToPath(import.meta.url)),tmp=await fs.mkdtemp(path.join(os.tmpdir(),'life-checks-')),vault=path.join(tmp,'vault'),data=path.join(tmp,'data');
const helper=process.env.PROMPTED_OS_HEALTH_HELPER;if(!helper)throw Error('Compile HealthImport.swift and set PROMPTED_OS_HEALTH_HELPER.');
const require=createRequire(path.join(process.env.PROMPTED_OS_DEPS_DIR||path.resolve(root,'../../alfred-os'),'package.json'));
await fs.mkdir(vault);await fs.mkdir(data);await fs.writeFile(path.join(data,'configuracion.json'),JSON.stringify({vault,vida:{diarioEntrenamiento:'diario.md'}}));
process.env.PROMPTED_OS_DATA_DIR=data;process.env.PROMPTED_OS_VAULT_DIR=vault;
const atajo=path.join(tmp,'atajo');process.env.PROMPTED_OS_HEALTH_SHORTCUT_DIR=atajo;   // never the real iCloud folder
await require('esbuild').build({entryPoints:[path.join(root,'life.ts')],outfile:path.join(tmp,'life.mjs'),bundle:true,platform:'node',format:'esm',logLevel:'silent'});
const life=await import(pathToFileURL(path.join(tmp,'life.mjs')).href);
await require('esbuild').build({entryPoints:[path.resolve(root,'../../alfred-os/lib/health-shortcut.ts')],outfile:path.join(tmp,'hs.mjs'),bundle:true,platform:'node',format:'esm',logLevel:'silent'});
let count=0,child;
async function check(name,fn){await fn();count++;console.log('PASS '+name);}
const xml=body=>'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE HealthData [<!ELEMENT HealthData ANY>]>\n<HealthData locale="es_ES">'+body+'</HealthData>';
const sample=xml('<ExportDate value="2026-10-31 11:00:00 +0100"/><ActivitySummary dateComponents="2026-10-30" activeEnergyBurned="418.4" activeEnergyBurnedUnit="kJ" activeEnergyBurnedGoal="836.8" appleExerciseTime="32" appleExerciseTimeGoal="30" appleStandHours="9" appleStandHoursGoal="12"/><Record type="HKQuantityTypeIdentifierRestingHeartRate" value="57" unit="count/min" endDate="2026-10-30 08:00:00 +0100" sourceName="Synthetic Watch"/><Record type="HKQuantityTypeIdentifierRestingHeartRate" value="62" unit="count/min" endDate="2026-10-29 08:00:00 +0100" sourceName="Older"/><Record type="HKQuantityTypeIdentifierHeartRateVariabilitySDNN" value="0.041" unit="s" endDate="2026-10-30 08:00:00 +0100" sourceName="Synthetic Watch"/><Record type="HKQuantityTypeIdentifierBodyMass" value="176.36980975" unit="lb" endDate="2026-10-29 10:00:00 +0100" sourceName="Synthetic Scale"/><Record type="HKQuantityTypeIdentifierStepCount" value="1000" unit="count" endDate="2026-10-30 11:00:00 +0100" sourceName="Phone"/><Record type="HKQuantityTypeIdentifierStepCount" value="1000" unit="count" endDate="2026-10-30 11:00:00 +0100" sourceName="Watch"/><Workout workoutActivityType="HKWorkoutActivityTypeRunning" startDate="2026-10-30 10:00:00 +0100" endDate="2026-10-30 10:30:00 +0100" duration="1800" durationUnit="s" totalDistance="3.106855961" totalDistanceUnit="mi" sourceName="Synthetic Strava"/><Workout workoutActivityType="HKWorkoutActivityTypeRunning" startDate="2026-10-30 10:00:00 +0100" endDate="2026-10-30 10:30:00 +0100" duration="30" durationUnit="min" totalDistance="5" totalDistanceUnit="km" sourceName="Synthetic Watch"/>');
let seq=0;
async function parse(body,ok=true){const f=path.join(tmp,'xml-'+seq+++'.xml');await fs.writeFile(f,body);try{const r=execFileSync(helper,[f],{encoding:'utf8',stdio:['ignore','pipe','pipe']});if(!ok)assert.fail('Unsafe/malformed XML accepted');return JSON.parse(r);}catch(e){if(ok)throw e;assert.equal(e.status,1);}}
try{
 await check('diary preserves explicit status, uncertainty and body; overlapping sources stay one entry',()=>{
  const s=life.diarySessions('### 2026-10-20 · Fuerza\n- REALIZADO: carga sin confirmar.\n### 2026-10-21 (fecha no confirmada) · Carrera\n- Estado: PARCIAL\n- Apple y Strava: dos medidas de una sesión.\n### 2026-10-22 · Próxima sesión\n- PLANIFICADO: no completado.\n### 2026-10-23 · Sin datos\n- Pendiente.\n');
  assert.equal(s.length,4);assert.equal(s[0].status,'NO CONFIRMADO');assert.equal(s[1].status,'PLANIFICADO');assert.equal(s[2].dateCertain,false);assert.equal(s[2].status,'PARCIAL');assert.equal(s[3].status,'REALIZADO');assert.match(s[3].details[0],/sin confirmar/);
 });
 await check('invalid dates do not crash or become records',()=>assert.equal(life.diarySessions('### 2026-99-99 · Error\n- REALIZADO\n### 2026-02-30 · Error\n- REALIZADO\n').length,0));
 await check('Markdown status and pending dates retain their meaning',()=>{const s=life.diarySessions('### 2026-10-20 (fecha por confirmar) · Fuerza\n- **Estado:** REALIZADO\n### 2026-10-21 · Fuerza\n**Estado**: PARCIAL\n');assert.equal(s[0].status,'PARCIAL');assert.equal(s[1].status,'REALIZADO');assert.equal(s[1].dateCertain,false);});
 await check('session subsections retain exercises and sensations, excluding later general notes',()=>{const s=life.diarySessions('## 2026-10-20 · Fuerza\nEstado: REALIZADO\n### Ejercicios\n- Synthetic squat 3x5\n### Sensaciones\n- Synthetic note\n## Notas generales\n- Not part of session\n');assert.match(s[0].details.join(' '),/Synthetic squat 3x5/);assert.match(s[0].details.join(' '),/Synthetic note/);assert.doesNotMatch(s[0].details.join(' '),/Not part of session/);assert.equal(s[0].truncated,false);});
 await check('source empty state has no fake records or connection',async()=>{const v=await life.lifeView(data);assert.equal(v.training.status,'missing');assert.deepEqual(v.training.sessions,[]);assert.equal(v.health.status,'missing');assert.equal(v.capabilities.healthImport,true);});
 await check('corrupt summary elements become a recoverable error, not a partial ready state',async()=>{await fs.mkdir(path.join(data,'vida'),{recursive:true});for(const mutation of [{metrics:[null]},{days:[{date:'2026-99-99'}]},{metrics:[{id:'weight',value:'88',unit:'kg',date:'2026-10-30T00:00:00Z',source:'Synthetic'}]},{workouts:[null]},{importedAt:'invalid'}]){await fs.writeFile(path.join(data,'vida','salud.json'),JSON.stringify({schema:1,importedAt:'2026-11-02T00:00:00Z',exportedAt:null,days:[],metrics:[],workouts:[],duplicates:0,...mutation}));const v=await life.lifeView(data);assert.equal(v.health.status,'error');assert.equal(v.health.data,null);}await fs.rm(path.join(data,'vida','salud.json'));});
 await fs.writeFile(path.join(vault,'diario.md'),'### 2026-10-28 · Synthetic session\n- REALIZADO: ejercicio registrado\n');
 await check('configured diary is read locally',async()=>{const v=await life.lifeView(data);assert.equal(v.training.sessions[0].title,'Synthetic session');assert.equal(v.training.note,await fs.realpath(path.join(vault,'diario.md')));assert.ok(v.training.updatedAt);});
 await fs.rm(path.join(vault,'diario.md'));await fs.writeFile(path.join(tmp,'outside.md'),'private');await fs.symlink(path.join(tmp,'outside.md'),path.join(vault,'diario.md'));
 await check('diary symlink cannot escape configured vault',async()=>assert.equal((await life.lifeView(data)).training.status,'error'));
 await check('interrupted raw exports are removed, other files and workspaces remain',async()=>{const folder=life.healthTempFolder(data),other=life.healthTempFolder(data+'-other');await fs.mkdir(folder,{recursive:true});await fs.mkdir(other,{recursive:true});const name='.import-00000000-0000-4000-8000-000000000000.xml';await fs.writeFile(path.join(folder,name),'SYNTHETIC ONLY');await fs.writeFile(path.join(folder,'keep.xml'),'keep');await fs.writeFile(path.join(other,name),'other');await life.cleanInterruptedHealthImports(data);assert.deepEqual(await fs.readdir(folder),['keep.xml']);assert.deepEqual(await fs.readdir(other),[name]);await fs.rm(folder,{recursive:true});await fs.rm(other,{recursive:true});});
 await fs.rm(path.join(vault,'diario.md'));await fs.writeFile(path.join(vault,'diario.md'),'### 2026-10-28 · Synthetic session\n- REALIZADO: ejercicio registrado\n');
 const parsed=await parse(sample);
 await check('native XML accepts Apple DTD and only allowlisted metrics',()=>{assert.equal(parsed.schema,1);assert.equal(parsed.metrics.length,3);assert.equal(parsed.days.length,1);assert.equal(parsed.metrics.find(m=>m.id==='restingHeartRate').value,57);assert.equal(parsed.metrics.find(m=>m.id==='hrv').value,41);assert.ok(Math.abs(parsed.metrics.find(m=>m.id==='weight').value-80)<.01);assert.ok(Math.abs(parsed.days[0].energy-100)<1e-8);assert.ok(Math.abs(parsed.days[0].energyGoal-200)<1e-8);assert.equal(parsed.days[0].exerciseGoal,30);assert.equal(parsed.days[0].steps,undefined);});
 await check('duplicate workout from two apps is one record with source',()=>{assert.equal(parsed.workouts.length,1);assert.equal(parsed.duplicates,1);assert.equal(parsed.workouts[0].source,'Synthetic Watch');assert.equal(parsed.workouts[0].minutes,30);assert.equal(parsed.workouts[0].distanceKm,5);});
 await check('timestamps keep timezone semantics',()=>{assert.equal(parsed.exportedAt,'2026-10-31T10:00:00Z');assert.equal(parsed.workouts[0].date,'2026-10-30T09:00:00Z');});
 for(const [name,bad] of [
  ['malformed XML', '<HealthData><Record>'],
  ['wrong root', '<Other><ActivitySummary dateComponents="2026-10-30"/></Other>'],
  ['no supported data',xml('<Record type="ClinicalRecord" value="private"/>')],
  ['entity expansion','<!DOCTYPE HealthData [<!ENTITY x "abc">]><HealthData>&x;</HealthData>'],
  ['external entity','<!DOCTYPE HealthData [<!ENTITY x SYSTEM "file:///etc/passwd">]><HealthData>&x;</HealthData>'],
  ['UTF16 entity bypass',Buffer.from('<HealthData/>','utf16le')],
  ['excessive depth','<HealthData>'+'<x>'.repeat(40)+'</x>'.repeat(40)+'</HealthData>']
 ])await check('reject '+name,()=>parse(bad,false));
 await check('nonfinite/negative/unknown-unit values omitted',async()=>{const p=await parse(xml('<ActivitySummary dateComponents="2026-10-30" activeEnergyBurned="NaN" appleExerciseTime="-3"/><Record type="HKQuantityTypeIdentifierBodyMass" value="88" unit="unknown" endDate="2026-10-30 00:00:00 +0000"/>'));assert.equal(p.days[0].energy,undefined);assert.equal(p.days[0].exercise,undefined);assert.equal(p.metrics.length,0);});
 child=spawn(process.execPath,[path.join(root,'dist/server.mjs')],{cwd:root,env:{...process.env,PROMPTED_OS_DATA_DIR:data,PROMPTED_OS_VAULT_DIR:vault,PROMPTED_OS_DISABLE_BRIDGE:'1',PROMPTED_OS_DISABLE_TELEGRAM:'1',PROMPTED_OS_CLAUDE_BIN:'/usr/bin/false',PROMPTED_OS_EQUIPO:'',PROMPTED_OS_HEALTH_HELPER:helper},stdio:['pipe','pipe','pipe']});
 const ready=await new Promise((resolve,reject)=>{let buffer='',stderr='';const timer=setTimeout(()=>reject(Error('server startup timeout '+stderr)),10000);child.stderr.on('data',d=>stderr=(stderr+d).slice(-2500));child.stdout.on('data',d=>{buffer+=d;const m=buffer.match(/^READY (.+)$/m);if(m){clearTimeout(timer);resolve(JSON.parse(m[1]));}});child.once('error',reject);child.once('exit',code=>{clearTimeout(timer);reject(Error('server exit '+code+' '+stderr));});});
 const origin=ready.origin||ready.url;let cookie='';
 async function request(route,options={}){return fetch(origin+route,{...options,headers:{...(cookie?{Cookie:cookie}:{}),...options.headers}});}
 await check('GET /api/life requires local session',async()=>assert.equal((await request('/api/life')).status,401));
 await check('POST /api/life/health requires local session',async()=>assert.equal((await request('/api/life/health',{method:'POST',body:sample,headers:{'Content-Type':'application/xml'}})).status,401));
 const bootstrap=await fetch(origin+'/',{headers:{'X-OS-Launch':ready.bootstrapToken||ready.launchToken||ready.token}});cookie=bootstrap.headers.get('set-cookie')?.split(';')[0]||'';assert.ok(cookie,'bootstrap cookie');
 await check('local authenticated dashboard reads configured records',async()=>{const r=await request('/api/life');assert.equal(r.status,200);assert.equal((await r.json()).training.sessions.length,1);});
 await check('cross-origin import denied',async()=>assert.equal((await request('/api/life/health',{method:'POST',headers:{Origin:'https://evil.example','Content-Type':'application/xml'},body:sample})).status,403));
 await check('cross-site import denied',async()=>assert.equal((await request('/api/life/health',{method:'POST',headers:{'Sec-Fetch-Site':'cross-site','Content-Type':'application/xml'},body:sample})).status,403));
 await check('JSON and ZIP are not accepted as XML',async()=>assert.equal((await request('/api/life/health',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).status,415));
 await check('valid import persists aggregates and discards raw XML',async()=>{const r=await request('/api/life/health',{method:'POST',headers:{Origin:origin,'Content-Type':'application/xml'},body:sample});assert.equal(r.status,200);assert.equal((await r.json()).health.data.workouts.length,1);assert.deepEqual(await fs.readdir(path.join(data,'vida')),['salud.json']);assert.equal((await fs.stat(path.join(data,'vida','salud.json'))).mode&0o777,0o600);});
 const before=await fs.readFile(path.join(data,'vida','salud.json'),'utf8');
 await check('invalid import preserves earlier successful import',async()=>{const r=await request('/api/life/health',{method:'POST',headers:{'Content-Type':'application/xml'},body:'invalid'});assert.equal(r.status,422);assert.equal(await fs.readFile(path.join(data,'vida','salud.json'),'utf8'),before);assert.deepEqual(await fs.readdir(path.join(data,'vida')),['salud.json']);});
 await check('health state is read from disk without model activity',async()=>{const v=await (await request('/api/life')).json();assert.equal(v.health.status,'ready');assert.equal(v.health.data.metrics.length,3);assert.equal(v.local,true);assert.ok(v.health.data.importedAt);});
 // The iPhone shortcut: one JSON a day in iCloud Drive, written however Shortcuts writes numbers.
 const shortcut=await import(pathToFileURL(path.join(tmp,'hs.mjs')).href);
 await check('shortcut values are read leniently and impossible ones left out',()=>{
  const d=shortcut.shortcutDay({fecha:'11/11/2026',energía:'1.234 kcal',ejercicio:'32 min','De pie':'9',fcReposo:'300 lpm',vfc:'41,6 ms',peso:'72,4 kg'});
  assert.deepEqual(d.day,{date:'2026-11-11',energy:1234,exercise:32,stand:9});
  assert.deepEqual(d.metrics.map(m=>[m.id,m.value]),[['hrv',41.6],['weight',72.4]]);
  assert.equal(shortcut.shortcutDay({energia:'520,3'},'salud-2026-11-10.json').day.energy,520.3);
  assert.equal(shortcut.shortcutDay({energia:'kcal'},'salud-2026-11-10.json'),null);
  assert.equal(shortcut.shortcutDay({energia:400}),null,'no date, no day');
 });
 await fs.mkdir(atajo);
 await fs.writeFile(path.join(atajo,'salud-2026-11-11.json'),JSON.stringify({fecha:'2026-11-11T23:30:00+01:00',energia:'520,3 kcal',ejercicio:'45',fcReposo:'61 lpm',peso:'79,1'}));
 await fs.writeFile(path.join(atajo,'salud-2026-11-10.json'),'{not json');
 await fs.writeFile(path.join(atajo,'.salud-2026-01-01.json.icloud'),'placeholder');
 await check('shortcut days go on top of the XML import, newest metric wins',async()=>{
  const v=await life.lifeView(data),h=v.health.data;
  assert.equal(v.health.status,'ready');
  assert.deepEqual(h.days.map(d=>d.date),['2026-10-30','2026-11-11']);
  assert.equal(h.days[1].energy,520.3);assert.equal(h.days[1].exercise,45);
  assert.equal(h.metrics.find(m=>m.id==='restingHeartRate').value,61);assert.equal(h.metrics.find(m=>m.id==='weight').value,79.1);
  assert.equal(h.metrics.find(m=>m.id==='hrv').value,41,'the XML reading stays when the shortcut has none');
  assert.equal(h.workouts.length,1);assert.equal(h.shortcut.lastDay,'2026-11-11');
 });
 await check('unchanged shortcut files do not rewrite the summary',async()=>{const f=path.join(data,'vida','salud.json'),a=(await fs.stat(f)).mtimeMs;await new Promise(r=>setTimeout(r,20));await life.lifeView(data);assert.equal((await fs.stat(f)).mtimeMs,a);});
 await check('a new XML import gets the shortcut days back on the next look',async()=>{
  assert.equal((await request('/api/life/health',{method:'POST',headers:{Origin:origin,'Content-Type':'application/xml'},body:sample})).status,200);
  const v=await (await request('/api/life')).json();assert.ok(v.health.data.days.some(d=>d.date==='2026-11-11'));
 });
 console.log('LIFE CHECKS PASSED: '+count);
}finally{
 if(child&&child.exitCode===null){const ended=new Promise(resolve=>child.once('exit',resolve));child.stdin.end();const timer=setTimeout(()=>child.kill('SIGKILL'),5000);await ended;clearTimeout(timer);}
  await fs.rm(life.healthTempFolder(data),{recursive:true,force:true});
  await fs.rm(tmp,{recursive:true,force:true});
}
