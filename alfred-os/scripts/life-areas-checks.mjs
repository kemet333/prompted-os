import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL,fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url),temp=mkdtempSync(join(tmpdir(),'life-areas-'));
try{
 await require('esbuild').build({entryPoints:[fileURLToPath(new URL('../lib/life-areas.ts',import.meta.url))],outfile:join(temp,'model.mjs'),bundle:true,platform:'node',format:'esm',logLevel:'silent'});
 const model=await import(pathToFileURL(join(temp,'model.mjs')));
 const project=(id,division,extra={})=>({id,division,name:id,line:'',stage:'',next:'',repo:'',evidence:'',...extra});
 const list=[project('tienda','trabajo'),project('idea','proyectos'),project('fitness','personal'),project('casa','vida'),project('curso','aprender'),project('learning','personal'),project('sleep-project','x',{lifeArea:'personal',lifeCategory:'sleep'}),project('new-business','x',{lifeArea:'business'}),project('unknown','unknown')];
 const all=model.lifeAreas.flatMap(a=>model.areaProjects(list,a.id));
 assert.deepEqual(all.map(p=>p.id).sort(),list.map(p=>p.id).sort(),'every project appears once across the life areas');
 assert.equal(new Set(all.map(p=>p.id)).size,list.length);
 const at=id=>model.placement(list.find(p=>p.id===id));
 assert.equal(at('tienda').area,'business');assert.equal(at('tienda').unit,'trabajo');
 assert.equal(at('idea').unit,'proyectos');
 assert.equal(at('fitness').area,'personal');assert.equal(at('fitness').category,'health');
 assert.equal(at('casa').area,'personal');assert.equal(at('casa').category,'organization');
 assert.equal(at('curso').area,'growth','a project in aprender is growth');
 assert.equal(at('learning').area,'growth');
 assert.equal(at('sleep-project').category,'sleep');
 assert.equal(at('new-business').unit,'unassigned','a business project without a known unit stays visible for classification');
 assert.equal(model.placement(project('id','trabajo',{lifeArea:'personal',lifeCategory:'sleep'})).area,'personal','explicit metadata takes precedence over the unit');
 assert.equal(model.placement(project('id','x',{lifeArea:'growth',lifeCategory:'sleep'})).category,'learning','invalid cross-area category falls back visibly');
 assert.equal(model.matchesProject(project('tienda','trabajo',{name:'Tienda online'}),'tienda'),true);
 console.log('LIFE AREAS CHECKS PASSED: isolation, completeness, metadata, units');
}finally{rmSync(temp,{recursive:true,force:true});}
