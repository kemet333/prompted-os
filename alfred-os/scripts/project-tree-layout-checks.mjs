import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL,fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url),temp=mkdtempSync(join(tmpdir(),'project-tree-'));
try{
 await require('esbuild').build({entryPoints:[fileURLToPath(new URL('../lib/project-tree-layout.ts',import.meta.url))],outfile:join(temp,'layout.mjs'),bundle:true,platform:'node',format:'esm',logLevel:'silent'});
 const {projectTreeLayout:layout,treeCurve}=await import(pathToFileURL(join(temp,'layout.mjs')));
 let scenarios=0;
 for(const width of [720,760,900,1024,1280,1600])for(const branches of [3,4,6])for(const leaves of [0,1,7,18,40]){
  const g=layout(width,branches,leaves);assert.ok(g);assert.equal(g.leaves.length,leaves,'never truncate a project');assert.equal(g.branches.length,branches);
  for(const leaf of g.leaves){assert.ok(leaf.x>g.branches[0].x+g.branches[0].width);assert.ok(leaf.width>=180);assert.ok(leaf.x+leaf.width<=width-15);assert.ok(leaf.y>=32&&leaf.y<=g.height-32);}
  for(let i=1;i<g.leaves.length;i++)assert.ok(g.leaves[i].y-g.leaves[i-1].y>=64,'long two-line leaf labels have room');
  for(let i=1;i<g.branches.length;i++)assert.ok(g.branches[i].y-g.branches[i-1].y>=80,'branches do not overlap');
  for(const b of g.branches)assert.ok(b.y>=32&&b.y<=g.height-32);
  scenarios++;
 }
 assert.equal(layout(719,3,7),null,'small inspector canvas uses vertical tree even on wide viewport');
 assert.equal(layout(390,3,7),null);assert.equal(layout(NaN,3,7),null);
 assert.match(treeCurve(0,20,100,80),/^M0 20C/);
 console.log('PROJECT TREE LAYOUT CHECKS PASSED: '+scenarios+' bounds/completeness scenarios plus compact fallbacks');
}finally{rmSync(temp,{recursive:true,force:true});}
