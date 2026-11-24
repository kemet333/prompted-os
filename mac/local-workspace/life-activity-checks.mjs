// Synthetic records only. Verifies what the dashboard is allowed to count.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';
const root=path.dirname(fileURLToPath(import.meta.url)),temp=await fs.mkdtemp(path.join(os.tmpdir(),'life-activity-'));
const require=createRequire(path.join(process.env.PROMPTED_OS_DEPS_DIR||path.resolve(root,'../../alfred-os'),'package.json'));
let passed=0;
const check=(name,run)=>{run();passed++;console.log('PASS '+name);};
try{
 await require('esbuild').build({entryPoints:[path.resolve(root,'../../alfred-os/lib/life-activity.ts')],bundle:true,platform:'node',format:'esm',outfile:path.join(temp,'activity.mjs'),logLevel:'silent'});
 const {activityRecords,activityTimeline,localDay,activityKind}=await import(pathToFileURL(path.join(temp,'activity.mjs')).href);
 const entry=(id,date,status='REALIZADO',certain=true,title='Fuerza')=>({id,date,title,status,dateCertain:certain,details:['Synthetic only'],truncated:false});
 const data={local:true,training:{status:'ready',note:null,updatedAt:null,sessions:[entry('yes','2026-11-01'),entry('partial','2026-10-30','PARCIAL'),entry('planned','2026-11-02','PLANIFICADO'),entry('uncertain','2026-10-29','REALIZADO',false),entry('future','2026-12-01'),entry('old','2025-01-01')]},health:{status:'ready',data:{workouts:[{date:'2026-11-01T10:00:00Z',end:'2026-11-01T11:00:00Z',activity:'Running',source:'Synthetic Watch',minutes:60}]}},capabilities:{healthImport:true}};
 const now=new Date(2026,10,2,12);
 check('planned, partial, uncertain, future and out-of-period sessions do not count',()=>{const t=activityTimeline(activityRecords(data,'diary'),13,now);assert.equal(t.total,1);assert.equal(t.counts.strength,1);});
 check('unconfirmed and planned records remain accessible in history',()=>{const r=activityRecords(data,'diary');assert.equal(r.length,6);assert.equal(r.find(r=>r.id==='uncertain').certain,false);assert.equal(r.find(r=>r.id==='planned').performed,false);});
 check('diary and Health are separate totals, never added',()=>{assert.equal(activityTimeline(activityRecords(data,'diary'),13,now).counts.running,0);assert.equal(activityTimeline(activityRecords(data,'health'),13,now).counts.running,1);});
 check('Monday-aligned weeks include seven columns of days without future activity',()=>{const t=activityTimeline(activityRecords(data,'diary'),13,now);assert.equal(t.days.length,91);assert.equal(new Date(t.start+'T12:00:00').getDay(),1);assert.equal(new Date(t.end+'T12:00:00').getDay(),0);assert.equal(t.days.filter(d=>d.future).length,6);});
 check('DST and leap day do not duplicate or skip calendar dates',()=>{for(const day of [new Date(2026,2,30),new Date(2026,9,26),new Date(2024,1,29)]){const t=activityTimeline([],13,day);assert.equal(new Set(t.days.map(d=>d.day)).size,91);assert.ok(t.days.some(d=>d.day===localDay(day)));}});
 check('Health measurements keep their timestamp and attribution',()=>{const w=activityRecords(data,'health')[0];assert.equal(w.source,'Synthetic Watch');assert.equal(w.date,'2026-11-01T10:00:00Z');assert.deepEqual(w.measures,['60 min']);assert.equal(w.day,localDay(new Date(w.date)));});
 check('empty sources are empty rather than demo data',()=>{assert.deepEqual(activityRecords(null,'health'),[]);assert.equal(activityTimeline([],52,now).total,0);});
 check('labels use explicit titles rather than guessed weights or activity',()=>{assert.equal(activityKind('Empuje'), 'strength');assert.equal(activityKind('Tracciones'),'strength');assert.equal(activityKind('Running'),'running');assert.equal(activityKind('Sesión no especificada'),'other');assert.equal(activityKind('Pierna (dictado por WhatsApp)'),'strength');assert.equal(activityKind('Torso'),'strength');});
 await require('esbuild').build({entryPoints:[path.resolve(root,'../../alfred-os/lib/workout-progress.ts')],bundle:true,platform:'node',format:'esm',outfile:path.join(temp,'progress.mjs'),logLevel:'silent'});
 const {exerciseLine,exerciseProgress,daysSinceGroups}=await import(pathToFileURL(path.join(temp,'progress.mjs')).href);
 check('a diary line gives its exercise and top load, never reps as kilos',()=>{
  assert.deepEqual([exerciseLine('Press inclinado mancuernas: 28, 30 y 30 kg, 3×8.')].map(e=>[e.id,e.kg,e.scheme]),[['press-inclinado',30,'3×8']]);
  assert.equal(exerciseLine('Aperturas en máquina de pectoral: 100 y 86 kg, 3×8–10').kg,100);
  assert.equal(exerciseLine('REALIZADO: dominadas, calentamiento a peso corporal ×10; +5 kg ×6–7; +10 kg ×4.').kg,10);
  assert.equal(exerciseLine('Gemelos: 3×15, 10 kg').kg,10);
  assert.equal(exerciseLine('Press plano con barra, 27,5 kg/lado + barra de 20 kg (75 kg totales): 2 series × 5–6 reps.').kg,75);
  assert.equal(exerciseLine('Press francés plano barra Z: 15 kg/lado + barra de peso desconocido, 3×10–12.'),null,'per side without a total is not comparable');
  assert.equal(exerciseLine('Curl predicador en máquina: unos 59 kg (aprox.), 3×8–10').approx,true);
  assert.equal(exerciseLine('Incidencia: molestia lumbar tras 80 kg'),null,'only known exercises');
  assert.equal(exerciseLine('Fondos: 3×8 (lastre no indicado).'),null,'no load, no entry');});
 check('progress per exercise: one top load per session, last against previous, the best one and a new record',()=>{
  const rec=(day,details,title='Empuje',status='REALIZADO',certain=true)=>({id:day,day,date:day,certain,title,status,performed:status==='REALIZADO',kind:'strength',source:'Diario de Alfred',details,truncated:false,measures:[]});
  const p=exerciseProgress([rec('2026-11-10',['Aperturas en máquina: 100 y 86 kg, 3×8']),rec('2026-10-12',['Aperturas en máquina: 79 kg, 3×10','Aperturas en máquina: 82 kg']),rec('2026-10-20',['Aperturas: 999 kg'],'Empuje','PLANIFICADO'),rec('2026-10-25',['Aperturas: 999 kg'],'Empuje','REALIZADO',false)]);
  assert.equal(p.length,1);assert.deepEqual(p[0].entries.map(e=>e.kg),[82,100],'planned and undated sessions never count');
  assert.equal(p[0].previous.kg,82);assert.equal(p[0].best.kg,100);assert.equal(p[0].record,true);
  assert.deepEqual(exerciseProgress([rec('2026-11-01',['Press militar: 41 kg'],'Empuje')]).map(x=>x.record),[false],'a first session is not a record');
  const g=daysSinceGroups([rec('2026-11-10',[],'Torso'),rec('2026-11-09',[],'Pierna')],'2026-11-11');
  assert.deepEqual(g.map(x=>[x.id,x.days]),[['empuje',1],['tracciones',1],['pierna',2]]);
  assert.equal(daysSinceGroups([],'2026-11-11')[0].days,null);});
 console.log('LIFE_ACTIVITY_OK '+passed);
}finally{await fs.rm(temp,{recursive:true,force:true});}
