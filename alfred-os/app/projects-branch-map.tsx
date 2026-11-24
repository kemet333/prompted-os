'use client';
import {useId,useLayoutEffect,useRef,useState} from 'react';
import {ArrowUpRight,ChevronRight} from 'lucide-react';
import type {Project} from '../lib/alfred-data';
import {projectLabel} from '../lib/life-areas';
import {projectTreeLayout,treeCurve} from '../lib/project-tree-layout';
import './projects-branch-map.css';

export type ProjectBranch={id:string;name:string;description:string;projects:Project[]};
type Props={title:string;subtitle:string;branches:ProjectBranch[];active:string;onBranch:(id:string)=>void;selectedId?:string;focusId:string;query:string;onOpen:(p:Project,target:HTMLElement)=>void};

export default function ProjectsBranchMap({title,subtitle,branches,active,onBranch,selectedId,focusId,query,onOpen}:Props){
 const container=useRef<HTMLDivElement>(null),[width,setWidth]=useState(0),[hover,setHover]=useState<string|null>(null);
 const gradient='project-branches-'+useId().replace(/:/g,'');
 const branch=branches.find(b=>b.id===active)||branches[0];
 const leaves=branch?.projects||[];
 const layout=projectTreeLayout(width,branches.length,leaves.length);
 useLayoutEffect(()=>{const el=container.current;if(!el)return;const read=()=>setWidth(el.getBoundingClientRect().width);read();const observer=new ResizeObserver(read);observer.observe(el);return()=>observer.disconnect();},[]);
 const root=<div className="la-tree-root"><span className="la-tree-orbit" aria-hidden="true"><i/></span><strong>{title}</strong><small>{subtitle}</small></div>;
 function branchButton(b:ProjectBranch,i:number){const pos=layout?.branches[i];return <button key={b.id} className={'la-tree-branch'+(b.id===active?' on':'')} style={pos?{left:pos.x,top:pos.y,width:pos.width}:undefined} aria-pressed={b.id===active} aria-label={`${b.name}, ${b.projects.length} proyectos`} onClick={()=>onBranch(b.id)} disabled={!!query&&!b.projects.length}>
  <span className="la-tree-number">{b.projects.length.toString().padStart(2,'0')}</span><span className="la-tree-branch-copy"><b>{b.name}</b><small>{b.description}</small></span><ChevronRight size={13} aria-hidden="true"/>
 </button>;}
 function leafButton(p:Project,i:number){const pos=layout?.leaves[i];return <button key={p.id} className={'la-tree-leaf'+(p.id===selectedId?' selected':'')+(p.id===focusId?' focus':'')} style={pos?{left:pos.x,top:pos.y,width:pos.width}:undefined} onClick={e=>onOpen(p,e.currentTarget)} onPointerEnter={()=>setHover(p.id)} onPointerLeave={()=>setHover(null)} onFocus={()=>setHover(p.id)} onBlur={()=>setHover(null)} aria-current={p.id===selectedId?'true':undefined} title={projectLabel(p)}>
  <i className="la-tree-dot" aria-hidden="true"/><span className="la-tree-leaf-copy"><b>{projectLabel(p)}{p.id===focusId&&<em>Foco</em>}</b><small>{p.stage||p.line||'Sin etapa documentada'}</small></span><ArrowUpRight size={14} aria-hidden="true"/>
 </button>;}
 const empty=<p className="la-tree-empty" role="status">{query?'No hay proyectos que coincidan en esta rama.':'Esta área todavía no tiene proyectos asociados.'}</p>;
 return <div ref={container} className={'la-tree'+(layout?' la-tree-wide':' la-tree-compact')} aria-label={'Árbol de '+title}>
  {layout?<div className="la-tree-canvas" style={{height:layout.height}}>
   <svg className="la-tree-connections" width={layout.width} height={layout.height} aria-hidden="true"><defs><linearGradient id={gradient}><stop offset="0" stopColor="#d5dfe9" stopOpacity=".65"/><stop offset="1" stopColor="#d5dfe9" stopOpacity=".18"/></linearGradient></defs>
    {branches.map((b,i)=>{const p=layout.branches[i];return <path key={b.id} className={'la-tree-trunk'+(b.id===active?' on':'')} d={treeCurve(layout.root.x+39,layout.root.y,p.x,p.y)} stroke={`url(#${gradient})`}/>;})}
    {branch&&leaves.map((p,i)=>{const from=layout.branches[branches.indexOf(branch)],to=layout.leaves[i],lit=p.id===selectedId||p.id===hover;const d=treeCurve(from.x+from.width,from.y,to.x+3,to.y);return <g key={p.id}><path className={'la-tree-line'+(lit?' on':'')} d={d} stroke={`url(#${gradient})`}/>{lit&&<path className="la-tree-flow" d={d} pathLength={100}/>}</g>;})}
   </svg>
   <div className="la-tree-core" style={{left:layout.root.x,top:layout.root.y}}>{root}</div>
   {branches.map(branchButton)}
   {leaves.map(leafButton)}
   {!leaves.length&&<div className="la-tree-empty-wide" style={{left:layout.branches[0]?.x+layout.branches[0]?.width+65,top:layout.root.y}}>{empty}</div>}
  </div>:<>
   {root}
   <div className="la-tree-mobile-branches">{branches.map((b,i)=><section key={b.id} className={'la-tree-mobile-branch'+(b.id===active?' on':'')}>{branchButton(b,i)}{b.id===active&&<div className="la-tree-mobile-leaves">{leaves.length?leaves.map(leafButton):empty}</div>}</section>)}</div>
  </>}
  <p className="la-tree-caption">{branch?`${branch.name} · ${leaves.length} ${leaves.length===1?'proyecto':'proyectos'}`:'Sin ramas'}<span>Selecciona una rama para explorar</span></p>
 </div>;
}
