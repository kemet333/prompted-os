'use client';
import {useCallback,useEffect,useId,useLayoutEffect,useRef,useState,type CSSProperties} from 'react';
import type {Project} from '../lib/alfred-data';
import {projectLabel} from '../lib/life-areas';
import type {ProjectBranch} from './projects-branch-map';
import './projects.css';
import './projects-constellation.css';

type Line={id:string;d:string;color:string;kind:'core'|'branch';x1:number;y1:number;x2:number;y2:number};
const COLORS:Record<string,string>={prompted:'#b4a6d7',norte:'#d5b77b',social:'#90c8c3',unassigned:'#a5b8d0'};
const QUADRANTS=[{area:'tl',side:'left'},{area:'tr',side:'right'},{area:'bl',side:'left'},{area:'br',side:'right'}];
type Props={branches:ProjectBranch[];active:string;onBranch:(id:string)=>void;selectedId?:string;focusId:string;onOpen:(p:Project,target?:HTMLElement)=>void;query:string};
// The original colored project constellation, scoped to Business. Reuses its measured curves and HTML nodes.
export default function ProjectsConstellation({branches,active,onBranch,selectedId,focusId,onOpen,query}:Props){
 const scene=useRef<HTMLDivElement>(null),core=useRef<HTMLSpanElement>(null),hubs=useRef<Record<string,HTMLElement|null>>({}),dots=useRef<Record<string,HTMLElement|null>>({});
 const latest=useRef(branches);latest.current=branches;
 const [lines,setLines]=useState<Line[]>([]),[size,setSize]=useState({w:0,h:0}),[narrow,setNarrow]=useState(false),[hover,setHover]=useState<string|null>(null);
 const uid=useId().replace(/:/g,''),structure=JSON.stringify(branches.map(b=>[b.id,b.name,b.projects.map(p=>[p.id,projectLabel(p),p.stage])]));
 const measure=useCallback(()=>{
  const el=scene.current;if(!el)return;const box=el.getBoundingClientRect(),small=box.width<920;setNarrow(small);
  const at=(node?:HTMLElement|null)=>{if(!node||!node.isConnected)return null;const r=node.getBoundingClientRect();return {x:r.left-box.left+r.width/2,y:r.top-box.top+r.height/2,r:r.width/2};};
  const c=at(core.current),next:Line[]=[];
  for(const b of latest.current){
   const h=at(hubs.current[b.id]);if(!h)continue;const color=COLORS[b.id]||COLORS.unassigned;
   if(c&&!small){const mx=(c.x+h.x)/2;next.push({id:'core-'+b.id,color,kind:'core',x1:c.x,y1:c.y,x2:h.x,y2:h.y,d:`M${c.x} ${c.y} C${mx} ${c.y} ${mx} ${h.y} ${h.x} ${h.y}`});}
   for(const p of b.projects){const t=at(dots.current[p.id]);if(!t)continue;
    const dir=t.x<h.x?-1:1,sx=h.x+dir*(h.r+6),ex=t.x-dir*(t.r+3),mx=(sx+ex)/2;
    const top=h.y+h.r+4,bend=Math.min(10,Math.max(0,t.y-top));
    next.push({id:p.id,color,kind:'branch',x1:small?h.x:sx,y1:small?top:h.y,x2:small?t.x-t.r:ex,y2:t.y,d:small?`M${h.x} ${top} V${t.y-bend} Q${h.x} ${t.y} ${h.x+bend} ${t.y} H${t.x-t.r}`:`M${sx} ${h.y} C${mx} ${h.y} ${mx} ${t.y} ${ex} ${t.y}`});
   }
  }
  setSize({w:box.width,h:box.height});setLines(next);
 },[]);
 useLayoutEffect(()=>{
  measure();const el=scene.current;if(!el)return;let live=true;const ro=new ResizeObserver(measure);ro.observe(el);
  document.fonts?.ready.then(()=>{if(live)measure();}).catch(()=>{});
  return()=>{live=false;ro.disconnect();};
 },[measure,structure,narrow]);
 useEffect(()=>{let frame=0;const end=performance.now()+400;function tick(){measure();if(performance.now()<end)frame=requestAnimationFrame(tick);}frame=requestAnimationFrame(tick);return()=>cancelAnimationFrame(frame);},[Boolean(selectedId),measure]);
 const lit=(id:string)=>id===selectedId||id===hover||(!selectedId&&id===focusId);
 return <div className={'lc-tree'+(narrow?' lc-narrow':'')} aria-label="Árbol de empresas y proyectos">
  <div className="lc-tree-caption"><span>Mapa de proyectos</span><small>Tus empresas</small></div>
  <div className="pj-scene" ref={scene}>
   <svg className="pj-lines" width={size.w} height={size.h} viewBox={`0 0 ${size.w||1} ${size.h||1}`} aria-hidden="true">
    <defs><filter id={uid+'-soft'}><feGaussianBlur stdDeviation="3"/></filter>{lines.map(l=><linearGradient key={l.id} id={uid+'-'+l.id} gradientUnits="userSpaceOnUse" x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2}><stop offset="0" stopColor={l.color} stopOpacity={l.kind==='core'?.1:1}/><stop offset="1" stopColor={l.color} stopOpacity=".5"/></linearGradient>)}</defs>
    <g filter={`url(#${uid}-soft)`} className="lc-glow">{lines.map(l=><path key={l.id} d={l.d} fill="none" stroke={l.color}/>)}</g>
    {lines.map(l=><path key={l.id} d={l.d} className={'pj-line '+l.kind+(lit(l.id)?' on':'')} style={{'--c':l.color,stroke:`url(#${uid}-${l.id})`} as CSSProperties}/>)}
   </svg>
   <div className="pj-core"><span className="pj-core-ring" ref={core}><i/></span><span className="pj-core-word">ALFRED</span></div>
   {branches.map((b,i)=>{const pos=QUADRANTS[i%4],color=COLORS[b.id]||COLORS.unassigned;return <section key={b.id} className={`pj-area pj-${pos.area} pj-${pos.side}`+(active===b.id?' chosen':'')} style={{'--c':color,'--i':i} as CSSProperties}>
    <button className="pj-hub" aria-pressed={active===b.id} onClick={()=>onBranch(b.id)} aria-label={b.name+', '+b.projects.length+' proyectos'}><span className="pj-hub-ring" ref={el=>{hubs.current[b.id]=el;}}><span className="pj-hub-count">{b.projects.length}</span></span><span className="pj-hub-name">{b.name}</span></button>
    <ul className="pj-list">{b.projects.map(p=><li key={p.id}><button className={'pj-project'+(lit(p.id)?' on':'')+(p.id===focusId?' focus':'')+(p.id===selectedId?' selected':'')} aria-current={p.id===selectedId?'true':undefined} onClick={e=>onOpen(p,e.currentTarget)} onPointerEnter={()=>setHover(p.id)} onPointerLeave={()=>setHover(null)} onFocus={()=>setHover(p.id)} onBlur={()=>setHover(null)}><span className="pj-dot" ref={el=>{dots.current[p.id]=el;}}/><span className="pj-copy"><strong>{projectLabel(p)}{p.id===focusId&&<em>Foco</em>}</strong><small title={p.stage}>{p.stage}</small></span></button></li>)}{!b.projects.length&&<li className="lc-empty">{query?'Sin coincidencias':'Sin proyectos documentados'}</li>}</ul>
   </section>;})}
  </div>
 </div>;
}
