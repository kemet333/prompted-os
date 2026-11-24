'use client';
// Agent panel inside Alfred's chat (21/11/2026, «Alfred, una sola puerta»): what the team and Hermes are doing right now and
// what waits for Owner, without opening Entregas, Hermes' own chat, Claude or Codex. Only real data from the team board
// (/api/equipo) and Hermes (/api/hermes); nothing running, nothing invented. Owner asks for new work by talking to Alfred.
import {useEffect,useState} from 'react';
import {ChevronDown,GitMerge,Check,Square} from 'lucide-react';
import {useEquipo} from './equipo-panel';

type Objetivo={id:string;objetivo:string;proyecto:string|null;estado:string;rondas:number;pregunta:string|null};
const PHASE:Record<string,string>={construyendo:'Construyendo',probando:'Probando',revisando:'Revisando'};
const ESTADO:Record<string,string>={contrato:'Preparando cómo medirlo',esperando_contrato:'Espera que apruebes cómo medirlo',planificando:'Planificando la ronda',
 esperando_aprobacion:'Ronda lista: espera tu visto bueno',en_marcha:'El equipo trabaja en la ronda',necesita_owner:'Te necesita'};
const ESPERA=new Set(['esperando_contrato','esperando_aprobacion','necesita_owner']);

function useHermes(){
 const [h,setH]=useState<{objetivos:Objetivo[]}|null>(null);
 useEffect(()=>{let gone=false;
  const pull=async()=>{try{const r=await fetch('/api/hermes',{cache:'no-store'});if(r.ok&&!gone)setH(await r.json());}catch{}};
  pull();const t=setInterval(pull,20000);return()=>{gone=true;clearInterval(t);};},[]);
 return [h,setH] as const;
}
const ago=(iso?:string)=>{const t=Date.parse(iso||'');if(!Number.isFinite(t))return '';const m=Math.max(0,Math.round((Date.now()-t)/60000));return m<60?`${m} min`:`${Math.floor(m/60)} h ${m%60} min`;};

export function AgentPanel(){
 const s=useEquipo(),[hermes,setHermes]=useHermes(),[open,setOpen]=useState(false),[busy,setBusy]=useState(''),[note,setNote]=useState('');
 const name=(pid?:string|null)=>pid?(s?.proyectos?.[pid]?.nombre||pid):'';
 const now:any[]=((s as any)?.tablero||[]).filter((i:any)=>i.columna==='curso'&&(i.fuente==='hermes'||PHASE[i.fase]));
 const merges=Object.entries(s?.proyectos||{}).filter(([,p])=>(p.por_fusionar||0)>0);
 const objetivos=(hermes?.objetivos||[]).filter(o=>o.estado!=='hecho'&&o.estado!=='parado');
 const waiting=merges.length+objetivos.filter(o=>ESPERA.has(o.estado)).length;
 if(!s&&!hermes)return null;
 async function post(url:string,payload:object,key:string,ok:string){
  setBusy(key);setNote('');
  try{const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const j:any=await r.json().catch(()=>({}));
   if(!r.ok)throw new Error(j.error||'No se ha podido.');setNote(ok);
   if(url==='/api/hermes'){const h=await fetch('/api/hermes',{cache:'no-store'});if(h.ok)setHermes(await h.json());}}
  catch(e){setNote(e instanceof Error?e.message:'No se ha podido.');}finally{setBusy('');}
 }
 const summary=[now.length?`${now.length} trabajando`:'Nadie trabajando ahora',waiting?`${waiting} ${waiting===1?'te espera':'te esperan'}`:''].filter(Boolean).join(' · ');
 return <section className={'j-agents'+(open?' open':'')} aria-label="Agentes">
  <button type="button" className="j-agents-head" onClick={()=>setOpen(!open)} aria-expanded={open}>
   <i className={now.length?'on':''} aria-hidden="true"/><b>Agentes</b><span>{summary}</span><ChevronDown size={14} aria-hidden="true"/>
  </button>
  {open&&<div className="j-agents-body">
   {now.length>0&&<ul>{now.map(i=><li key={i.id}><small>{i.fuente==='hermes'?'Hermes':PHASE[i.fase]} · {name(i.proyecto)}{ago(i.hora)&&<> · {ago(i.hora)}</>}</small><p>{i.titulo}</p></li>)}</ul>}
   {(merges.length>0||objetivos.length>0)&&<ul>
    {merges.map(([pid,p])=><li key={'m'+pid} className="wait"><small>Por juntar · {p.nombre}</small><p>{p.por_fusionar} {p.por_fusionar===1?'mejora probada y revisada':'mejoras probadas y revisadas'}</p>
     <div className="j-agents-acts"><button type="button" disabled={!!busy} onClick={()=>post('/api/equipo',{action:'fusionar',project:pid},'m'+pid,'Juntando: te aviso cuando acabe.')}><GitMerge size={13}/>Júntalas</button></div></li>)}
    {objetivos.map(o=><li key={o.id} className={ESPERA.has(o.estado)?'wait':''}><small>Hermes{o.proyecto?' · '+name(o.proyecto):''}{o.rondas?` · ronda ${o.rondas}`:''}</small><p>{o.objetivo}</p><em>{o.estado==='necesita_owner'&&o.pregunta?o.pregunta:ESTADO[o.estado]||o.estado}</em>
     <div className="j-agents-acts">{o.estado==='esperando_aprobacion'&&<button type="button" disabled={!!busy} onClick={()=>post('/api/hermes',{action:'aprobar',id:o.id},o.id,'Ronda aprobada: entra al equipo.')}><Check size={13}/>Aprobar ronda</button>}
      <button type="button" className="quiet" disabled={!!busy} onClick={()=>post('/api/hermes',{action:'parar',id:o.id},o.id+'p','Objetivo parado.')}><Square size={11}/>Parar</button></div></li>)}
   </ul>}
   {note&&<p className="j-agents-note" role="status">{note}</p>}
   <p className="j-agents-tip">Para encargar algo, díselo a Alfred: «encárgale al equipo…» o «objetivo: …».</p>
  </div>}
 </section>;
}
