'use client';

import {Activity, ArrowUpRight, CalendarDays, ChevronDown, Dumbbell, Footprints, Heart, HeartPulse, Scale, Wind, X, type LucideIcon} from 'lucide-react';
import type {CSSProperties} from 'react';
import type {LifeData, HealthDay} from '../lib/life-data';
import {activityRecords, activityTimeline, type ActivityKind, type ActivityRecord} from '../lib/life-activity';
import {daysSinceGroups, exerciseProgress, type ExerciseProgress} from '../lib/workout-progress';
import './training-dashboard.css';

export type TrainingView = 'all' | 'training' | 'habits' | 'health';
type Props = {
  data: LifeData | null;
  view: TrainingView;
  source: 'diary' | 'health';
  weeks: number;
  kind: ActivityKind | null;
  limit: number;
  onSource: (source: 'diary' | 'health') => void;
  onWeeks: (weeks: number) => void;
  onKind: (kind: ActivityKind | null) => void;
  onLimit: (limit: number) => void;
  onView: (view: TrainingView) => void;
  onSources: () => void;
};
const kinds: {id: ActivityKind; title: string; color: string; Icon: LucideIcon}[] = [
  {id:'strength', title:'Fuerza', color:'#b6a1e8', Icon:Dumbbell},
  {id:'running', title:'Carrera', color:'#dfb777', Icon:Footprints},
  {id:'other', title:'Otras actividades', color:'#8bc9c1', Icon:Activity},
];
const metrics = [
  {id:'restingHeartRate', title:'Pulso en reposo', Icon:HeartPulse},
  {id:'hrv', title:'Variabilidad cardíaca', Icon:Wind},
  {id:'weight', title:'Peso corporal', Icon:Scale},
];
const format = (n:number, places=0) => n.toLocaleString('es-ES',{maximumFractionDigits:places});
const dayDate = (s:string) => new Date(s.length===10?s+'T12:00:00':s);
const shortDate = (s:string) => dayDate(s).toLocaleDateString('es-ES',{day:'numeric',month:'short'});
const fullDate = (s:string) => dayDate(s).toLocaleDateString('es-ES',{day:'numeric',month:'short',year:'numeric'});
const sourceName = (source:'diary'|'health') => source==='diary'?'Diario de Alfred':'Apple Health';

function TrainingEntry({entry,note}:{entry:ActivityRecord;note:string|null}) {
  const {Icon} = kinds.find(k=>k.id===entry.kind)!;
  return <details className="pd-workout">
    <summary>
      <span className={'pd-workout-icon '+entry.kind}><Icon size={19}/></span>
      <span className="pd-workout-copy"><strong>{entry.title}</strong><small>{fullDate(entry.date)}{!entry.certain?' · fecha por confirmar':''} · {entry.source}</small></span>
      <span className="pd-workout-measures">{entry.measures.slice(0,2).join(' · ')}</span>
      <span className={'pd-status '+(entry.performed?'done':'')}>{entry.status==='REALIZADO'?'Realizado':entry.status==='PARCIAL'?'Parcial':entry.status==='PLANIFICADO'?'Planificado':'Por confirmar'}</span>
      <ChevronDown size={15}/>
    </summary>
    <div className="pd-workout-detail">
      {!!entry.measures.length&&<p>{entry.measures.join(' · ')}</p>}
      {!!entry.details.length&&<ul>{entry.details.map((line,i)=><li key={i}>{line}</li>)}</ul>}
      {entry.truncated&&<p>Consulta la nota para ver el registro completo.</p>}
      {note&&<a href={'obsidian://open?path='+encodeURIComponent(note)}>Abrir registro original <ArrowUpRight size={13}/></a>}
    </div>
  </details>;
}

const kg = (n:number) => format(n,1)+' kg';
const ago = (days:number) => days===0?'hoy':days===1?'ayer':'hace '+days+' días';
function Spark({entries}:{entries:ExerciseProgress['entries']}) {
  if(entries.length<2) return <span className="pd-spark-empty">Primera vez</span>;
  const pts=entries.slice(-8),lo=Math.min(...pts.map(e=>e.kg)),hi=Math.max(...pts.map(e=>e.kg)),span=hi-lo||1;
  const xy=pts.map((e,i)=>[4+i*(92/(pts.length-1)),26-((e.kg-lo)/span)*22]);
  return <svg className="pd-spark" viewBox="0 0 100 30" aria-hidden="true"><polyline points={xy.map(p=>p.join(',')).join(' ')}/>{xy.map(([x,y],i)=><circle key={i} cx={x} cy={y} r={i===xy.length-1?2.6:1.6}/>)}</svg>;
}
function ExerciseRow({p}:{p:ExerciseProgress}) {
  const delta=p.previous?p.last.kg-p.previous.kg:null;
  return <li className="pd-exercise" title={p.entries.map(e=>fullDate(e.date)+': '+e.line).join('\n')}>
    <span className="pd-exercise-name"><strong>{p.name}</strong><small>{shortDate(p.last.date)}{p.last.scheme?' · '+p.last.scheme:''}</small></span>
    <Spark entries={p.entries}/>
    <span className="pd-exercise-load"><strong>{p.last.approx?'≈ ':''}{kg(p.last.kg)}</strong>
      {p.record?<small className="up">Récord</small>:delta===null?<small>—</small>:<small className={delta>0?'up':delta<0?'down':''}>{delta>0?'+'+kg(delta):delta<0?'−'+kg(-delta):'Igual'}</small>}</span>
    <span className="pd-exercise-best"><small>Marca</small>{kg(p.best.kg)}<small>{shortDate(p.best.date)}</small></span>
  </li>;
}

function ActivityRings({day,onSources}:{day:HealthDay|undefined;onSources:()=>void}) {
  const values = [
    {label:'Movimiento',value:day?.energy,goal:day?.energyGoal,unit:'kcal',color:'#e9a4ba'},
    {label:'Ejercicio',value:day?.exercise,goal:day?.exerciseGoal,unit:'min',color:'#bcd99a'},
    {label:'De pie',value:day?.stand,goal:day?.standGoal,unit:'h',color:'#8cced7'},
  ];
  return <article className="pd-card pd-activity">
    <header className="pd-card-heading"><h3>Actividad diaria</h3><span>{day?shortDate(day.date):'Apple Health'}</span></header>
    <div className="pd-activity-body">
      <div className="pd-rings" aria-hidden="true">
        <svg viewBox="0 0 112 112">{values.map((v,i)=>{
          const radius=47-i*12,length=2*Math.PI*radius;
          const progress=v.value!=null&&v.goal!=null&&v.goal>0?Math.min(1,v.value/v.goal):0;
          return <g key={v.label} fill="none" strokeWidth="8"><circle cx="56" cy="56" r={radius} stroke={v.color} opacity=".12"/>{progress>0&&<circle className="pd-ring-value" cx="56" cy="56" r={radius} stroke={v.color} strokeLinecap="round" strokeDasharray={`${length*progress} ${length}`} transform="rotate(-90 56 56)"/>}</g>;
        })}</svg>
        <Activity size={18}/>
      </div>
      <dl className="pd-activity-values">{values.map(v=><div key={v.label} style={{'--activity':v.color} as CSSProperties}><dt><i/>{v.label}</dt><dd>{v.value!=null?format(v.value):'—'}<small>{v.unit}</small><span>{v.goal!=null?' / '+format(v.goal):' · sin objetivo'}</span></dd></div>)}</dl>
    </div>
    {!day&&<button className="pd-inline-action" onClick={onSources}>Traer la actividad de tu Watch <ArrowUpRight size={13}/></button>}
  </article>;
}

export default function TrainingDashboard(props:Props) {
  const {data,view,source,weeks,kind,limit,onSource,onWeeks,onKind,onLimit,onView,onSources}=props;
  const records=activityRecords(data,source),timeline=activityTimeline(records,weeks),week=activityTimeline(records,1);
  const completed=records.filter(r=>r.certain&&r.performed&&r.day<=timeline.today);
  const health=data?.health.data;
  const sourceReady=source==='diary'?data?.training.status==='ready':data?.health.status==='ready'&&!!health;
  const sourceError=source==='diary'?data?.training.status==='error':data?.health.status==='error';
  const latest=sourceReady?completed[0]:undefined;
  const lastDay=health?.days.filter(d=>d.date<=timeline.today).sort((a,b)=>b.date.localeCompare(a.date))[0];
  const history=kind?records.filter(r=>r.kind===kind):records;
  const countDays=week.days.filter(d=>d.strength+d.running+d.other>0).length;
  const monthLabels=timeline.days.filter((_,i)=>i%7===0).flatMap((d,i,days)=>i===0||d.day.slice(0,7)!==days[i-1].day.slice(0,7)?[{index:i,label:dayDate(d.day).toLocaleDateString('es-ES',{month:'short'})}]:[]);
  const openHistory=(value:ActivityKind|null)=>{
    onKind(value);onLimit(8);onView('training');
    requestAnimationFrame(()=>{const heading=document.getElementById('pd-history-title');heading?.focus({preventScroll:true});heading?.scrollIntoView({block:'start',behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});});
  };
  const historyLimit=view==='all'?3:limit;
  const energyDays=health?.days.filter(d=>d.date<=timeline.today&&d.energy!=null).sort((a,b)=>a.date.localeCompare(b.date)).slice(-14)||[];
  const maxEnergy=Math.max(1,...energyDays.map(d=>d.energy!));
  const diary=source==='diary'&&sourceReady;
  const progress=diary?exerciseProgress(records):[],groups=diary?daysSinceGroups(records,timeline.today):[];
  const due=groups.filter(g=>g.days!==null).sort((a,b)=>b.days!-a.days!)[0];

  const session = <section className="pd-card pd-session" aria-label="Resumen de entrenamientos">
    <header className="pd-card-heading"><span><Dumbbell size={16}/>Entrenamiento</span><span>{sourceName(source)}</span></header>
    <div className="pd-session-main"><span className="pd-eyebrow">ÚLTIMA SESIÓN REALIZADA</span><h3>{latest?.title||(sourceReady?'Tu próximo registro empieza aquí.':sourceError?'No se puede leer esta fuente.':'Tus registros, aquí.')}</h3>
      <p>{latest?fullDate(latest.date):sourceReady?'Las sesiones confirmadas de tu diario o de Apple Health aparecerán en este espacio.':sourceError?'Revisa la fuente o vuelve a intentar la lectura. Los contadores quedan sin dato.':'Conecta tu diario o importa Apple Health para ver tus entrenamientos.'}</p>
      {!!groups.length&&<div className="pd-groups" aria-label="Último entreno de cada grupo">{groups.map(g=><span key={g.id} className={g===due&&g.days!>=3?'due':''}><b>{g.name}</b>{g.days===null?'sin registro':ago(g.days)}</span>)}</div>}
      {latest&&<div className="pd-session-notes">{latest.measures.length?latest.measures.map(m=><span key={m}>{m}</span>):<p>{latest.details[0]||('Sesión confirmada · '+latest.source+'.')}</p>}</div>}
    </div>
    <div className="pd-week-summary"><div><strong>{sourceReady?String(week.total).padStart(2,'0'):'—'}</strong><span>sesiones esta semana<small>{shortDate(week.start)} — {shortDate(week.end)}</small></span></div><span className="pd-active-days">{sourceReady?countDays:'—'}<small>{countDays===1?'día con registro':'días con registro'}</small></span></div>
    <div className="pd-week" aria-label={sourceReady?'Sesiones documentadas esta semana':'Semana · fuente sin leer'}>{week.days.map((d,i)=>{const count=d.strength+d.running+d.other;return <div className={(d.day===timeline.today?'today ':'')+(d.future?'future':'')} key={d.day} title={fullDate(d.day)+(d.future?' · fecha futura':!sourceReady?' · fuente sin leer':`: ${count} ${count===1?'sesión realizada':'sesiones realizadas'}`)}><span>{['L','M','X','J','V','S','D'][i]}</span><b>{Number(d.day.slice(8))}</b><div>{kinds.map(k=>d[k.id]>0?<i key={k.id} style={{background:k.color}}/>:null)}</div></div>;})}</div>
    <button className="pd-inline-action" onClick={()=>sourceReady?openHistory(null):onSources()}>{sourceReady?'Explorar entrenamientos':'Revisar fuente'} <ArrowUpRight size={14}/></button>
  </section>;

  const progressPanel = progress.length>0&&<section className="pd-progress" aria-label="Progreso por ejercicio">
    <header className="pd-section-heading"><div><h3>Progreso por ejercicio</h3><p>La carga más alta de cada sesión, tal como está en tu diario. Con ≈, cifra aproximada.</p></div><span>{progress.length} ejercicios</span></header>
    <ul className="pd-card pd-exercises">{progress.slice(0,view==='all'?6:undefined).map(p=><ExerciseRow key={p.id} p={p}/>)}</ul>
    {view==='all'&&progress.length>6&&<button className="pd-more" onClick={()=>onView('training')}>Ver los {progress.length} ejercicios <ArrowUpRight size={13}/></button>}
  </section>;

  const healthPanel = <section className="pd-health" aria-label="Mediciones y actividad de Salud">
    <header className="pd-section-heading"><h3><Heart size={16}/>Salud</h3><button onClick={onSources}>{health?'Ver importación':'Conectar mis datos'}<ArrowUpRight size={13}/></button></header>
    <div className="pd-metrics">{metrics.map(({id,title,Icon})=>{const metric=health?.metrics.find(m=>m.id===id);return <article className={'pd-card pd-metric pd-metric-'+id} key={id}><header><Icon size={16}/><span>{title}</span></header><strong title={metric?format(metric.value,id==='weight'?1:0)+' '+metric.unit:undefined}>{metric?format(metric.value,id==='weight'?1:0):'—'}<small>{metric?.unit||''}</small></strong><p title={metric?.source}>{metric?shortDate(metric.date):'Sin importar'}</p></article>;})}</div>
    <ActivityRings day={lastDay} onSources={onSources}/>
  </section>;

  const habits = <section className="pd-habits" aria-label="Hábitos de entrenamiento">
    <header className="pd-section-heading"><div><h3>Tu constancia</h3><p>Hábitos de entrenamiento · {sourceName(source)}</p></div><div className="pd-range" aria-label="Periodo de constancia">{[13,26,52].map(n=><button key={n} aria-pressed={weeks===n} onClick={()=>onWeeks(n)}>{n===13?'3 meses':n===26?'6 meses':'1 año'}</button>)}</div></header>
    <div className="pd-habit-grid">{kinds.map(({id,title,color,Icon})=>{
      const activeDays=timeline.days.filter(d=>d[id]>0).length;
      return <article className="pd-card pd-habit" key={id} style={{'--habit':color} as CSSProperties}>
        <header><span className="pd-habit-symbol"><Icon size={18}/></span><h4>{title}</h4><button aria-label={'Ver registros de '+title} onClick={()=>openHistory(id)}><ArrowUpRight size={15}/></button></header>
        <div className="pd-habit-count"><strong>{sourceReady?timeline.counts[id]:'—'}</strong><span>{timeline.counts[id]===1?'sesión':'sesiones'}<small>{sourceReady?activeDays+" "+(activeDays===1?'día con registro':'días con registro'):'Fuente no disponible'}</small></span></div>
        <div className="pd-months" style={{'--weeks':weeks} as CSSProperties} aria-hidden="true">{monthLabels.filter(m=>m.index<weeks-2).map(m=><span key={m.index} style={{gridColumn:m.index+1}}>{m.label}</span>)}</div>
        <div className="pd-heatmap" style={{'--weeks':weeks} as CSSProperties} role="img" aria-label={sourceReady?`${timeline.counts[id]} sesiones de ${title} realizadas y fechadas en ${weeks} semanas`:'Sin datos de la fuente para '+title}>
          {timeline.days.map(d=><span key={d.day} className={(sourceReady&&d[id]>0?'filled ':'')+(d.future?'future':'')} style={sourceReady&&d[id]>0?{opacity:Math.min(1,.55+d[id]*.2)}:undefined} title={fullDate(d.day)+(d.future?' · fecha futura':!sourceReady?' · fuente sin leer':`: ${d[id]} registros realizados`)}/>)}
        </div>
        <footer><span>{shortDate(timeline.start)} — {shortDate(timeline.today)}</span><span><i/>Con registro</span></footer>
      </article>;
    })}</div>
    <p className="pd-caption">{!sourceReady?'Fuente sin leer: los contadores quedan sin dato. ':''}Casilla vacía = sin registro. Solo cuentan sesiones realizadas con fecha confirmada. Diario y Salud se consultan por separado para no sumar dos veces una sesión.{source==='health'&&' Distintas mediciones parcialmente solapadas pueden seguir apareciendo por separado.'}</p>
  </section>;

  return <div className={'pd pd-view-'+view}>
    <div className="pd-source-row"><div className="pd-source-switch" aria-label="Fuente de entrenamientos"><button aria-pressed={source==='diary'} onClick={()=>onSource('diary')}>Diario de Alfred</button><button aria-pressed={source==='health'} onClick={()=>onSource('health')}>Apple Health</button></div><span><i/>Registros locales{health?' · importación '+shortDate(health.importedAt):' · Health pendiente'}</span></div>
    {view==='all'?<div className="pd-overview">{session}{healthPanel}</div>:view==='training'?session:view==='health'?<div className="pd-health-focus">{healthPanel}<section className="pd-card pd-energy"><header className="pd-card-heading"><h3>Movimiento registrado</h3><span>Apple Health · kcal</span></header>{energyDays.length?<><div className="pd-energy-bars" role="img" aria-label={energyDays.map(d=>`${fullDate(d.date)}: ${format(d.energy!)} kcal`).join('; ')}>{energyDays.map(d=><div key={d.date} title={`${fullDate(d.date)} · ${format(d.energy!)} kcal`}><span style={{height:d.energy!/maxEnergy*100+'%'}}/></div>)}</div><div className="pd-chart-dates"><span>{shortDate(energyDays[0].date)}</span><span>{shortDate(energyDays[energyDays.length-1].date)}</span></div><p className="pd-caption">Últimos {energyDays.length} días con datos de movimiento importados. No incluye días sin registro.</p></>:<div className="pd-connect"><HeartPulse size={32}/><h4>Tu salud, con contexto.</h4><p>Importa Apple Health para ver las mediciones de tu Watch y cómo cambia tu actividad.</p><button onClick={onSources}>Preparar importación <ArrowUpRight size={14}/></button></div>}</section></div>:null}
    {(view==='all'||view==='training')&&progressPanel}
    {(view==='all'||view==='habits')&&habits}
    {(view==='all'||view==='training')&&<section className="pd-history" id="ld-training">
      <header className="pd-section-heading"><div><h3 id="pd-history-title" tabIndex={-1}>{view==='all'?'Últimos registros':'Historial de entrenamientos'}</h3><p>Registros completos, incluidos los parciales y planificados.</p></div><span>{history.length} registros</span></header>
      {view==='training'&&<div className="pd-kind-filter" aria-label="Tipo de entrenamiento"><button aria-pressed={!kind} onClick={()=>onKind(null)}>Todos</button>{kinds.map(k=><button key={k.id} aria-pressed={kind===k.id} onClick={()=>onKind(k.id)}>{k.title}</button>)}</div>}
      {kind&&view==='all'&&<button className="pd-filter" onClick={()=>onKind(null)}>{kinds.find(k=>k.id===kind)?.title}<X size={12}/></button>}
      {history.length?<div className="pd-history-list">{history.slice(0,historyLimit).map(entry=><TrainingEntry key={entry.id} entry={entry} note={source==='diary'?data?.training.note||null:null}/>)}</div>:<div className="pd-empty-history"><CalendarDays size={22}/><p>{source==='health'?(!health?'Importa Apple Health o selecciona tu diario para ver entrenamientos.':'No hay entrenamientos importados para esta selección.'):data?.training.status==='error'?'No se ha podido leer el diario. Revisa el vault y sus permisos.':data?.training.status==='unconfigured'?'Configura tu vault en los ajustes de Prompted OS.':'No hay sesiones documentadas para esta selección.'}</p></div>}
      {view==='all'&&records.length>0&&<button className="pd-more" onClick={()=>openHistory(null)}>Ver todo el historial <ArrowUpRight size={13}/></button>}
      {view==='training'&&history.length>limit&&<button className="pd-more" onClick={()=>onLimit(limit+12)}>Cargar más registros ({history.length-limit}) <ChevronDown size={13}/></button>}
    </section>}
    {data?.health.status==='error'&&<p className="ld-error" role="alert">No se puede leer la importación guardada. Vuelve a importar el XML desde Fuentes de datos.</p>}
  </div>;
}
