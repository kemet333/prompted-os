import {POR_LANZAR_TOPE} from './office-limits';

export const MOTIVOS_DESCARTE: readonly string[]=[
 'No es un experimento de mercado: es una mejora interna.',
 'No entiendo qué tengo que hacer para lanzarlo.',
 'Ahora no tengo tiempo para lanzarlo.',
 'Ya no me interesa esta línea de trabajo.',
];

export function formularioVigente(abierto:string|null|undefined,actual:string|undefined):boolean{
 return typeof abierto==='string'&&abierto.length>0&&typeof actual==='string'&&actual.length>0&&abierto===actual;
}

// Aviso para Owner cuando la cola de experimentos por lanzar llega al tope y el equipo deja de preparar nuevos.
export function avisoColaLanzar(total:unknown):string{
 if(typeof total!=='number'||!Number.isFinite(total)||total<POR_LANZAR_TOPE)return '';
 return `Esperan ${total} experimentos: mientras haya ${POR_LANZAR_TOPE} o más, el equipo no prepara otros nuevos. Lanza o descarta alguno.`;
}

// Un experimento que se lanza poniendo un enlace en el perfil: solo mira los textos que son string.
export function seLanzaEnPerfiles(textos:unknown[]):boolean{
 const texto=textos.filter((t):t is string=>typeof t==='string').join(' ');
 return /perfil/i.test(texto)&&/enlace/i.test(texto);
}

// Mismo formato de id de tarea que acepta /api/equipo (equipo-acciones.ts).
export const TAREA_ID=/^[A-Za-z0-9][A-Za-z0-9_-]{2,79}$/;

// Las tareas que Entregas deja en sessionStorage para apuntarlas como lanzadas desde Contenido: hasta 10 ids válidos, sin repetir.
export function tareasDeLanzar(raw:unknown):string[]{
 if(typeof raw!=='string')return [];
 try{
  const lista:unknown=JSON.parse(raw);
  if(!Array.isArray(lista))return [];
  return [...new Set(lista.filter((t):t is string=>typeof t==='string'&&TAREA_ID.test(t)))].slice(0,10);
 }catch{return [];}
}

// Un cuerpo de «descartar» por tarea (mismos ids que tareasDeLanzar) con uno de los motivos rápidos; sin motivo válido no hay nada que enviar.
export function descartesDeLanzar(ids:unknown,motivo:unknown):{action:'descartar';tarea:string;texto:string}[]{
 if(!Array.isArray(ids)||typeof motivo!=='string'||!MOTIVOS_DESCARTE.includes(motivo))return [];
 return [...new Set(ids.filter((t):t is string=>typeof t==='string'&&TAREA_ID.test(t)))].slice(0,10).map(tarea=>({action:'descartar' as const,tarea,texto:motivo}));
}

// Las tareas que quedan por apuntar: los ids válidos de tareasDeLanzar (hasta 10, sin repetir) quitando las que ya respondieron bien.
export function pendientesDeLanzar(ids:unknown,hechas:unknown):string[]{
 if(!Array.isArray(ids))return [];
 const hechasSet=new Set<unknown>(Array.isArray(hechas)?hechas:[]);
 return [...new Set(ids.filter((t):t is string=>typeof t==='string'&&TAREA_ID.test(t)))].slice(0,10).filter(t=>!hechasSet.has(t));
}

const enteroNoNegativo=(n:unknown):n is number=>typeof n==='number'&&Number.isFinite(n)&&Number.isInteger(n)&&n>=0;

export function lineaCifrasCalendario(c:unknown):string{
 if(!c||typeof c!=='object')return '';
 const cifras=c as Record<string,unknown>,apuntadas=cifras.apuntadas,publicadas=cifras.publicadas;
 if(!enteroNoNegativo(apuntadas)||!enteroNoNegativo(publicadas)||publicadas===0||apuntadas>publicadas)return '';
 let linea=`Cifras apuntadas del calendario: ${apuntadas} de ${publicadas} publicaciones`;
 const anteriores=[cifras.hace_7,cifras.hace_14].flatMap(hace=>{
  if(!hace||typeof hace!=='object')return [];
  const anterior=hace as Record<string,unknown>,dias=anterior.dias,x=anterior.apuntadas,y=anterior.publicadas;
  return typeof dias==='number'&&Number.isInteger(dias)&&enteroNoNegativo(x)&&enteroNoNegativo(y)&&y>0&&x<=y?[`hace ${dias} días: ${x} de ${y}`]:[];
 });
 if(anteriores.length)linea+=` (${anteriores.join('; ')})`;
 return linea+'.'+(apuntadas<publicadas?' Apunta el resto en Contenido → Por medir o con /medir en Telegram.':'');
}

export function lineaAtribucionCalendario(c:unknown):string{
 if(!c||typeof c!=='object')return '';
 const cifras=c as Record<string,unknown>,clics=cifras.con_clics,compras=cifras.con_compras,total=cifras.con_cifras;
 if(!enteroNoNegativo(clics)||!enteroNoNegativo(compras)||!enteroNoNegativo(total)||total===0||clics>total||compras>total)return '';
 let linea=`Del calendario con cifras: ${clics} de ${total} con clics y ${compras} con compras`;
 const anteriores=[cifras.hace_7,cifras.hace_14].flatMap(hace=>{
  if(!hace||typeof hace!=='object')return [];
  const anterior=hace as Record<string,unknown>,dias=anterior.dias,a=anterior.con_clics,b=anterior.con_compras,c=anterior.con_cifras;
  return typeof dias==='number'&&Number.isInteger(dias)&&enteroNoNegativo(a)&&enteroNoNegativo(b)&&enteroNoNegativo(c)&&c>0&&a<=c&&b<=c?[`hace ${dias} días: ${a} con clics y ${b} con compras de ${c}`]:[];
 });
 if(anteriores.length)linea+=` (${anteriores.join('; ')})`;
 return linea;
}

export function etiquetaSinMedir(titulo:string,indice:number,mostrados:number,total:number):string{
 if(!Number.isFinite(total)||total<mostrados)total=mostrados;
 return mostrados>1?`Sin medir (${indice+1} de ${total}): «${titulo}»`:`Sin medir: «${titulo}»${total>1?` y ${total-1} más`:''}`;
}

// Cuánto lleva esperando un experimento; vacío si la fecha no es válida o es futura.
export function esperaDesde(avisado:unknown,ahora:Date):string{
 if(typeof avisado!=='string')return '';
 const t=Date.parse(avisado);
 if(!Number.isFinite(t))return '';
 const horas=(ahora.getTime()-t)/3600000;
 if(horas<0)return '';
 if(horas<24)return 'desde hoy';
 if(horas<48)return 'desde hace 1 día';
 return `desde hace ${Math.floor(horas/24)} días`;
}
