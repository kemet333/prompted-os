import {ANALISIS_DIAS_VIGENTE} from './office-limits';

export function avisoAnalisis(hayAnalisis:boolean,fecha:unknown,ahora:Date):string{
 if(hayAnalisis||typeof fecha!=='string')return '';
 if(!/^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2}))?$/.test(fecha))return '';
 const dia=fecha.slice(0,10),parsed=Date.parse(dia+'T00:00:00Z');
 if(!Number.isFinite(parsed)||new Date(parsed).toISOString().slice(0,10)!==dia)return '';
 const tiempo=Date.parse(fecha),actual=ahora.getTime();
 if(!Number.isFinite(tiempo)||!Number.isFinite(actual)||tiempo>actual)return '';
 const dias=Math.floor((actual-tiempo)/86400_000);
 if(actual-tiempo<=ANALISIS_DIAS_VIGENTE*86400_000)return `Último análisis: ${dia.slice(8,10)}/${dia.slice(5,7)}. Ha llegado, pero no se ha podido leer.`;
 return `Último análisis: ${dia.slice(8,10)}/${dia.slice(5,7)} (hace ${dias} días). No ha llegado uno nuevo.`;
}
