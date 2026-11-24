// The sessions Owner opened outside Alfred (21/11/2026, «Alfred, una sola puerta», paso 3): his recent Claude Code and Codex
// conversations about a project, so the project's thread knows they exist and can read what was done there («¿qué hicimos
// ayer en Claude con Norte?»). Read-only and cheap: only the head of the newest files, never their whole content.
import {closeSync,existsSync,openSync,readSync,readdirSync,statSync} from 'node:fs';
import {homedir} from 'node:os';
import {join} from 'node:path';

export type Sesion={fuente:'Claude Code'|'Codex';titulo:string;fecha:string;archivo:string};
const HEAD=96*1024;

function head(file:string){
 try{const fd=openSync(file,'r');try{const b=Buffer.alloc(HEAD);const n=readSync(fd,b,0,HEAD,0);return b.subarray(0,n).toString('utf8');}finally{closeSync(fd);}}catch{return '';}
}
function lines(text:string){const out:any[]=[];for(const l of text.split('\n')){if(!l.trim())continue;try{out.push(JSON.parse(l));}catch{}}return out;}
const clean=(t:string)=>t.replace(/<[^>]+>/g,' ').replace(/\s+/g,' ').trim();
const textOf=(c:any):string=>typeof c==='string'?c:Array.isArray(c)?c.map(p=>typeof p==='string'?p:p?.text||'').join(' '):'';
function newest(dir:string,limit:number){
 try{return readdirSync(dir).filter(f=>f.endsWith('.jsonl')).map(f=>{const p=join(dir,f);return {p,t:statSync(p).mtimeMs};}).sort((a,b)=>b.t-a.t).slice(0,limit);}catch{return [];}
}

/** Claude Code: ~/.claude/projects/<repo path with every / and . as ->/ (and its worktrees, «<that>--claude-worktrees-…»). */
function claudeCode(repo:string,root:string){
 const key=repo.replace(/[/.]/g,'-'),base=join(root,'.claude','projects');
 let dirs:string[]=[];try{dirs=readdirSync(base).filter(d=>d===key||d.startsWith(key+'--claude-worktrees-'));}catch{}
 const files=dirs.flatMap(d=>newest(join(base,d),8)).sort((a,b)=>b.t-a.t).slice(0,8);
 return files.map(({p,t})=>{
  const ls=lines(head(p));
  const titled=ls.find(d=>d.type==='custom-title'||d.type==='ai-title'||d.type==='summary');
  const first=ls.find(d=>d.type==='user'&&!d.isMeta&&textOf(d.message?.content).trim());
  const titulo=clean(String(titled?.customTitle||titled?.title||titled?.summary||textOf(first?.message?.content)||'')).slice(0,90);
  // Not his: Alfred's own threads (their messages open with the [context] block) and scheduled runs.
  const own=/^\[/.test(textOf(first?.message?.content).trim())||/^This is an automated run/i.test(titulo);
  return titulo&&!own?{fuente:'Claude Code' as const,titulo,fecha:new Date(t).toISOString(),archivo:p}:null;
 }).filter(Boolean) as Sesion[];
}

/** Codex keeps its sessions by date, run from its own folders: one belongs to the project when its first request names it. */
function codex(names:string[],root:string){
 const base=join(root,'.codex','sessions');if(!existsSync(base))return [];
 const days:string[]=[];
 try{for(const y of readdirSync(base).sort().reverse())for(const m of readdirSync(join(base,y)).sort().reverse())for(const d of readdirSync(join(base,y,m)).sort().reverse()){days.push(join(base,y,m,d));if(days.length>=10)break;}}catch{}
 const files=days.flatMap(d=>newest(d,12)).sort((a,b)=>b.t-a.t).slice(0,40);
 const want=names.map(n=>n.toLowerCase()).filter(n=>n.length>=3);
 const out:Sesion[]=[];
 for(const {p,t} of files){
  const ls=lines(head(p));
  const ask=ls.find(d=>d.type==='response_item'&&d.payload?.role==='user'&&!/^\s*<(environment_context|user_instructions|permissions)/.test(textOf(d.payload?.content?.map?.((c:any)=>c.text))));
  const text=clean(textOf(ask?.payload?.content?.map?.((c:any)=>c.text)));
  if(text&&want.some(n=>text.toLowerCase().includes(n)))out.push({fuente:'Codex',titulo:text.slice(0,90),fecha:new Date(t).toISOString(),archivo:p});
  if(out.length>=5)break;
 }
 return out;
}

/** The newest outside sessions for a project (its repository and its names), newest first. */
export function sesionesExternas(repo:string,names:string[],root=homedir(),limit=6,skip:string[]=[]):Sesion[]{
 return [...claudeCode(repo,root),...codex(names,root)].filter(x=>!skip.some(id=>id&&x.archivo.endsWith(id+'.jsonl'))).sort((a,b)=>b.fecha.localeCompare(a.fecha)).slice(0,limit);
}
