'use client';
import {useEffect,useRef,useState} from 'react';
import {ArrowLeft,ArrowUp,ArrowUpRight,BriefcaseBusiness,ChevronLeft,ChevronRight,Compass,Heart,Search,X} from 'lucide-react';
import {projects,type Project} from '../lib/alfred-data';
import {areaProjects,businessUnits,lifeAreas,lifeCategories,matchesProject,placement,projectLabel,type LifeAreaId} from '../lib/life-areas';
import {useEquipo} from './equipo-panel';
import {ProjectExperiments,clip} from './project-experiments';
import './projects-life-areas.css';
import type {ProjectBranch} from './projects-branch-map';
import ClaudeProjectsMap from './projects-claude-map';
import './life-shell.css';
import LifeDashboard from './life-dashboard';

type Props={filter:string;setFilter:(v:string)=>void;search:string;setSearch:(v:string)=>void;focusId:string;selected:Project|null;onSelect:(p:Project|null)=>void;onChooseFocus:(p:Project)=>void;onRequest:(p:Project)=>void;saving:boolean;onAsk?:(p:Project,text:string)=>void};
type Task={id:string;proyecto:string;columna:string;titulo:string;detalle?:string;hora?:string;responsable?:string;fuente?:string;revision?:{decision?:string;resumen?:string}};
const TASK_STATES=[{id:'curso',name:'En curso'},{id:'para-ti',name:'Para ti'},{id:'cola',name:'En cola'},{id:'bloqueado',name:'Bloqueadas'},{id:'hecho',name:'Hechas'}];
const ICONS={business:BriefcaseBusiness,personal:Heart,growth:Compass};

export default function ProjectsTree({filter,setFilter,search,setSearch,focusId,selected,onSelect,onChooseFocus,onRequest,saving,onAsk}:Props){
 const [area,setArea]=useState<LifeAreaId>('business'),[category,setCategory]=useState('all'),[unit,setUnit]=useState(()=>{const p=projects.find(p=>p.id===focusId);return p?placement(p).unit:'trabajo';}),[tab,setTab]=useState('overview'),[taskId,setTaskId]=useState<string|null>(null),[ask,setAsk]=useState('');
 const heading=useRef<HTMLHeadingElement>(null),detailHeading=useRef<HTMLHeadingElement>(null),opener=useRef<HTMLElement|null>(null),exploring=useRef(filter!=='all');
 const current=selected?(projects.find(p=>p.id===selected.id)||selected):null;
 const equipo=useEquipo();
 const tasks:Task[]=current?.team?((equipo as any)?.tablero||[]).filter((t:Task)=>t.proyecto===current.team&&t.fuente!=='hermes'):[];
 const task=tasks.find(t=>t.id===taskId);
 const scoped=areaProjects(projects,area);
 const visible=scoped.filter(p=>matchesProject(p,search));
 const active=lifeAreas.find(a=>a.id===area)!;
 const query=search.trim();
 function changeArea(id:LifeAreaId){exploring.current=true;setArea(id);setCategory('all');setFilter('all');setSearch('');onSelect(null);}
 function open(p:Project,target?:HTMLElement){exploring.current=true;opener.current=target||null;onSelect(p);}
 function close(){const id=current?.id;onSelect(null);requestAnimationFrame(()=>requestAnimationFrame(()=>{const returned=Array.from(document.querySelectorAll<HTMLElement>('[data-project-id]')).find(el=>el.dataset.projectId===id);if(returned)returned.focus();else if(opener.current?.isConnected)opener.current.focus();else document.querySelector<HTMLElement>('.la-areas>button[aria-pressed=true]')?.focus();}));}
 function step(delta:number){if(!current)return;const list=areaProjects(projects,placement(current).area);const i=list.findIndex(p=>p.id===current.id);if(list.length)onSelect(list[(Math.max(i,0)+delta+list.length)%list.length]);}
 useEffect(()=>{if(filter==='lab')setArea('personal');else if(businessUnits.some(u=>u.id===filter))setArea('business');},[filter]);
 const selectedArea=current?placement(current).area:null;
 const selectedBranch=current?(selectedArea==='business'?placement(current).unit:placement(current).category):null;
 const focus=projects.find(p=>p.id===focusId),focusArea=focus?placement(focus).area:null,focusBranch=focus?(focusArea==='business'?placement(focus).unit:placement(focus).category):null;
 // Follow the asynchronously loaded office focus until the user starts exploring.
 useEffect(()=>{if(!exploring.current&&!current&&!query&&filter==='all'&&focusArea&&focusBranch){setArea(focusArea);if(focusArea==='business')setUnit(focusBranch);else setCategory(focusBranch);}},[focusId,focusArea,focusBranch]);
 useEffect(()=>{if(selectedArea&&selectedBranch){setArea(selectedArea);if(selectedArea==='business')setUnit(selectedBranch);else setCategory(selectedBranch);}},[current?.id,selectedArea,selectedBranch]);
 useEffect(()=>{if(current){setTab('overview');setTaskId(null);setAsk('');if(search&&!matchesProject(current,search))setSearch('');detailHeading.current?.focus();}},[current?.id]);
 useEffect(()=>{
  if(!current)return;
  const key=(e:KeyboardEvent)=>{if(e.key==='Escape'){e.preventDefault();close();return;}if(e.target instanceof Element&&e.target.closest('input,textarea,select,button,a'))return;if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();step(e.key==='ArrowRight'?1:-1);}};
  window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key);
 });
 const branches:ProjectBranch[]=area==='business'?businessUnits.map(u=>({id:u.id,name:u.name,description:u.description,projects:visible.filter(p=>placement(p).unit===u.id).sort((a,b)=>{const ids=u.featured as readonly string[];const rank=(p:Project)=>ids.includes(p.id)?ids.indexOf(p.id):-1;return (rank(a)<0?999:rank(a))-(rank(b)<0?999:rank(b));})})):lifeCategories[area].map(c=>({...c,projects:visible.filter(p=>placement(p).category===c.id)}));
 if(area==='business'&&visible.some(p=>placement(p).unit==='unassigned'))branches.push({id:'unassigned',name:'Por clasificar',description:'Negocio sin empresa asociada.',projects:visible.filter(p=>placement(p).unit==='unassigned')});
 const desired=current?selectedBranch:area==='business'?(filter!=='all'&&filter!=='lab'?filter:unit):category;
 const candidate=branches.find(b=>b.id===desired);
 const activeBranch=(candidate&&(!query||candidate.projects.length)?candidate:branches.find(b=>b.projects.length)||branches[0])?.id||'';
 function changeBranch(id:string){exploring.current=true;setFilter('all');if(area==='business')setUnit(id);else setCategory(id);onSelect(null);}
 return <section className={'la la-tree-view la-'+area+(current?' la-inspecting':'')} aria-label="Áreas de vida y proyectos" onKeyDownCapture={e=>{if(current&&e.key==='Escape'){e.preventDefault();e.stopPropagation();close();}}}>
  <div className="la-main">
   <header className="la-toolbar">
    <div className="la-toolbar-title"><h1 ref={heading} tabIndex={-1}>Tu espacio</h1></div>
    <nav className="la-areas" aria-label="Áreas de vida">{lifeAreas.map(a=>{const Icon=ICONS[a.id];const n=areaProjects(projects,a.id).length;return <button key={a.id} className={area===a.id?'on':''} onClick={()=>changeArea(a.id)} aria-pressed={area===a.id}><Icon size={16}/><strong>{a.name}</strong><span className="la-count">{n}<span className="la-sr"> proyectos</span></span></button>;})}</nav>
    <label className="la-search"><Search size={15}/><input aria-label={'Buscar en '+active.name} placeholder={'Buscar en '+active.name} value={search} onChange={e=>{exploring.current=true;setSearch(e.target.value);}}/>{query&&<button onClick={()=>setSearch('')} aria-label="Limpiar búsqueda"><X size={14}/></button>}</label>
   </header>
   {area==='business'?<div className="la-business-canvas"><ClaudeProjectsMap groups={branches} active={activeBranch} onGroup={changeBranch} selected={current} focusId={focusId} tasks={tasks} taskId={taskId} onTask={id=>{setTab('activity');setTaskId(id);}} onOpen={open} onClose={()=>{if(current)close();else setFilter('all');}}/>{query&&<p className="la-search-result" role="status">{visible.length} proyectos encontrados</p>}</div>:<div className="la-dashboard-scroll"><LifeDashboard area={area} projects={scoped} query={query} focusId={focusId} onOpen={open}/></div>}
  </div>
  {current&&<aside className="la-inspector" aria-label={'Proyecto '+projectLabel(current)}>
   <div className="la-inspector-top"><button onClick={close} className="la-back"><ArrowLeft size={15}/>Volver</button><div><button onClick={()=>step(-1)} aria-label="Proyecto anterior"><ChevronLeft size={17}/></button><button onClick={()=>step(1)} aria-label="Proyecto siguiente"><ChevronRight size={17}/></button><button onClick={close} aria-label="Cerrar proyecto"><X size={17}/></button></div></div>
   <div className="la-inspector-scroll"><span className="la-eyebrow">{lifeAreas.find(a=>a.id===placement(current).area)?.name} / {businessUnits.find(u=>u.id===placement(current).unit)?.name||'Proyecto'}</span><h2 ref={detailHeading} tabIndex={-1}>{projectLabel(current)}</h2>{projectLabel(current)!==current.name&&<p className="la-original-name">{current.name}</p>}<div className="la-project-meta"><span>{current.stage}</span>{current.updated&&<time>{current.updated}</time>}</div>
    <div className="la-detail-tabs" role="group" aria-label="Información del proyecto"><button aria-pressed={tab==='overview'} onClick={()=>setTab('overview')}>Resumen</button><button aria-pressed={tab==='activity'} onClick={()=>setTab('activity')}>Actividad <span>{tasks.length}</span></button></div>
    {tab==='overview'?<><label>El siguiente paso</label><p className="la-next">{current.next||'Este proyecto todavía no tiene un siguiente paso documentado.'}</p><label>Lo que sabemos</label><p className="la-evidence">{current.evidence||'No hay evidencia documentada en la ficha. Consulta la nota para ampliar el contexto.'}</p>{current.team&&<ProjectExperiments key={current.id} team={current.team}/>}
     <div className="la-links">{current.note&&<a href={'obsidian://open?path='+encodeURIComponent(current.note)}>Abrir nota en Obsidian <ArrowUpRight size={13}/></a>}{current.repo&&<a href={current.repo} target="_blank" rel="noreferrer">Ver repositorio <ArrowUpRight size={13}/></a>}</div>
     <div className="la-actions">{current.id===focusId?<p className="la-is-focus">Es tu foco actual</p>:<button className="la-primary" disabled={saving} onClick={()=>onChooseFocus(current)}>Elegir como foco</button>}<button className="la-secondary" onClick={()=>onRequest(current)}>Preparar una entrega</button></div>
    </>:<div className="la-activity">{task?<><button className="la-back" onClick={()=>setTaskId(null)}><ChevronLeft size={14}/>Toda la actividad</button><h3>{task.titulo}</h3><p>{task.detalle||'Sin detalle adicional.'}</p>{task.revision?.decision&&<p>Revisión: {task.revision.decision}{task.revision.resumen?` · ${task.revision.resumen}`:''}</p>}</>:<>{TASK_STATES.filter(s=>tasks.some(t=>t.columna===s.id)).map(s=><section key={s.id}><h3>{s.name}<span>{tasks.filter(t=>t.columna===s.id).length}</span></h3>{tasks.filter(t=>t.columna===s.id).map(t=><button key={t.id} onClick={()=>setTaskId(t.id)}><strong>{t.titulo}</strong><small>{[t.responsable,t.hora?new Date(t.hora).toLocaleDateString('es-ES'):null].filter(Boolean).join(' · ')}</small></button>)}</section>)}{!tasks.length&&<p className="la-empty-small">No hay actividad del equipo registrada para este proyecto.</p>}</>}</div>}
   </div>
   {onAsk&&<form className="la-ask" onSubmit={e=>{e.preventDefault();const value=ask.trim();if(value){onAsk(current,value);setAsk('');}}}><input aria-label={'Preguntar sobre '+current.name} placeholder={`Pregunta sobre ${clip(projectLabel(current),23)}…`} value={ask} onChange={e=>setAsk(e.target.value)}/><button aria-label="Enviar pregunta" disabled={!ask.trim()}><ArrowUp size={16}/></button></form>}
  </aside>}
 </section>;
}
