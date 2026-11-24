'use client';
import {useEffect,useState} from 'react';
import {RESULTADO_MAX,RESULTADO_MIN,type Decision} from '../lib/office-limits';
import {avisoColaLanzar,descartesDeLanzar,esperaDesde,etiquetaSinMedir,formularioVigente,lineaCifrasCalendario,lineaAtribucionCalendario,MOTIVOS_DESCARTE} from '../lib/experimentos-cola';
import {DecisionPicker,decisionLabel,embudoExperimentos} from './decision-picker';
import {shareEquipo,useEquipo,type Status} from './equipo-panel';
import './projects.css';

export function clip(s:string,n:number){return s.length>n?s.slice(0,n-1).trimEnd()+'…':s;}
function textoLanz(l:unknown,campo:'que'|'a_quien'|'metrica'|'umbral'):string{const v=l&&typeof l==='object'?(l as Record<string,unknown>)[campo]:undefined;return typeof v==='string'?v.trim():'';}

export function ProjectExperiments({team}:{team:string}){
 const equipo=useEquipo();
 const teamItems:any[]=team?(((equipo as any)?.tablero||[]) as any[]).filter(i=>i.proyecto===team&&i.fuente!=='hermes'):[];
 const p=team?equipo?.proyectos?.[team]:undefined,lanz=p?.lanzamientos_7d,embudo=embudoExperimentos(lanz),exps=p?.experimentos||[],lastExp=exps[exps.length-1],sinMedir=lanz?.sin_medir||[],porLanzar=lanz?.por_lanzar||[];
 const [idx,setIdx]=useState(0);
 const [idxMedir,setIdxMedir]=useState(0);
 const i=Math.min(idx,Math.max(porLanzar.length-1,0));
 const j=Math.min(idxMedir,Math.max(sinMedir.length-1,0));
 const lanzamientoPendiente=porLanzar[i],tareaPendiente=lanzamientoPendiente?teamItems.find(i=>i.id===lanzamientoPendiente.tarea&&i.lanzamiento):undefined,quePendiente=textoLanz(tareaPendiente?.lanzamiento,'que'),aQuienPendiente=textoLanz(tareaPendiente?.lanzamiento,'a_quien'),metricaPendiente=textoLanz(tareaPendiente?.lanzamiento,'metrica'),umbralPendiente=textoLanz(tareaPendiente?.lanzamiento,'umbral'),tareaSinMedir=sinMedir[j]?teamItems.find(i=>i.id===sinMedir[j].tarea&&i.lanzamiento):undefined,metricaSinMedir=textoLanz(tareaSinMedir?.lanzamiento,'metrica'),umbralSinMedir=textoLanz(tareaSinMedir?.lanzamiento,'umbral'),masPorLanzar=typeof lanz?.por_lanzar_total==='number'&&Number.isFinite(lanz.por_lanzar_total)?lanz.por_lanzar_total-1:porLanzar.length-1;
 const esperaPendiente=esperaDesde(porLanzar[i]?.avisado,new Date()),esperaSinMedir=esperaDesde(sinMedir[j]?.lanzado,new Date());
 const totalSinMedir=typeof lanz?.sin_medir_total==='number'&&Number.isFinite(lanz.sin_medir_total)&&lanz.sin_medir_total>=sinMedir.length?lanz.sin_medir_total:sinMedir.length;
 const totalPorLanzar=typeof lanz?.por_lanzar_total==='number'&&Number.isFinite(lanz.por_lanzar_total)&&lanz.por_lanzar_total>=porLanzar.length?lanz.por_lanzar_total:porLanzar.length;
 const avisoCola=avisoColaLanzar(totalPorLanzar);
 const cifras=lineaCifrasCalendario(p?.cifras_calendario);
 const atribucion=lineaAtribucionCalendario(p?.atribucion_calendario);
 // Closing the circuit from the panel: mark the selected experiment as launched, or write down its result.
 const [sending,setSending]=useState(false),[formTarea,setFormTarea]=useState<string|null>(null),[texto,setTexto]=useState(''),[decision,setDecision]=useState<Decision|null>(null),[discardTarea,setDiscardTarea]=useState<string|null>(null),[discardText,setDiscardText]=useState(''),[expMsg,setExpMsg]=useState<{text:string;error:boolean}|null>(null);
 useEffect(()=>{if(!formularioVigente(formTarea,sinMedir[j]?.tarea)){setFormTarea(null);setTexto('');setDecision(null);}},[sinMedir[j]?.tarea,formTarea]);
 useEffect(()=>{if(!formularioVigente(discardTarea,porLanzar[i]?.tarea)){setDiscardTarea(null);setDiscardText('');}},[porLanzar[i]?.tarea,discardTarea]);
 async function sendExp(payload:Record<string,unknown>,done:string,after?:()=>void):Promise<boolean>{
  setSending(true);setExpMsg(null);
  try{
   const r=await fetch('/api/equipo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const j:any=await r.json();if(!r.ok)throw new Error(j.error);
   shareEquipo(j as Status);
   after?.();setExpMsg({text:done,error:false});
   return true;
  }catch(e){setExpMsg({text:e instanceof Error&&e.message?e.message:'No se ha podido.',error:true});return false;}
  finally{setSending(false);}
 }
 async function descartarRapido(m:string){
  if(sending||!formularioVigente(discardTarea,porLanzar[i]?.tarea))return;
  const cuerpo=descartesDeLanzar([discardTarea],m)[0];
  if(!cuerpo)return;
  setDiscardText(m);
  const ok=await sendExp(cuerpo,'Descartado: el equipo no volverá a proponerlo.',()=>{setIdx(0);setDiscardTarea(null);setDiscardText('');});
  if(!ok)setDiscardText('');
 }
 if(!((lanz?.avisados||0)>0||porLanzar.length>0||sinMedir.length>0||lastExp||cifras||atribucion))return null;
 return <><label>EXPERIMENTOS (7 DÍAS)</label>
  {embudo&&<p className="pj-team">{embudo}</p>}
  {cifras&&<p className="pj-team">{cifras}</p>}
  {atribucion&&<p className="pj-team">{atribucion}</p>}
  {avisoCola&&<p className="pj-exp-msg">{avisoCola}</p>}
  {porLanzar[i]&&<div className="pj-exp-row"><p className="pj-team-last">{porLanzar.length>1?`Listo para lanzar (${i+1} de ${totalPorLanzar}): «${porLanzar[i].titulo}»`:`Listo para lanzar: «${porLanzar[i].titulo}»${masPorLanzar>0?` y ${masPorLanzar} más`:''}`}{esperaPendiente&&<><br/>Esperando {esperaPendiente}</>}{quePendiente&&<><br/>Qué: {clip(quePendiente,160)}</>}{aQuienPendiente&&<><br/>A quién: {clip(aQuienPendiente,160)}</>}{metricaPendiente&&<><br/>Cómo se mide: {clip(metricaPendiente,160)}</>}{umbralPendiente&&<><br/>Umbral: {clip(umbralPendiente,160)}</>}</p>
   <button className="pj-exp-btn" disabled={sending} onClick={()=>sendExp({action:'lanzado',tarea:porLanzar[i].tarea},'Marcado como lanzado.',()=>{setIdx(0);setDiscardTarea(null);setDiscardText('');})}>Ya lo he lanzado</button>
   <button className="pj-exp-btn" disabled={sending} aria-expanded={formularioVigente(discardTarea,porLanzar[i]?.tarea)} onClick={()=>{setDiscardTarea(o=>formularioVigente(o,porLanzar[i]?.tarea)?null:porLanzar[i].tarea);setFormTarea(null);}}>No lo lanzo</button>
   {porLanzar.length>1&&<button className="pj-exp-btn" disabled={sending} onClick={()=>{setIdx((i+1)%porLanzar.length);setDiscardTarea(null);setDiscardText('');}}>Ver el siguiente</button>}</div>}
  {formularioVigente(discardTarea,porLanzar[i]?.tarea)&&<form className="pj-exp-form" onSubmit={e=>{e.preventDefault();if(!formularioVigente(discardTarea,porLanzar[i]?.tarea)||discardText.trim().length<RESULTADO_MIN||sending)return;sendExp({action:'descartar',tarea:discardTarea,texto:discardText.trim()},'Descartado: el equipo no volverá a proponerlo.',()=>{setIdx(0);setDiscardTarea(null);setDiscardText('');});}}>
   <p className="pj-team-last">¿Qué te frena?</p>
   {MOTIVOS_DESCARTE.map(m=><button key={m} className="pj-exp-btn" type="button" disabled={sending} aria-pressed={discardText===m} onClick={()=>{void descartarRapido(m);}}>{m}</button>)}
   <textarea value={discardText} onChange={e=>setDiscardText(e.target.value)} maxLength={RESULTADO_MAX} rows={3} placeholder="Por qué no lo lanzas" aria-label="Motivo para no lanzarlo"/>
   <button className="pj-exp-btn" disabled={sending||discardText.trim().length<RESULTADO_MIN}>Descartar</button></form>}
  {sinMedir[j]&&<div className="pj-exp-row"><p className="pj-team-last">{etiquetaSinMedir(sinMedir[j].titulo,j,sinMedir.length,totalSinMedir)}{esperaSinMedir&&<><br/>Esperando resultado {esperaSinMedir}</>}{metricaSinMedir&&<><br/>Cómo se mide: {clip(metricaSinMedir,160)}</>}{umbralSinMedir&&<><br/>Umbral: {clip(umbralSinMedir,160)}</>}</p>
   <button className="pj-exp-btn" disabled={sending} aria-expanded={formularioVigente(formTarea,sinMedir[j]?.tarea)} onClick={()=>{setFormTarea(o=>formularioVigente(o,sinMedir[j]?.tarea)?null:sinMedir[j].tarea);setDiscardTarea(null);}}>Apuntar resultado</button>
   {sinMedir.length>1&&<button className="pj-exp-btn" disabled={sending} onClick={()=>{setIdxMedir((j+1)%sinMedir.length);setFormTarea(null);setTexto('');setDecision(null);}}>Ver el siguiente</button>}</div>}
  {formularioVigente(formTarea,sinMedir[j]?.tarea)&&<form className="pj-exp-form" onSubmit={e=>{e.preventDefault();if(!formularioVigente(formTarea,sinMedir[j]?.tarea)||texto.trim().length<RESULTADO_MIN||sending)return;sendExp({action:'resultado',project:team,tarea:formTarea,texto,...(decision?{decision}:{})},'Apuntado.',()=>{setIdxMedir(0);setFormTarea(null);setTexto('');setDecision(null);});}}>
   <textarea value={texto} onChange={e=>setTexto(e.target.value)} maxLength={RESULTADO_MAX} rows={3} placeholder="Qué pasó y con qué cifras" aria-label="Resultado del experimento"/>
   <DecisionPicker value={decision} onChange={setDecision} disabled={sending} className="pj-exp-btn"/>
   <button className="pj-exp-btn" disabled={sending||texto.trim().length<RESULTADO_MIN}>Guardar</button></form>}
  {expMsg&&<p className={'pj-exp-msg'+(expMsg.error?' error':'')} role="status">{expMsg.text}</p>}
  {lastExp&&(()=>{const decision=decisionLabel((lastExp as {decision?:unknown}).decision);return <p className="pj-team-last">Último resultado: {decision?decision+' · ':''}{clip(lastExp.texto,140)}</p>;})()}</>;
}
