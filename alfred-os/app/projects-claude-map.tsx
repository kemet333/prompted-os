'use client';
import {useLayoutEffect,useRef,useState,type CSSProperties} from 'react';
import {ChevronLeft,ChevronRight} from 'lucide-react';
import type {Project} from '../lib/alfred-data';
import {projectLabel} from '../lib/life-areas';
import {taper,spine} from '../lib/claude-project-curves';
import './projects-tree.css';
import './projects-claude-map.css';

export type MapTask={id:string;proyecto:string;columna:string;titulo:string;hora?:string;responsable?:string};
type Group={id:string;name:string;description:string;projects:Project[]};
type Props={groups:Group[];active:string;onGroup:(id:string)=>void;selected:Project|null;focusId:string;onOpen:(p:Project,target?:HTMLElement)=>void;tasks:MapTask[];taskId:string|null;onTask:(id:string|null)=>void;onClose:()=>void};
const COLORS:Record<string,string>={social:'#90c8c3',prompted:'#b4a6d7',norte:'#d5b77b',unassigned:'#a5b8d0'};
const STATES=[{id:'curso',name:'En curso'},{id:'para-ti',name:'Para ti'},{id:'cola',name:'En cola'},{id:'bloqueado',name:'Bloqueadas'},{id:'hecho',name:'Hechas'}];
export default function ClaudeProjectsMap({groups,active,onGroup,selected,focusId,onOpen,tasks,taskId,onTask,onClose}:Props){
 const scene=useRef<HTMLDivElement>(null),[size,setSize]=useState({w:0,h:0}),[state,setState]=useState(''),[pages,setPages]=useState<Record<string,number>>({}),[hover,setHover]=useState('');
 const group=groups.find(g=>g.id===active)||groups[0],color=COLORS[group?.id]||COLORS.unassigned;
 const states=STATES.map(s=>({...s,n:tasks.filter(t=>t.columna===s.id).length})).filter(s=>s.n>0);
 const activeState=states.some(s=>s.id===state)?state:states[0]?.id;
 const cards=selected?states.map(s=>({id:s.id,title:s.name,n:s.n,sub:'tareas'})):groups.map(g=>({id:g.id,title:g.name,n:g.projects.length,sub:'proyectos'}));
 const key=selected?'task:'+selected.id+':'+activeState:'group:'+active;
 const page=pages[key]||0;
 const setPage=(value:number)=>setPages(p=>({...p,[key]:value}));
 useLayoutEffect(()=>{const el=scene.current;if(!el)return;const read=()=>{const r=el.getBoundingClientRect();setSize(s=>s.w===r.width&&s.h===r.height?s:{w:r.width,h:r.height});};read();const ro=new ResizeObserver(read);ro.observe(el);return()=>ro.disconnect();},[]);
 const {w:W,h:H}=size,compact=W<900,mobile=W<620||H<Math.max(320,cards.length*74+40),cardW=compact?164:208,cardH=62,cardGap=12;
 const cx=compact?66:Math.max(100,W*.1),cy=H/2,cardX=cx+(compact?96:148);
 const labelW=compact?Math.max(150,Math.min(232,W-cardX-cardW-64)):346;
 const rootRadius=compact?38:52;
 const from=cards.find(c=>c.id===(selected?activeState:active));
 const placed=cards.map((c,i)=>({...c,x:cardX,y:cy-(cards.length*cardH+(cards.length-1)*cardGap)/2+i*(cardH+cardGap)}));
 const source=placed.find(c=>c.id===from?.id);
 const allLeaves=selected?tasks.filter(t=>t.columna===activeState).sort((a,b)=>(b.hora||'').localeCompare(a.hora||'')).map(t=>({id:t.id,label:t.titulo,meta:t.responsable||'',project:null as Project|null})): (group?.projects||[]).map(p=>({id:p.id,label:projectLabel(p),meta:p.stage,project:p as Project|null}));
 const room=Math.max(3,Math.min(13,Math.floor(Math.max(0,H-105)/56)+1)),maxPage=Math.max(0,Math.ceil(allLeaves.length/room)-1),safePage=Math.min(page,maxPage),shown=allLeaves.slice(safePage*room,(safePage+1)*room);
 const gap=shown.length>1?Math.max(46,Math.min(62,(H-90)/(shown.length-1))):0,half=gap*(shown.length-1)/2,Ry=Math.max(half*1.18,1),acx=cardX+cardW+(compact?24:44),Rx=Math.max(22,Math.min(240,W-acx-labelW-16));
 const leaves=shown.map((l,i)=>{const y=cy-half+gap*i,k=Math.min(1,Math.abs(y-cy)/Ry);return {...l,x:acx+Rx*Math.sqrt(1-k*k),y};});
 const choose=(id:string)=>{if(selected){setState(id);onTask(null);}else onGroup(id);};
 return <div className={'pt claude-map'+(compact?' pt-compact':'')+(mobile?' cm-mobile':'')} style={{'--c':color} as CSSProperties} aria-label="Árbol de proyectos de Claude">
  <header className="pt-head"><nav className="pt-crumb" aria-label="Ruta del árbol"><button onClick={()=>{onClose();}}>Tus empresas</button><ChevronRight size={13}/><span>{selected?projectLabel(selected):group?.name}</span></nav>{!selected&&<span className="cm-total">{groups.reduce((n,g)=>n+g.projects.length,0)} proyectos</span>}</header>
  <div ref={scene} className="pt-scene">
   {!mobile&&W>0&&H>0&&<><svg className="pt-svg" width={W} height={H} aria-hidden="true"><defs><linearGradient id="cm-fade"><stop offset="0" stopColor={color} stopOpacity=".85"/><stop offset="1" stopColor={color} stopOpacity=".18"/></linearGradient><linearGradient id="cm-trunk"><stop offset="0" stopColor="#dfe6ef" stopOpacity=".55"/><stop offset="1" stopColor="#dfe6ef" stopOpacity=".12"/></linearGradient></defs>
    {placed.map(c=><path key={c.id} className={'pt-trunk'+(c.id===source?.id?' on':'')} d={taper(cx+rootRadius+6,cy,c.x,c.y+cardH/2,c.id===source?.id?3.2:1.6,c.id===source?.id?1.4:.7)} fill={c.id===source?.id?'url(#cm-fade)':'url(#cm-trunk)'}/>)}
    {source&&leaves.map(l=><g key={l.id}><path className={'pt-branch'+(taskId===l.id||hover===l.id?' on':'')} d={taper(source.x+cardW,source.y+cardH/2,l.x-6,l.y,hover===l.id?2:1.3,.35)} fill="url(#cm-fade)"/>{hover===l.id&&<path className="pt-flow" d={spine(source.x+cardW,source.y+cardH/2,l.x-6,l.y)} pathLength={100}/>}</g>)}
   </svg><button className="pt-root" style={{left:cx,top:cy}} onClick={onClose} aria-label={selected?'Volver a las empresas':'Tus empresas'}><span className="pt-root-ring"><i/></span><span className="pt-root-name">{selected?projectLabel(selected):'Tus empresas'}</span><small>{selected?'Proyecto seleccionado':'Tu cartera profesional'}</small></button>
   <div className="pt-cards">{placed.map(c=><button key={c.id} className={'pt-card'+(c.id===source?.id?' on':'')} style={{left:c.x,top:c.y,width:cardW,'--c':selected?color:(COLORS[c.id]||color)} as CSSProperties} aria-pressed={c.id===source?.id} onClick={()=>choose(c.id)}><strong>{c.n}</strong><span><b>{c.title}</b><small>{c.sub}</small></span></button>)}</div>
   <div className="pt-leaves">{leaves.map(l=><button key={l.id} data-project-id={l.project?.id} className={'pt-leaf'+(l.id===taskId?' on':'')+(l.id===focusId?' focus':'')} style={{left:l.x,top:l.y,maxWidth:Math.max(120,W-l.x-12)}} title={l.label+' · '+l.meta} onPointerEnter={()=>setHover(l.id)} onPointerLeave={()=>setHover('')} onFocus={()=>setHover(l.id)} onBlur={()=>setHover('')} onClick={e=>l.project?onOpen(l.project,e.currentTarget):onTask(l.id)}><i className="pt-dot"/><span className="pt-leaf-copy"><b>{l.label}{l.id===focusId&&<em>Foco</em>}</b><small>{l.meta}</small></span></button>)}</div></>}
   {mobile&&<div className="cm-flow"><nav aria-label={selected?'Estados de actividad':'Empresas'}>{cards.map(c=><button key={c.id} style={{'--c':selected?color:(COLORS[c.id]||color)} as CSSProperties} aria-pressed={c.id===(selected?activeState:active)} onClick={()=>choose(c.id)}><span>{c.title}</span><strong>{c.n}</strong></button>)}</nav><div className="cm-flow-leaves">{allLeaves.map(l=><button key={l.id} data-project-id={l.project?.id} onClick={e=>l.project?onOpen(l.project,e.currentTarget):onTask(l.id)}><i/><span><strong>{l.label}{l.id===focusId&&<em>Foco</em>}</strong><small>{l.meta}</small></span><ChevronRight size={14}/></button>)}</div></div>}
   {selected&&!tasks.length&&<p className="cm-empty">Este proyecto aún no tiene tareas del equipo registradas.</p>}
  </div>
  {!mobile&&maxPage>0&&<div className="cm-pages"><button disabled={!safePage} onClick={()=>setPage(safePage-1)} aria-label="Proyectos anteriores"><ChevronLeft size={15}/></button><span>{safePage*room+1}–{Math.min((safePage+1)*room,allLeaves.length)} de {allLeaves.length}</span><button disabled={safePage>=maxPage} onClick={()=>setPage(safePage+1)} aria-label="Más proyectos"><ChevronRight size={15}/></button></div>}
 </div>;
}
