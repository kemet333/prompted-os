'use client';
// Piezas del chat de Alfred (11/11/2026, petición de Owner: «mejora la interfaz del chat»).
// - Rich: Markdown mínimo y seguro (párrafos, listas, **negrita**, `código`, enlaces http) sin HTML crudo.
// - DayBreak: separador «Hoy», «Ayer» o la fecha cuando cambia el día entre dos mensajes.
// - CopyButton: copia la respuesta con aviso breve.
// - useStick: sigue el final de la conversación salvo que Owner haya subido a leer; entonces ofrece «Ir al final».
import {Fragment,useCallback,useEffect,useState,type ReactNode,type RefObject} from 'react';
import {Copy,Check} from 'lucide-react';

function inline(text:string,key:string):ReactNode[]{
 const out:ReactNode[]=[];const re=/(\*\*[^*]+\*\*|`[^`]+`|https?:\/\/[^\s)]+)/g;let last=0,m:RegExpExecArray|null,i=0;
 while((m=re.exec(text))){
  if(m.index>last)out.push(text.slice(last,m.index));
  const t=m[0],k=key+'-'+i++;
  if(t.startsWith('**'))out.push(<strong key={k}>{t.slice(2,-2)}</strong>);
  else if(t.startsWith('`'))out.push(<code key={k}>{t.slice(1,-1)}</code>);
  else out.push(<a key={k} href={t} target="_blank" rel="noreferrer">{t.replace(/^https?:\/\//,'').replace(/\/$/,'')}</a>);
  last=m.index+t.length;
 }
 if(last<text.length)out.push(text.slice(last));
 return out;
}

/** Bloques: líneas seguidas forman un párrafo; «- », «• » o «1. » forman listas; una línea vacía separa. */
export function Rich({text}:{text:string}){
 const blocks:{kind:'p'|'ul'|'ol';lines:string[]}[]=[];
 for(const raw of String(text||'').split('\n')){
  const line=raw.trimEnd(),ul=line.match(/^\s*[-•*]\s+(.*)$/),ol=line.match(/^\s*\d+[.)]\s+(.*)$/),last=blocks[blocks.length-1];
  if(!line.trim()){blocks.push({kind:'p',lines:[]});continue;}
  if(ul){if(last?.kind==='ul')last.lines.push(ul[1]);else blocks.push({kind:'ul',lines:[ul[1]]});continue;}
  if(ol){if(last?.kind==='ol')last.lines.push(ol[1]);else blocks.push({kind:'ol',lines:[ol[1]]});continue;}
  if(last?.kind==='p'&&last.lines.length)last.lines.push(line);else blocks.push({kind:'p',lines:[line]});
 }
 return <div className="j-rich">{blocks.filter(b=>b.lines.length).map((b,i)=>b.kind==='p'
  ?<p key={i}>{b.lines.map((l,j)=><Fragment key={j}>{j>0&&<br/>}{inline(l,i+'-'+j)}</Fragment>)}</p>
  :b.kind==='ul'?<ul key={i}>{b.lines.map((l,j)=><li key={j}>{inline(l,i+'-'+j)}</li>)}</ul>
  :<ol key={i}>{b.lines.map((l,j)=><li key={j}>{inline(l,i+'-'+j)}</li>)}</ol>)}</div>;
}

const dayKey=(t:number)=>new Date(t).toLocaleDateString('sv-SE');
export function dayLabel(t:number,now=Date.now()){
 const d=dayKey(t);if(d===dayKey(now))return 'Hoy';if(d===dayKey(now-864e5))return 'Ayer';
 return new Date(t).toLocaleDateString('es-ES',{weekday:'long',day:'numeric',month:'long'});
}
export function DayBreak({prev,at}:{prev?:number;at:number}){
 if(prev&&dayKey(prev)===dayKey(at))return null;
 return <div className="j-day" role="separator"><span>{dayLabel(at)}</span></div>;
}

export function CopyButton({text}:{text:string}){
 const [done,setDone]=useState(false);
 useEffect(()=>{if(!done)return;const t=setTimeout(()=>setDone(false),1600);return()=>clearTimeout(t);},[done]);
 return <button type="button" className="j-tool" onClick={async()=>{try{await navigator.clipboard.writeText(text);setDone(true);}catch{}}} aria-label={done?'Copiado':'Copiar respuesta'}>
  {done?<Check size={12}/>:<Copy size={12}/>}{done?'Copiado':'Copiar'}</button>;
}

export function useStick(ref:RefObject<HTMLElement|null>,key:string){
 const [away,setAway]=useState(false);
 // `key` cambia al abrir el chat o al llegar texto nuevo: vuelve a enganchar el scroll y baja al final si Owner no estaba leyendo arriba.
 useEffect(()=>{const el=ref.current;if(!el)return;const on=()=>setAway(el.scrollHeight-el.scrollTop-el.clientHeight>160);on();el.addEventListener('scroll',on,{passive:true});return()=>el.removeEventListener('scroll',on);},[ref,key]);
 useEffect(()=>{const el=ref.current;if(el&&!away)el.scrollTo({top:el.scrollHeight,behavior:'smooth'});},[ref,key,away]);
 const toEnd=useCallback(()=>{const el=ref.current;if(el)el.scrollTo({top:el.scrollHeight,behavior:'smooth'});},[ref]);
 return {away,toEnd};
}
