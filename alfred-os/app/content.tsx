'use client';
// Contenido: the content calendar of every project (what goes out, when, and how it looks), the profiles' bio preview and,
// as a third part, Tendencias (what works out there). Same split view as Entregas: sidebar · list by day · inspector.
// The content machine stays the backend: a new time or «ya publicado» is written to its calendar by the local server.
import {useEffect,useMemo,useRef,useState,type ReactNode} from 'react';
import {CalendarDays,UserRound,TrendingUp,Sun,Trophy,Layers,FolderClosed,Clock3,CheckCircle2,Inbox,ChevronLeft,Copy,ExternalLink,EyeOff,Check,Ruler} from 'lucide-react';
import {TrendsView} from './trends';
import {mejores,posicionTasa,publicados,tasa} from '../lib/contenido-tasas';
import {avisoAnalisis} from '../lib/contenido-analisis';
import {avisoEnlacePerfil,avisoSinEnlace,etiquetaEnlace,ofreceLanzado,perfilParaLanzar} from '../lib/contenido-enlace';
import {descartesDeLanzar,MOTIVOS_DESCARTE,pendientesDeLanzar,tareasDeLanzar} from '../lib/experimentos-cola';
import './board.css';
import './content.css';

type Video={nombre:string;path:string};
type Item={id:string;fuente:'maquina'|'plan'|'video'|'ritmo'|'calendario';brand:string;marca:string;canal:string;tipo:string;fecha:string|null;titulo:string;gancho:string;texto:string;enlace:string;nota:string;
 estado:'publicado'|'programado'|'postonce'|'tu'|'falta'|'fallo'|'plan'|'sin-fecha';detalle:string;url:string;videos:Video[];portadas:string[];elegido:number;horaEditable:boolean;marcable:boolean;porMedir:boolean;medida:{vistas:number;visitas:number;clics:number;compras:number;hora:string}|null};
type Perfil={brand:string;red:string;usuario:string;nombre:string;bio:string;enlace:string;nota:string};
type Analisis={fecha:string;marcas:Record<string,{resumen:string;conclusiones:string[];validados:string[];
 ganadores:{titulo:string;red:string;vistas:number;x_mediana:number;url:string}[];
 ganchos_que_ganan:string[];conceptos_que_ganan:string[];evitar:string[];repetir:string[];
 perdedores:{titulo:string;red:string;vistas:number;x_mediana:number;url:string}[];
 exprimir:{concepto:string;por_que:string;variantes:{gancho:string;angulo:string}[]}|null}>};
type Data={disponible:boolean;motivo:string;plan:string;items:Item[];perfiles:Perfil[];marcas?:{id:string;nombre:string}[];analisis?:Analisis|null;analisisFecha?:string|null;porRed:{brand:string;marca:string;red:string;publicadas:number;medidas:number;vistas:number;visitas:number;clics:number;compras:number}[]};

const CANAL:Record<string,{name:string;short:string}>={instagram:{name:'Instagram',short:'IG'},tiktok:{name:'TikTok',short:'TT'},x:{name:'X',short:'X'},youtube:{name:'YouTube',short:'YT'},
 telegram:{name:'Telegram',short:'TG'},discord:{name:'Discord',short:'DC'},email:{name:'Email',short:'@'}};
const TONE:Record<Item['estado'],string>={publicado:'#5fd38d',programado:'#7cb6ff',postonce:'#7cb6ff',tu:'#ffb35c',falta:'#ff7a70',fallo:'#ff7a70',plan:'#a3a7b0','sin-fecha':'#a3a7b0'};
const media=(p:string)=>'/api/contenido/media?p='+encodeURIComponent(p);
const isoDay=(d:Date)=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
const hm=(s:string)=>new Date(s).toLocaleTimeString('es-ES',{hour:'2-digit',minute:'2-digit'});
function dayName(key:string){
 const today=isoDay(new Date()),tomorrow=isoDay(new Date(Date.now()+86400000)),d=new Date(key+'T12:00:00');
 const label=d.toLocaleDateString('es-ES',{weekday:'short',day:'numeric',month:'short'}).replace(/\./g,'');
 return key===today?'Hoy · '+label:key===tomorrow?'Mañana · '+label:label[0].toUpperCase()+label.slice(1);
}
/** «2026-10-21T18:00:00Z» → «2026-10-21T20:00» for <input type=datetime-local> (the Mac's time zone). */
const toLocalInput=(s:string|null)=>{const d=s?new Date(s):new Date(Date.now()+3600000);return isoDay(d)+'T'+d.toTimeString().slice(0,5);};
/** Still to go out: from today on, plus anything overdue that is waiting for the owner. */
const upcoming=(i:Item,startToday:number)=>!!i.fecha&&i.estado!=='publicado'&&(Date.parse(i.fecha)>=startToday||['tu','falta','fallo'].includes(i.estado));
function Canal({c}:{c:string}){return <span className="tr-plat" aria-label={CANAL[c]?.name||c}>{CANAL[c]?.short||c.slice(0,2).toUpperCase()}</span>;}

export function ContentView(){
 const [part,setPart]=useState<'hoy'|'calendario'|'perfiles'|'tendencias'>('hoy');
 const [data,setData]=useState<Data|null>(null),[err,setErr]=useState(''),[brand,setBrand]=useState(''),[show,setShow]=useState<'proximo'|'sin-fecha'|'publicado'|'por-medir'>('proximo');
 const [selected,setSelected]=useState<string|null>(null),[open,setOpen]=useState(false),[tick,setTick]=useState(0);
 const [abrir,setAbrir]=useState(false),[lanzar,setLanzar]=useState<string[]>([]);
 const vinoDeEntregas=useRef(false);   // StrictMode runs the mount effect twice: the second run must not fall back to ct-part
 useEffect(()=>{try{
  if(sessionStorage.getItem('ct-abrir')==='perfil-sin-enlace'){sessionStorage.removeItem('ct-abrir');setLanzar(tareasDeLanzar(sessionStorage.getItem('ct-lanzar')));sessionStorage.removeItem('ct-lanzar');vinoDeEntregas.current=true;setPart('perfiles');setBrand('');setAbrir(true);return;}
  if(vinoDeEntregas.current)return;
  const p=localStorage.getItem('ct-part');if(p==='calendario'||p==='perfiles'||p==='tendencias')setPart(p);}catch{}},[]);
 useEffect(()=>{if(!abrir)return;if(err){setAbrir(false);return;}if(!data)return;const id=perfilParaLanzar(data.perfiles);if(id){setSelected(id);setOpen(true);}setAbrir(false);},[data,abrir,err]);
 const choosePart=(p:typeof part)=>{setPart(p);setSelected(null);setOpen(false);try{localStorage.setItem('ct-part',p);}catch{}};
 useEffect(()=>{let gone=false;
  fetch('/api/contenido',{cache:'no-store'}).then(async r=>{const j:any=await r.json();if(gone)return;if(!r.ok)throw new Error(j.error);setData(j);setErr('');})
   .catch(e=>{if(!gone)setErr(e instanceof Error?e.message:'No se ha podido leer el contenido.');});
  return()=>{gone=true;};},[tick]);
 const reload=()=>setTick(t=>t+1);
 const items=useMemo(()=>(data?.items||[]).filter(i=>!brand||i.brand===brand),[data,brand]);
 const BRANDS=[{id:'',nombre:'Todo'},...(data?.marcas||[])];   // the accounts come from the configuration
 const startToday=new Date(new Date().setHours(0,0,0,0)).getTime();
 const lists={
  proximo:items.filter(i=>upcoming(i,startToday)),
  'sin-fecha':items.filter(i=>!i.fecha),
  publicado:publicados(items),
  'por-medir':items.filter(i=>i.porMedir).reverse(),
 };
 const rows=lists[show];
 const top=part==='calendario'&&show==='publicado'?mejores(lists.publicado):[];
 const groups=useMemo(()=>{const m=new Map<string,Item[]>();for(const i of rows){const k=i.fecha?isoDay(new Date(i.fecha)):'';if(!m.has(k))m.set(k,[]);m.get(k)!.push(i);}return [...m.entries()];},[rows]);
 const item=rows.find(i=>i.id===selected);
 useEffect(()=>{if(part==='calendario'&&show!=='proximo'&&rows.length&&!rows.some(r=>r.id===selected)&&window.innerWidth>1180)setSelected(rows[0].id);},[data,show,brand,part]);// eslint-disable-line
 const perfiles=(data?.perfiles||[]).filter(p=>!brand||p.brand===brand);
 const perfilesConAviso=perfiles.filter(p=>avisoEnlacePerfil(p.brand,p.enlace)!=='').length;
 const perfilesSinEnlaceTotal=(data?.perfiles||[]).filter(p=>avisoEnlacePerfil(p.brand,p.enlace)!=='').length;
 const sinEnlace=avisoSinEnlace(data?.items||[],Date.now());
 const porRed=(data?.porRed||[]).filter(r=>!brand||r.brand===brand);
 const perfil=part==='perfiles'?perfiles.find(p=>p.brand+p.red===selected)||null:null;
 useEffect(()=>{if(!abrir&&part==='perfiles'&&perfiles.length&&!perfil&&window.innerWidth>1180)setSelected(perfiles[0].brand+perfiles[0].red);},[data,part,brand,abrir]);// eslint-disable-line
 const pick=(id:string)=>{setSelected(id);setOpen(true);};
 const nav=(id:string,label:string,Icon:any,count?:number,on?:boolean,onClick?:()=>void)=><button key={id} className={'ob-nav'+(on?' on':'')} aria-current={on?'true':undefined} onClick={onClick}><span className="ob-ico"><Icon size={15}/></span><span>{label}</span>{count?<b>{count}</b>:null}</button>;
 const partsNav=<><h3>Contenido</h3><div className="ob-side-group">
  {nav('ph','Hoy',Sun,undefined,part==='hoy',()=>choosePart('hoy'))}
  {nav('pc','Calendario',CalendarDays,undefined,part==='calendario',()=>choosePart('calendario'))}
  {nav('pp','Perfiles',UserRound,perfilesSinEnlaceTotal,part==='perfiles',()=>choosePart('perfiles'))}
  {nav('pt','Tendencias',TrendingUp,undefined,part==='tendencias',()=>choosePart('tendencias'))}
 </div></>;
 if(part==='tendencias')return <TrendsView arriba={partsNav}/>;
 if(part==='hoy')return <DayView arriba={partsNav} analisis={data?.analisis} analisisFecha={data?.analisisFecha} marcas={data?.marcas||[]} sinEnlace={sinEnlace}/>;
 const count=(b:string)=>(data?.items||[]).filter(i=>(!b||i.brand===b)&&upcoming(i,startToday)).length;
 const title=part==='perfiles'?'Perfiles':show==='proximo'?'Próximo':show==='sin-fecha'?'Listos, sin fecha':show==='por-medir'?'Por medir':'Publicado';
 const sub=part==='perfiles'?'Cómo se verá cada cuenta':show==='proximo'?'Lo que sale, día a día':show==='sin-fecha'?'Reels terminados que aún no tienen día':show==='por-medir'?'Publicadas hace más de un día, sin cifras':'Las últimas dos semanas';
 return <section className={'j-workspace ob ct'+(part==='calendario'&&show==='proximo'?' ct-weekmode'+(item?' ct-weeksel':''):'')}>
  <aside className="ob-side" aria-label="Contenido">
   <div className="ob-side-scroll">
    {partsNav}
    <h3>Proyectos</h3>
    <div className="ob-side-group">{BRANDS.map(b=>nav('b'+b.id,b.nombre,b.id?FolderClosed:Layers,part==='calendario'?count(b.id):undefined,brand===b.id,()=>{setBrand(b.id);setSelected(null);setOpen(false);}))}</div>
    {part==='calendario'&&<><h3>Mostrar</h3><div className="ob-side-group">
     {nav('sp','Próximo',Clock3,lists.proximo.length,show==='proximo',()=>{setShow('proximo');setSelected(null);setOpen(false);})}
     {nav('ss','Listos, sin fecha',Inbox,lists['sin-fecha'].length,show==='sin-fecha',()=>{setShow('sin-fecha');setSelected(null);setOpen(false);})}
     {nav('sd','Publicado',CheckCircle2,lists.publicado.length,show==='publicado',()=>{setShow('publicado');setSelected(null);setOpen(false);})}
     {nav('sm','Por medir',Ruler,lists['por-medir'].length,show==='por-medir',()=>{setShow('por-medir');setSelected(null);setOpen(false);})}
    </div></>}
   </div>
  </aside>
  <div className="ob-main">
   {part==='calendario'&&show==='proximo'?<WeekCalendar items={items} marcas={data?.marcas||[]} selected={selected} onPick={pick} loading={!data&&!err} error={err}/>:<>
   <header className="ob-list-head"><div className="ob-title"><h1>{title}</h1><span>{BRANDS.find(b=>b.id===brand)?.nombre} · {sub}</span></div></header>
   <div className="ob-list" role="listbox" aria-label={title}>
    {top.length>0&&<section className="ob-sec"><h2>Lo que más trae</h2>{top.length===1&&<p className="ob-note">Solo hay una publicación medida: todavía no hay con qué comparar.</p>}
     {top.map(i=><button key={'top:'+i.id} role="option" aria-selected={i.id===selected} className={'ob-row'+(i.id===selected?' sel':'')} onClick={()=>pick(i.id)}>
      <Canal c={i.canal}/>
      <span className="ob-row-text"><strong>{i.titulo}</strong><small>{!brand?i.marca+' · ':''}{tasa(i.medida!.clics,i.medida!.vistas)} clics por cada 100 visualizaciones · {i.medida!.vistas.toLocaleString('es-ES')} visualizaciones{i.medida!.compras>0?' · '+i.medida!.compras.toLocaleString('es-ES')+' compras':''}</small></span>
     </button>)}
    </section>}
    {part==='calendario'&&(show==='publicado'||show==='por-medir')&&porRed.length>0&&<section className="ob-sec"><h2>Por red</h2><div className="ob-group-box ct-por-red">
     <table aria-label="Por red"><thead><tr><th scope="col">Red</th>{!brand&&<th scope="col">Marca</th>}<th scope="col">Publicadas</th><th scope="col">Medidas</th><th scope="col">Visualizaciones</th><th scope="col">Visitas</th><th scope="col">Clics</th><th scope="col">Compras</th><th scope="col">Visitas / vis.</th><th scope="col">Clics / vis.</th><th scope="col">Compras / clic</th></tr></thead>
      <tbody>{porRed.map(r=><tr key={r.brand+':'+r.red}><th scope="row"><Canal c={r.red}/> {CANAL[r.red]?.name||r.red}</th>{!brand&&<td>{r.marca}</td>}{(['publicadas','medidas','vistas','visitas','clics','compras'] as const).map(k=><td key={k}>{r[k].toLocaleString('es-ES')}</td>)}<td>{tasa(r.visitas,r.vistas)}</td><td>{tasa(r.clics,r.vistas)}</td><td>{tasa(r.compras,r.clics)}</td></tr>)}</tbody>
     </table>
    </div></section>}
    {part==='calendario'&&groups.map(([k,list])=><section key={k||'none'} className="ob-sec"><h2>{k?dayName(k):'Sin fecha'}<span>{list.length}</span></h2>
     {list.map(i=><button key={i.id} role="option" aria-selected={i.id===selected} className={'ob-row'+(i.id===selected?' sel':'')} onClick={()=>pick(i.id)}>
      <Canal c={i.canal}/>
      <span className="ob-row-text"><strong>{i.titulo}</strong><small>{!brand?i.marca+' · ':''}{i.tipo} · <span className="ct-state" style={{'--t':TONE[i.estado]} as any}>{i.detalle}</span>{(show==='publicado'||show==='por-medir')&&<>{i.medida&&<> · {i.medida.vistas.toLocaleString('es-ES')} visualizaciones · {i.medida.clics.toLocaleString('es-ES')} clics</>}{i.porMedir&&<> · <span className="ct-state" style={{'--t':'#ffb35c'} as any}>Falta medir</span></>}</>}</small></span>
      <span className="ob-row-meta">{i.fecha&&<time>{hm(i.fecha)}</time>}{i.videos.length?<span className="ct-has-video" aria-label="Con vídeo"/>:null}</span>
     </button>)}
    </section>)}
    {part==='perfiles'&&perfilesConAviso>0&&<section className="ob-sec"><p className="ob-note">{perfilesConAviso===1?'1 cuenta':perfilesConAviso+' cuentas'} sin enlace medido: sus publicaciones no se podrán atribuir. Abre cada una y pon un enlace completo con https://.</p></section>}
    {part==='perfiles'&&perfiles.map(p=>{const aviso=avisoEnlacePerfil(p.brand,p.enlace);return <button key={p.brand+p.red} role="option" aria-selected={p.brand+p.red===selected} className={'ob-row'+(p.brand+p.red===selected?' sel':'')} onClick={()=>pick(p.brand+p.red)}>
     <Canal c={p.red}/>
     <span className="ob-row-text"><strong>{p.nombre}</strong><small>{CANAL[p.red]?.name} · {p.bio?p.bio:'Bio sin decidir'}{aviso&&<> · <span className="ct-state" style={{'--t':'#ffb35c'} as any}>{aviso==='vacio'?'Sin enlace medido':'Enlace sin https://'}</span></>}</small></span>
    </button>})}
    {part==='calendario'&&data&&!rows.length&&<div className="ob-blank"><p>{!data.disponible?(data.motivo||'Todavía no hay contenido.'):show==='proximo'?'Nada programado.':show==='sin-fecha'?'No hay reels esperando fecha.':show==='por-medir'?'Todo lo publicado está medido.':'Nada publicado en las últimas dos semanas.'}</p></div>}
    {err&&<div className="ob-blank"><p>{err}</p></div>}
    {!data&&!err&&<div className="ob-blank"><p>Leyendo el calendario…</p></div>}
   </div></>}
  </div>
  <div className={'ob-inspector'+(open&&(item||perfil)?' open':'')+(part==='calendario'&&show==='proximo'&&!item?' ct-week-off':'')}>
   {part==='calendario'&&item?<Detail key={item.id} item={item} red={data?.porRed.find(r=>r.brand===item.brand&&r.red===item.canal)||null} plan={data?.plan||''} back={title} onBack={()=>setOpen(false)} onChange={reload}/>
   :part==='perfiles'&&perfil?<ProfileDetail key={perfil.brand+perfil.red} perfil={perfil} items={(data?.items||[]).filter(i=>i.brand===perfil.brand&&i.canal===perfil.red)} onBack={()=>setOpen(false)} onSaved={reload} lanzar={lanzar} onHecha={id=>setLanzar(l=>pendientesDeLanzar(l,[id]))} onLanzado={()=>setLanzar([])}/>
   :<div className="ob-blank"><p>Elige algo de la lista.</p></div>}
  </div>
 </section>;
}

async function post(body:object){
 const r=await fetch('/api/contenido',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const j:any=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.error||'No se ha podido guardar.');return j;
}

function Detail({item,red,plan,back,onBack,onChange}:{item:Item;red:Data['porRed'][number]|null;plan:string;back:string;onBack:()=>void;onChange:()=>void}){
 const [v,setV]=useState(Math.min(item.elegido,Math.max(0,item.videos.length-1))),[when,setWhen]=useState(toLocalInput(item.fecha)),[busy,setBusy]=useState(''),[msg,setMsg]=useState(''),[copied,setCopied]=useState(false),[copiedEnlace,setCopiedEnlace]=useState(false),[url,setUrl]=useState('');
 const [medida,setMedida]=useState({vistas:item.medida?.vistas.toString()??'',visitas:item.medida?.visitas.toString()??'',clics:item.medida?.clics.toString()??'',compras:item.medida?.compras.toString()??''});
 const camposMedida=[['vistas','Visualizaciones'],['visitas','Visitas a la web'],['clics','Clics al enlace'],['compras','Compras']] as const;
 const medidaCompleta=camposMedida.every(([k])=>medida[k]!==''&&Number.isInteger(Number(medida[k]))&&Number(medida[k])>=0);
 const medidaCambiada=camposMedida.some(([k])=>Number(medida[k])!==item.medida?.[k]);
 const {vistas,visitas,clics,compras}={vistas:Number(medida.vistas),visitas:Number(medida.visitas),clics:Number(medida.clics),compras:Number(medida.compras)};
 const video=item.videos[v],cover=item.portadas[Math.min(v,item.portadas.length-1)];
 const etiqueta=etiquetaEnlace(item.enlace);
 const act=async(what:string,body:object,ok:string)=>{setBusy(what);setMsg('');try{await post(body);setMsg(ok);onChange();}catch(e){setMsg(e instanceof Error?e.message:String(e));}finally{setBusy('');}};
 const copy=async()=>{try{await navigator.clipboard.writeText(item.texto);setCopied(true);setMsg(msg=>msg==='No he podido copiarlo: selecciónalo y cópialo a mano.'?'':msg);setTimeout(()=>setCopied(false),1600);}catch{setMsg('No he podido copiarlo: selecciónalo y cópialo a mano.')}};
 const copyEnlace=async()=>{try{await navigator.clipboard.writeText(item.enlace);setCopiedEnlace(true);setMsg(msg=>msg==='No he podido copiarlo: selecciónalo y cópialo a mano.'?'':msg);setTimeout(()=>setCopiedEnlace(false),1600);}catch{setMsg('No he podido copiarlo: selecciónalo y cópialo a mano.')}};
 const changed=when&&toLocalInput(item.fecha)!==when;
 const screen=item.gancho&&item.gancho!==item.texto?item.gancho:'';
 const comparacion=(parte:number,totalRed:number)=>{
  if(!red)return null;
  const posicion=posicionTasa(parte,item.medida!.vistas,totalRed,red.vistas,red.medidas);
  if(posicion==='primera')return ' · primera medida de esta red';
  const textoPosicion=posicion==='media'?' · en la media':posicion==='encima'?' · por encima':posicion==='debajo'?' · por debajo':'';
  return ' · media de '+(CANAL[red.red]?.name||red.red)+' en '+item.marca+': '+tasa(totalRed,red.vistas)+textoPosicion;
 };
 return <article className="ob-insp">
  <button className="ob-back" onClick={onBack}><ChevronLeft size={16}/>{back}</button>
  <p className="ob-insp-eyebrow">{item.marca} · {CANAL[item.canal]?.name||item.canal} · {item.tipo}</p>
  <h2>{item.titulo}</h2>
  <div className="ob-pills"><span className="ob-pill status plain" style={{'--t':TONE[item.estado]} as any}>{item.detalle}</span>{item.fecha&&<span className="ob-pill plain">{new Date(item.fecha).toLocaleString('es-ES',{weekday:'long',day:'numeric',month:'long',hour:'2-digit',minute:'2-digit'})}</span>}</div>
  <div className="ct-preview">
   <div className="ct-phone">
    {video?<video key={video.path} src={media(video.path)} poster={cover?media(cover):undefined} controls playsInline preload="metadata"/>
     :cover?<img src={media(cover)} alt=""/>
     :<div className="ct-phone-text"><p>{screen||item.texto||'Sin texto todavía.'}</p></div>}
   </div>
   {item.videos.length>1&&<div className="tr-seg ct-openings" role="group" aria-label="Apertura">{item.videos.map((x,i)=><button key={x.path} className={i===v?'on':''} aria-pressed={i===v} onClick={()=>setV(i)}>{x.nombre}{i===item.elegido&&item.fuente==='maquina'?' · principal':''}</button>)}</div>}
   {!item.videos.length&&item.estado==='falta'&&<p className="ob-note">Todavía no hay vídeo para esta pieza.</p>}
  </div>
  <div className="ob-group"><h4>Cuándo</h4><div className="ob-group-box ct-when">
   {item.horaEditable?<><input type="datetime-local" value={when} onChange={e=>setWhen(e.target.value)} aria-label="Día y hora"/>
    <button className="ob-primary" disabled={!changed||!!busy} onClick={()=>act('hora',{accion:'hora',id:item.id,fecha:new Date(when).toISOString()},'Hora cambiada.')}>{busy==='hora'?'Guardando…':item.fecha?'Cambiar hora':'Poner fecha'}</button></>
    :<p className="ob-note">{item.estado==='postonce'?'Ya está en PostOnce: la hora se cambia allí.':item.estado==='publicado'?'Publicado: ya no se cambia.':item.fuente==='ritmo'?'Hora fija del plan de contenido: Trial Reels a las 10:30 y 17:30.':item.fuente==='calendario'?'Lo publica solo el calendario de publicación a esta hora.':'Esta hora la marca el plan.'}</p>}
  </div></div>
  {item.marcable&&<div className="ob-group"><h4>¿Ya ha salido?</h4><div className="ob-group-box ct-when">
   {item.estado==='publicado'?<>{item.url&&<a className="tr-link" href={item.url} target="_blank" rel="noreferrer"><ExternalLink size={13}/> Ver la publicación</a>}
     <button className="ct-ghost" disabled={!!busy} onClick={()=>act('pub',{accion:'publicado',id:item.id,publicado:false},'Vuelve a estar pendiente.')}>Marcar como pendiente</button></>
    :<><input type="url" value={url} onChange={e=>setUrl(e.target.value)} placeholder="Enlace (opcional)" aria-label="Enlace de la publicación"/>
     <button className="ob-primary" disabled={!!busy} onClick={()=>act('pub',{accion:'publicado',id:item.id,publicado:true,url},'Marcado como publicado.')}><Check size={14}/>{busy==='pub'?'Guardando…':'Ya está publicado'}</button></>}
  </div></div>}
  {item.estado==='publicado'&&(item.fuente!=='ritmo'||item.marcable)&&<div className="ob-group"><h4>Lo medido</h4><div className="ob-group-box">
   <div className="ct-medida">{camposMedida.map(([k,label])=><label key={k} className="ct-field"><span>{label}</span><input type="number" min={0} step={1} value={medida[k]} onChange={e=>setMedida({...medida,[k]:e.target.value})}/></label>)}</div>
   <div className="ct-when"><button className="ob-primary" disabled={!!busy||!medidaCompleta||!medidaCambiada} onClick={()=>act('medir',{accion:'medido',id:item.id,medida:{vistas,visitas,clics,compras}},'Medido.')}>{busy==='medir'?'Guardando…':'Guardar lo medido'}</button>
    {item.medida&&<p className="ob-note">Medido el {new Date(item.medida.hora).toLocaleString('es-ES')}</p>}
   </div>
   {item.medida&&<><p className="ob-note">Visitas por cada 100 visualizaciones: {tasa(item.medida.visitas,item.medida.vistas)}{comparacion(item.medida.visitas,red?.visitas||0)}</p><p className="ob-note">Clics por cada 100 visualizaciones: {tasa(item.medida.clics,item.medida.vistas)}{comparacion(item.medida.clics,red?.clics||0)}</p>{item.medida.clics>0&&<p className="ob-note">Compras por cada 100 clics: {tasa(item.medida.compras,item.medida.clics)}</p>}</>}
  </div></div>}
  {msg&&<p className="ob-note ct-msg" role="status">{msg}</p>}
  {screen&&<div className="ob-group"><h4>En pantalla</h4><div className="ob-group-box"><p className="tr-text">{screen}</p></div></div>}
  {item.enlace&&<div className="ob-group"><h4 className="ct-h4">Enlace medido<button className="ct-ghost" onClick={copyEnlace}>{copiedEnlace?<><Check size={12}/>Copiado</>:<><Copy size={12}/>Copiar</>}</button></h4><div className="ob-group-box"><p className="tr-text" style={{userSelect:'text'}}>{item.enlace.replace(/^https:\/\//,'')}</p>{etiqueta&&<p className="ob-note">Etiqueta en las estadísticas: {etiqueta}</p>}</div><p className="ob-note">Pégalo donde la red deje un enlace (bio, historia o descripción) para saber qué visitas trae este vídeo.</p></div>}
  {!item.enlace&&item.brand!=='beta'&&item.estado!=='publicado'&&<p className="ob-note">Pon un enlace https en el perfil de {item.marca} en {CANAL[item.canal]?.name||item.canal} para que esta publicación tenga su enlace medido.</p>}
  {item.texto&&<div className="ob-group"><h4 className="ct-h4">Texto<button className="ct-ghost" onClick={copy}>{copied?<><Check size={12}/>Copiado</>:<><Copy size={12}/>Copiar</>}</button></h4><div className="ob-group-box"><p className="tr-text" style={{userSelect:'text'}}>{item.texto}</p></div></div>}
  {item.nota&&<div className="ob-group"><h4>Nota</h4><div className="ob-group-box"><p className="tr-text">{item.nota}</p></div></div>}
  <div className="ob-actions">
   {item.fuente==='plan'&&plan&&<a className="tr-link" href={'obsidian://open?path='+encodeURIComponent(plan)}>Ver el plan en Obsidian</a>}
   {item.fuente==='video'&&!item.fecha&&<button className="ct-ghost" disabled={!!busy} onClick={()=>act('ocultar',{accion:'ocultar',id:item.id},'Oculto.')}><EyeOff size={12}/>Ocultar</button>}
  </div>
  <p className="ob-note ct-source">{item.fuente==='maquina'?'Del calendario de la máquina de contenido.':item.fuente==='plan'?'Del plan de publicaciones del vault.':item.fuente==='ritmo'?'Del ritmo del plan de contenido.':item.fuente==='calendario'?'Del calendario de publicación: se publica solo y aquí ves su estado real en PostOnce.':'Del sistema de contenidos (full-visual).'}</p>
 </article>;
}

function ProfileDetail({perfil,items,onBack,onSaved,lanzar,onHecha,onLanzado}:{perfil:Perfil;items:Item[];onBack:()=>void;onSaved:()=>void;lanzar:string[];onHecha:(id:string)=>void;onLanzado:()=>void}){
 const [p,setP]=useState(perfil),[busy,setBusy]=useState(false),[msg,setMsg]=useState(''),[guardado,setGuardado]=useState<string|null>(null),[lanzando,setLanzando]=useState(false),[descartando,setDescartando]=useState(false),[motivo,setMotivo]=useState('');
 const changed=(['usuario','nombre','bio','enlace'] as const).some(k=>p[k]!==perfil[k]);
 const enlaceInvalido=avisoEnlacePerfil(p.brand,p.enlace)==='invalido';
 const save=async()=>{setBusy(true);setMsg('');setGuardado(null);try{await post({accion:'perfil',perfil:p});setMsg('Guardado. Recuerda cambiarlo también en '+(CANAL[p.red]?.name||p.red)+'.');setGuardado(p.enlace);onSaved();}catch(e){setMsg(e instanceof Error?e.message:String(e));}finally{setBusy(false);}};
 // Opened from Entregas to launch a link experiment: once a valid https link is saved, the owner can mark those tasks as launched right here.
 const ofrecerLanzado=ofreceLanzado(lanzar,p.brand,guardado!==null?guardado:(p.enlace===perfil.enlace?perfil.enlace:''));
 const lanzado=async()=>{setLanzando(true);setMsg('');const fallo='No se ha podido apuntar: hazlo desde Entregas.';
  try{for(const tarea of lanzar){const r=await fetch('/api/equipo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'lanzado',tarea})});
    if(!r.ok){const j:any=await r.json().catch(()=>null);throw new Error(j?.error||fallo);}
    onHecha(tarea);}
   setMsg('Apuntado como lanzado. En unos días te pregunto qué salió.');onLanzado();}
  catch(e){setMsg(e instanceof Error&&e.message?e.message:fallo);}finally{setLanzando(false);}};
 // «No lo voy a lanzar»: one of the quick reasons is sent as a discard for each pending task, same bodies as Entregas.
 const descartar=async(m:string)=>{setLanzando(true);setMotivo(m);setMsg('');const fallo='No se ha podido apuntar: hazlo desde Entregas.';
  try{for(const cuerpo of descartesDeLanzar(lanzar,m)){const r=await fetch('/api/equipo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cuerpo)});
    if(!r.ok){const j:any=await r.json().catch(()=>null);throw new Error(j?.error||fallo);}
    onHecha(cuerpo.tarea);}
   setMsg('Apuntado: no lo lanzas. El equipo lo tendrá en cuenta.');onLanzado();}
  catch(e){setMotivo('');setMsg(e instanceof Error&&e.message?e.message:fallo);}finally{setLanzando(false);}};
 // The grid shows what the account will look like: the next reels first, then what is already out.
 const grid=items.filter(i=>i.portadas.length||i.videos.length).sort((a,b)=>(b.fecha?Date.parse(b.fecha):0)-(a.fecha?Date.parse(a.fecha):0)).slice(0,9);
 const field=(k:'usuario'|'nombre'|'bio'|'enlace',label:string,max:number,area=false)=><label className="ct-field"><span>{label}<i>{p[k].length}/{max}</i></span>
  {area?<textarea value={p[k]} maxLength={max} rows={3} onChange={e=>setP({...p,[k]:e.target.value})}/>:<input value={p[k]} maxLength={max} onChange={e=>setP({...p,[k]:e.target.value})}/>}</label>;
 return <article className="ob-insp">
  <button className="ob-back" onClick={onBack}><ChevronLeft size={16}/>Perfiles</button>
  <p className="ob-insp-eyebrow">{CANAL[p.red]?.name} · vista previa</p>
  <div className="ct-profile">
   <div className="ct-profile-head"><span className="ct-avatar">{(p.nombre||'?').trim()[0]}</span><div><b>{p.usuario?'@'+p.usuario.replace(/^@/,''):'@usuario'}</b><span>{p.nombre}</span></div></div>
   <p className="ct-bio">{p.bio||<i>Sin bio todavía</i>}</p>
   {p.enlace&&<p className="ct-link">{p.enlace.replace(/^https?:\/\//,'')}</p>}
   {grid.length>0&&<div className="ct-grid">{grid.map(i=>{const c=i.portadas[0];return <div key={i.id} className="ct-tile" title={i.titulo}>{c?<img src={media(c)} alt=""/>:<video src={media(i.videos[0].path)+'#t=1'} muted preload="metadata"/>}{i.estado!=='publicado'&&<em>{i.fecha?new Date(i.fecha).toLocaleDateString('es-ES',{day:'numeric',month:'short'}).replace('.',''):'sin fecha'}</em>}</div>;})}</div>}
  </div>
  <div className="ob-group"><h4>Editar</h4><div className="ob-group-box ct-form">
   {field('nombre','Nombre',64)}{field('usuario','Usuario',40)}{field('bio','Bio',p.red==='x'?160:150,true)}{field('enlace','Enlace',300)}
   {p.brand!=='beta'&&<p className="ob-note">{enlaceInvalido?'Este enlace no genera enlace medido: escríbelo completo y con https://, por ejemplo https://tu-web.com':'Si pones un enlace https, cada publicación de esta cuenta tendrá su enlace medido.'}</p>}
   <div className="ct-when"><button className="ob-primary" disabled={!changed||busy} onClick={save}>{busy?'Guardando…':'Guardar'}</button></div>
  </div></div>
  {msg&&<p className="ob-note ct-msg" role="status">{msg}</p>}
  {ofrecerLanzado&&<><p className="ob-note">¿Ya lo has puesto también en {CANAL[p.red]?.name||p.red}? Apúntalo como lanzado.</p>
   <div className="ct-when"><button className="ob-primary" disabled={lanzando} onClick={lanzado}>Ya lo he lanzado</button></div></>}
  {lanzar.length>0&&<><div className="ct-when"><button className="ob-link" type="button" disabled={lanzando} aria-expanded={descartando} onClick={()=>setDescartando(v=>!v)}>No lo voy a lanzar</button></div>
   {descartando&&<><p className="ob-note">¿Qué te frena?</p>
    <div className="ob-actions">{MOTIVOS_DESCARTE.map(m=><button key={m} className="ob-link" type="button" disabled={lanzando} aria-pressed={motivo===m} onClick={()=>descartar(m)}>{m}</button>)}</div></>}</>}
  {perfil.nota&&<p className="ob-note ct-source">{perfil.nota} Aquí es una vista previa: en {CANAL[p.red]?.name} lo cambias tú.</p>}
 </article>;
}

// ---------- Hoy: the day of the Plan de contenido único ----------
// Per account with Trial Reels: the Trial Reels of 10:30 and 17:30 (one video, 3 openings each) and the winner, the concept to squeeze
// with variants (Owner 29/10: a winner is never posted as a normal reel the next day).
type Apertura={h:string;video:string;texto:string;vistas:number|null};
type Franja={name:string;brand:string;hora:string;titulo:string;estado:'listo'|'haciendose'|'en-cola'|'sin-video'|'subido';aperturas:Apertura[];subido:string|null};
type Cuenta={brand:string;marca:string;franjas:Franja[];bueno:Item|null;ganador:{name:string;h:string;titulo:string;vistas:number|null}|null};
type Dia={fecha:string;cuentas:Cuenta[]};
const ESTADO:Record<Franja['estado'],{label:string;tone:string}>={listo:{label:'Listo para subir',tone:'#ffb35c'},subido:{label:'Subido',tone:'#5fd38d'},haciendose:{label:'Haciéndose esta noche',tone:'#7cb6ff'},'en-cola':{label:'En cola',tone:'#a3a7b0'},'sin-video':{label:'Sin vídeo',tone:'#ff7a70'}};
const dayKey=(offset:number)=>isoDay(new Date(Date.now()+offset*86400000));
function best(c:Cuenta){let b:{f:Franja;a:Apertura}|null=null;for(const f of c.franjas)for(const a of f.aperturas)if(a.vistas!=null&&(!b||a.vistas>(b.a.vistas??-1)))b={f,a};return b;}

function AnalysisCards({analisis,marcas}:{analisis?:Analisis|null;marcas:{id:string;nombre:string}[]}){
 if(!analisis)return null;
 return <div aria-label="Análisis de nuestros reels">{marcas.filter(m=>analisis.marcas[m.id]).map(m=>{
  const a=analisis.marcas[m.id];
  const ganchos=a.ganchos_que_ganan??[],conceptos=a.conceptos_que_ganan??[],repetir=a.repetir??[],evitar=a.evitar??[],perdedores=a.perdedores??[];
  return <section key={m.id} className="ob-group" aria-label={'Qué dicen los datos · '+m.nombre}>
   <h4>{m.nombre} · Qué dicen los datos · <time dateTime={analisis.fecha}>{analisis.fecha.slice(0,10)}</time></h4>
   <div className="ob-group-box" style={{padding:14,overflowWrap:'anywhere'}}>
    <p className="ob-note-block">{a.resumen.split(/(?<=[.!?])\s/)[0]}</p>
    {a.conclusiones.length>0&&<ul className="ob-note">{a.conclusiones.slice(0,3).map((c,i)=><li key={i}>{c}</li>)}</ul>}
    {a.exprimir?.concepto&&<div className="ob-group">
     <strong>Exprimir con variantes: {a.exprimir.concepto}</strong>
     <ul className="ob-note">{a.exprimir.variantes.map((v,i)=><li key={i}><strong>{v.gancho}</strong> · {v.angulo}</li>)}</ul>
    </div>}
    {a.validados.length>0&&<div className="ob-group"><h4>Conceptos validados</h4>
     <ul className="ob-note">{a.validados.map((v,i)=><li key={i}>{v}</li>)}</ul>
     <p className="ob-note">Listo para subir como normal: lo decides tú</p>
    </div>}
    {a.ganadores.some(g=>g.url)&&<div className="ob-group"><h4>Ganadores</h4>
     {a.ganadores.filter(g=>g.url).map((g,i)=><p key={i} className="ob-note"><a className="tr-link" href={g.url} target="_blank" rel="noreferrer"><ExternalLink size={13}/>{g.titulo}</a> · {CANAL[g.red]?.name||g.red} · {g.vistas.toLocaleString('es-ES')} vistas · {g.x_mediana.toLocaleString('es-ES')}× la mediana</p>)}
    </div>}
    {(ganchos.length||conceptos.length||repetir.length||evitar.length||perdedores.length)>0&&<details className="ob-group">
     <summary>Ganchos, conceptos y qué evitar</summary>
     {ganchos.length>0&&<><h4>Ganchos que ganan</h4><ul className="ob-note">{ganchos.map((v,i)=><li key={i}>{v}</li>)}</ul></>}
     {conceptos.length>0&&<><h4>Conceptos que ganan</h4><ul className="ob-note">{conceptos.map((v,i)=><li key={i}>{v}</li>)}</ul></>}
     {repetir.length>0&&<><h4>Repetir</h4><ul className="ob-note">{repetir.map((v,i)=><li key={i}>{v}</li>)}</ul></>}
     {evitar.length>0&&<><h4>Evitar</h4><ul className="ob-note">{evitar.map((v,i)=><li key={i}>{v}</li>)}</ul></>}
     {perdedores.length>0&&<><h4>Los que menos funcionaron</h4><ul className="ob-note">{perdedores.map((p,i)=><li key={i}>{p.url?<a className="tr-link" href={p.url} target="_blank" rel="noreferrer"><ExternalLink size={13}/>{p.titulo}</a>:p.titulo} · {CANAL[p.red]?.name||p.red} · {p.vistas.toLocaleString('es-ES')} vistas · {p.x_mediana.toLocaleString('es-ES')}× la mediana</li>)}</ul></>}
    </details>}
   </div>
  </section>;
 })}</div>;
}

function DayView({arriba,analisis,analisisFecha,marcas,sinEnlace}:{arriba:ReactNode;analisis?:Analisis|null;analisisFecha?:string|null;marcas:{id:string;nombre:string}[];sinEnlace:string}){
 const [offset,setOffset]=useState(0),[data,setData]=useState<Dia|null>(null),[err,setErr]=useState(''),[sel,setSel]=useState<string|null>(null),[open,setOpen]=useState(false),[tick,setTick]=useState(0);
 const fecha=dayKey(offset);
 useEffect(()=>{let gone=false;setErr('');
  fetch('/api/contenido/dia?fecha='+fecha,{cache:'no-store'}).then(async r=>{const j:any=await r.json();if(gone)return;if(!r.ok)throw new Error(j.error);setData(j);}).catch(e=>{if(!gone)setErr(e instanceof Error?e.message:'No se ha podido leer el día.');});
  return()=>{gone=true;};},[fecha,tick]);
 const reload=()=>setTick(t=>t+1);
 const rows=(data?.cuentas||[]).flatMap(c=>[...c.franjas.map(f=>({id:f.name,c,f})),{id:'g-'+c.brand,c,f:null as Franja|null}]);
 useEffect(()=>{if(rows.length&&!rows.some(r=>r.id===sel)&&window.innerWidth>1180)setSel(rows[0].id);},[data]);// eslint-disable-line
 const cur=rows.find(r=>r.id===sel);
 const pick=(id:string)=>{setSel(id);setOpen(true);};
 const days:[number,string][]=[[-1,'Ayer'],[0,'Hoy'],[1,'Mañana']];
 const todo=(data?.cuentas||[]).reduce((n,c)=>n+c.franjas.filter(f=>f.estado==='listo').length,0);
 const aviso=avisoAnalisis(!!analisis,analisisFecha,new Date());
 return <section className="j-workspace ob ct">
  <aside className="ob-side" aria-label="Contenido"><div className="ob-side-scroll">{arriba}
   <h3>Día</h3><div className="ob-side-group">{days.map(([o,l])=><button key={o} className={'ob-nav'+(o===offset?' on':'')} aria-current={o===offset?'true':undefined} onClick={()=>{setOffset(o);setSel(null);setOpen(false);}}><span className="ob-ico"><CalendarDays size={15}/></span><span>{l}</span></button>)}</div>
  </div></aside>
  <div className="ob-main">
   <header className="ob-list-head"><div className="ob-title"><h1>{days.find(d=>d[0]===offset)?.[1]}</h1><span>{dayName(fecha)} · 7 vídeos por cuenta{todo?` · ${todo} ${todo===1?'franja lista':'franjas listas'} para subir`:''}</span></div></header>
   <div className="ob-list">
    <AnalysisCards analisis={analisis} marcas={marcas}/>
    {aviso&&<section className="ob-group" aria-label="Análisis de nuestros reels"><div className="ob-group-box" style={{padding:14}}><p className="ob-note">{aviso}</p></div></section>}
    {sinEnlace&&<section className="ob-group" aria-label="Enlace medido"><div className="ob-group-box" style={{padding:14}}><p className="ob-note">Falta {sinEnlace}.</p></div></section>}
    <div role="listbox" aria-label="Vídeos del día">
    {(data?.cuentas||[]).map(c=><section key={c.brand} className="ob-sec"><h2>{c.marca}<span>{c.franjas.reduce((n,f)=>n+f.aperturas.length,0)} vídeos</span></h2>
     {c.franjas.map(f=><button key={f.name} role="option" aria-selected={sel===f.name} className={'ob-row'+(sel===f.name?' sel':'')} onClick={()=>pick(f.name)}>
      <span className="tr-plat">TR</span>
      <span className="ob-row-text"><strong>{f.titulo}</strong><small>Trial Reels · {f.aperturas.length||3} aperturas · <span className="ct-state" style={{'--t':ESTADO[f.estado].tone} as any}>{ESTADO[f.estado].label}</span></small></span>
      <span className="ob-row-meta"><time>{f.hora}</time></span>
     </button>)}
     {c.bueno&&<div className="ob-row ct-static"><span className="tr-plat">IG</span><span className="ob-row-text"><strong>{c.bueno.titulo}</strong><small>Reel bueno · <span className="ct-state" style={{'--t':TONE[c.bueno.estado]} as any}>{c.bueno.detalle}</span></small></span><span className="ob-row-meta"><time>20:00</time></span></div>}
     <button role="option" aria-selected={sel==='g-'+c.brand} className={'ob-row'+(sel==='g-'+c.brand?' sel':'')} onClick={()=>pick('g-'+c.brand)}>
      <span className="ob-ico"><Trophy size={15}/></span>
      <span className="ob-row-text"><strong>{c.ganador?`Ganador: ${c.ganador.titulo}`:'Elegir el ganador'}</strong><small>{c.ganador?`Apertura ${c.ganador.h.slice(1)}${c.ganador.vistas!=null?` · ${c.ganador.vistas} visualizaciones`:''} · concepto para hacer variantes`:'Apunta las visualizaciones de los Trial Reels y elige el ganador del día'}</small></span>
     </button>
    </section>)}
    {data&&!rows.length&&<div className="ob-blank"><p>No hay vídeos para este día todavía.</p></div>}
    {err&&<div className="ob-blank"><p>{err}</p></div>}
    {!data&&!err&&<div className="ob-blank"><p>Leyendo el día…</p></div>}
    </div>
   </div>
  </div>
  <div className={'ob-inspector'+(open&&cur?' open':'')}>
   {cur?.f?<FranjaDetail key={fecha+cur.f.name} fecha={fecha} f={cur.f} marca={cur.c.marca} onBack={()=>setOpen(false)} onChange={reload}/>
   :cur?<WinnerDetail key={fecha+cur.c.brand} fecha={fecha} c={cur.c} onBack={()=>setOpen(false)} onChange={reload}/>
   :<div className="ob-blank"><p>Elige algo de la lista.</p></div>}
  </div>
 </section>;
}

function FranjaDetail({fecha,f,marca,onBack,onChange}:{fecha:string;f:Franja;marca:string;onBack:()=>void;onChange:()=>void}){
 const [busy,setBusy]=useState(false),[msg,setMsg]=useState(''),[copied,setCopied]=useState('');
 const toggle=async()=>{setBusy(true);setMsg('');try{await post({accion:'subido',fecha,name:f.name,subido:!f.subido});onChange();}catch(e){setMsg(e instanceof Error?e.message:String(e));}finally{setBusy(false);}};
 const copy=async(a:Apertura)=>{try{await navigator.clipboard.writeText(a.texto);setCopied(a.h);setTimeout(()=>setCopied(''),1500);}catch{}};
 return <article className="ob-insp">
  <button className="ob-back" onClick={onBack}><ChevronLeft size={16}/>Hoy</button>
  <p className="ob-insp-eyebrow">{marca} · Trial Reels de las {f.hora}</p>
  <h2>{f.titulo}</h2>
  <div className="ob-pills"><span className="ob-pill status plain" style={{'--t':ESTADO[f.estado].tone} as any}>{ESTADO[f.estado].label}</span></div>
  {f.aperturas.length?<div className="ct-trio">{f.aperturas.map(a=><div key={a.h} className="ct-trio-item">
    <div className="ct-phone ct-phone-sm"><video src={media(a.video)} controls playsInline preload="metadata"/></div>
    <div className="ct-trio-foot"><b>Apertura {a.h.slice(1)}</b><button className="ct-ghost" onClick={()=>copy(a)}>{copied===a.h?<><Check size={12}/>Copiado</>:<><Copy size={12}/>Texto</>}</button></div>
   </div>)}</div>
  :<p className="ob-note ct-msg">{f.estado==='haciendose'?'Se está haciendo ahora. Aparece aquí en cuanto termine.':'Todavía no hay vídeo: está en la cola de esta noche.'}</p>}
  {f.aperturas.length>0&&<div className="ob-group"><h4>¿Subidos?</h4><div className="ob-group-box ct-when">
   <p className="ob-note">{f.subido?`Subidos los 3 como Trial Reels.`:'Súbelos como Trial Reels en Instagram (y a las demás redes) y márcalo aquí.'}</p>
   <button className={f.subido?'ct-ghost':'ob-primary'} disabled={busy} onClick={toggle}>{f.subido?'Marcar como no subidos':<><Check size={14}/>Ya los he subido</>}</button>
  </div></div>}
  {msg&&<p className="ob-note ct-msg" role="status">{msg}</p>}
  {f.aperturas[0]?.texto&&<div className="ob-group"><h4>Texto (apertura 1)</h4><div className="ob-group-box"><p className="tr-text">{f.aperturas[0].texto}</p></div></div>}
 </article>;
}

function WinnerDetail({fecha,c,onBack,onChange}:{fecha:string;c:Cuenta;onBack:()=>void;onChange:()=>void}){
 const [busy,setBusy]=useState(''),[msg,setMsg]=useState('');
 const save=async(clave:string,v:string)=>{try{await post({accion:'vistas',fecha,clave,vistas:v.trim()===''?null:Number(v)});onChange();}catch(e){setMsg(e instanceof Error?e.message:String(e));}};
 const b=best(c);
 const choose=async(f:Franja,a:Apertura)=>{setBusy(f.name+a.h);setMsg('');try{await post({accion:'ganador',fecha,brand:c.brand,name:f.name,h:a.h});setMsg('Elegido. Queda apuntado para hacer variantes de este concepto en los próximos Trial Reels.');onChange();}catch(e){setMsg(e instanceof Error?e.message:String(e));}finally{setBusy('');}};
 const openings=c.franjas.flatMap(f=>f.aperturas.map(a=>({f,a})));
 return <article className="ob-insp">
  <button className="ob-back" onClick={onBack}><ChevronLeft size={16}/>Hoy</button>
  <p className="ob-insp-eyebrow">{c.marca} · ganador del día</p>
  <h2>{c.ganador?c.ganador.titulo:b?`Propuesto: ${b.f.titulo}`:'Apunta las visualizaciones'}</h2>
  <p className="ob-note ct-msg">Gana la apertura con más visualizaciones a unas 24 horas; si empatan, la que tenga más compartidos y guardados. Es el concepto que toca exprimir con variantes en los siguientes Trial Reels.</p>
  {openings.length?<div className="ob-group"><h4>Visualizaciones de cada apertura</h4><div className="ob-group-box">
   {openings.map(({f,a})=>{const key=f.name+'-'+a.h,isBest=b?.f.name===f.name&&b?.a.h===a.h,isWin=c.ganador?.name===f.name&&c.ganador?.h===a.h;
    return <div key={key} className="ct-vrow">
     <span className="ct-vname"><b>{f.hora} · apertura {a.h.slice(1)}</b><small>{f.titulo}</small></span>
     <input id={'v-'+fecha+key} type="number" inputMode="numeric" min={0} defaultValue={a.vistas??''} placeholder="Visualizaciones" aria-label={`Visualizaciones de las ${f.hora}, apertura ${a.h.slice(1)}`} onBlur={e=>{if(String(a.vistas??'')!==e.target.value)save(key,e.target.value);}}/>
     {isWin?<span className="ob-pill status plain" style={{'--t':'#5fd38d'} as any}>Ganador</span>
      :<button className={isBest?'ob-primary':'ct-ghost'} disabled={!!busy} onClick={()=>choose(f,a)}>{busy===f.name+a.h?'Guardando…':isBest?'Elegir (más visualizaciones)':'Elegir'}</button>}
    </div>;})}
  </div></div>:<p className="ob-note ct-msg">Este día no tiene Trial Reels todavía.</p>}
  {msg&&<p className="ob-note ct-msg" role="status">{msg}</p>}
 </article>;
}

// ---------- the week, as Apple Calendar draws it ----------
// One column per day (Monday first), hours down the side, each post a block in its account's colour at its time. Trial Reels
// slots carry their openings and state; a missing one is outlined in red. Narrow windows show one day. The current hour is a red line.
const ACCOUNT_TONES=['#d5b77b','#7cb6ff','#9d7bff','#5fd38d','#ff9a4d'];
const START_H=8,END_H=24,ROW=72;   // visible hours and px per hour
const mondayOf=(d:Date)=>{const x=new Date(d);x.setHours(0,0,0,0);x.setDate(x.getDate()-((x.getDay()+6)%7));return x;};
type Placed={i:Item;top:number;h:number;col:number;cols:number};
type More={day:string;top:number;n:number};
const MAX_LANES=3;
function place(list:Item[]):Placed[]{
 // Events overlapping in time share the width of the column, side by side.
 const ev=list.map(i=>{const d=new Date(i.fecha!);const m=(d.getHours()-START_H)*60+d.getMinutes();return {i,start:m,end:m+50};}).sort((a,b)=>a.start-b.start);
 const out:Placed[]=[];let group:typeof ev=[],groupEnd=-1;
 const flush=()=>{const lanes:number[]=[];const tmp:{e:typeof ev[0];col:number}[]=[];
  for(const e of group){let c=lanes.findIndex(end=>end<=e.start);if(c<0){c=lanes.length;lanes.push(0);}lanes[c]=e.end;tmp.push({e,col:c});}
  for(const t of tmp)out.push({i:t.e.i,top:Math.max(0,t.e.start)*ROW/60,h:50*ROW/60-3,col:t.col,cols:lanes.length});group=[];};
 for(const e of ev){if(group.length&&e.start>=groupEnd)flush();group.push(e);groupEnd=Math.max(groupEnd,e.end);}
 if(group.length)flush();
 return out;
}
function WeekCalendar({items,marcas,selected,onPick,loading,error}:{items:Item[];marcas:{id:string;nombre:string}[];selected:string|null;onPick:(id:string)=>void;loading:boolean;error:string}){
 const [week,setWeek]=useState(0),[narrow,setNarrow]=useState(false),[day,setDay]=useState(0),[now,setNow]=useState(Date.now()),[focus,setFocus]=useState<string|null>(null);
 useEffect(()=>{const f=()=>setNarrow(window.innerWidth<760);f();window.addEventListener('resize',f);const t=setInterval(()=>setNow(Date.now()),60000);return()=>{window.removeEventListener('resize',f);clearInterval(t);};},[]);
 const tone=(b:string)=>ACCOUNT_TONES[Math.max(0,marcas.findIndex(m=>m.id===b))%ACCOUNT_TONES.length];
 const monday=mondayOf(new Date(Date.now()+week*7*86400000));
 const todayKey=isoDay(new Date(now));
 const allDays=[...Array(7)].map((_,n)=>{const d=new Date(monday);d.setDate(d.getDate()+n);return d;});
 const narrowDay=new Date(new Date(now).setHours(0,0,0,0)+day*86400000);
 const days=focus?[new Date(focus+'T12:00:00')]:narrow?[narrowDay]:allDays;
 const single=days.length===1;
 const byDay=new Map<string,Item[]>();
 for(const i of items){if(!i.fecha)continue;const k=isoDay(new Date(i.fecha));if(!byDay.has(k))byDay.set(k,[]);byDay.get(k)!.push(i);}
 const title=(narrow?narrowDay:monday).toLocaleDateString('es-ES',{month:'long',year:'numeric'});
 const nowTop=((new Date(now).getHours()-START_H)*60+new Date(now).getMinutes())*ROW/60;
 const move=(n:number)=>focus?setFocus(isoDay(new Date(new Date(focus+'T12:00:00').getTime()+n*86400000))):narrow?setDay(d=>d+n):setWeek(w=>w+n);
 const atToday=narrow?day===0:week===0;
 return <div className="ct-cal">
  <header className="ct-cal-head">
   <h1>{title[0].toUpperCase()+title.slice(1)}</h1>
   <div className="ct-cal-legend">{marcas.map(m=><span key={m.id}><i style={{background:tone(m.id)}}/>{m.nombre}</span>)}</div>
   <div className="ct-cal-nav">
    <button onClick={()=>move(-1)} aria-label={narrow?'Día anterior':'Semana anterior'}>‹</button>
    {focus&&<button onClick={()=>setFocus(null)}>Semana</button>}
    <button className={atToday&&!focus?'on':''} onClick={()=>{setWeek(0);setDay(0);setFocus(null);}}>Hoy</button>
    <button onClick={()=>move(1)} aria-label={narrow?'Día siguiente':'Semana siguiente'}>›</button>
   </div>
  </header>
  <div className="ct-cal-days" style={{gridTemplateColumns:`52px repeat(${days.length},minmax(0,1fr))`}}>
   <span/>
   {days.map(d=>{const k=isoDay(d);return <div key={k} className={'ct-cal-day'+(k===todayKey?' today':'')}>
    <small>{d.toLocaleDateString('es-ES',{weekday:'short'}).replace('.','')}</small><b>{d.getDate()}</b></div>;})}
  </div>
  <div className="ct-cal-scroll">
   <div className="ct-cal-grid" style={{gridTemplateColumns:`52px repeat(${days.length},minmax(0,1fr))`,height:(END_H-START_H)*ROW}}>
    <div className="ct-cal-hours">{[...Array(END_H-START_H)].map((_,h)=><span key={h} style={{top:h*ROW}}>{h?String(START_H+h).padStart(2,'0')+':00':''}</span>)}</div>
    {days.map(d=>{const k=isoDay(d);const list=(byDay.get(k)||[]).filter(i=>new Date(i.fecha!).getHours()>=START_H);
     return <div key={k} className={'ct-cal-col'+(k===todayKey?' today':'')}>
      {[...Array(END_H-START_H)].map((_,h)=><i key={h} className="ct-cal-line" style={{top:h*ROW}}/>)}
      {(()=>{const placed=place(list);const shown=single?placed:placed.filter(p=>p.col<MAX_LANES-1||p.cols<=MAX_LANES);
       const hidden=single?[]:placed.filter(p=>p.cols>MAX_LANES&&p.col>=MAX_LANES-1);
       const more=new Map<number,number>();for(const p of hidden)more.set(p.top,(more.get(p.top)||0)+1);
       return <>{[...more].map(([top,n])=><button key={'m'+top} className="ct-ev ct-more" style={{top,height:50*ROW/60-3,left:`calc(${(MAX_LANES-1)*100/MAX_LANES}% + 2px)`,width:`calc(${100/MAX_LANES}% - 4px)`}} onClick={()=>setFocus(k)} title="Ver el día">+{n}</button>)}
       {shown.map(({i,top,h,col,cols})=>{const t=tone(i.brand);const c=single?cols:Math.min(cols,MAX_LANES);
       return <button key={i.id} className={'ct-ev'+(i.id===selected?' sel':'')+(i.estado==='publicado'?' done':'')+(i.estado==='falta'||i.estado==='fallo'?' miss':'')}
        style={{top,height:h,left:`calc(${col*100/c}% + 2px)`,width:`calc(${100/c}% - 4px)`,'--c':t} as any} onClick={()=>onPick(i.id)}
        title={`${hm(i.fecha!)} · ${i.marca} · ${i.titulo} · ${i.detalle}`}>
        <span className="ct-ev-time">{hm(i.fecha!)} · {CANAL[i.canal]?.short||i.canal}{i.videos.length?` · ${i.videos.length} vídeos`:''}</span>
        <strong>{i.titulo}</strong>
        <span className="ct-ev-meta">{i.tipo} · {i.detalle}</span>
       </button>;})}</>;})()}
      {k===todayKey&&nowTop>0&&nowTop<(END_H-START_H)*ROW&&<i className="ct-cal-now" style={{top:nowTop}}/>}
     </div>;})}
   </div>
  </div>
  {loading&&<div className="ob-blank"><p>Leyendo el calendario…</p></div>}
  {error&&<div className="ob-blank"><p>{error}</p></div>}
 </div>;
}
