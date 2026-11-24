export function tasa(parte:number,total:number):string{return total===0?'—':(parte/total*100).toLocaleString('es-ES',{maximumFractionDigits:1})+' %';}

export function mejores<T extends {estado:string;medida:{vistas:number;visitas:number;clics:number}|null}>(items:T[],n=3):T[]{
 return items.filter(i=>i.estado==='publicado'&&i.medida!==null&&i.medida.vistas>0).sort((a,b)=>{
  const ma=a.medida!,mb=b.medida!;
  return mb.clics/mb.vistas-ma.clics/ma.vistas||mb.visitas/mb.vistas-ma.visitas/ma.vistas||mb.vistas-ma.vistas;
 }).slice(0,n);
}

export function publicados<T extends {estado:string;fuente:string;marcable:boolean}>(items:T[]):T[]{
 return items.filter(i=>i.estado==='publicado'&&(i.fuente!=='ritmo'||i.marcable)).reverse();
}

export function posicionTasa(parte:number,vistas:number,totalRed:number,vistasRed:number,medidasRed:number):'primera'|'media'|'encima'|'debajo'|''{
 if(medidasRed<2)return 'primera';
 if(vistas===0||vistasRed===0)return '';
 const publicacion=parte/vistas,media=totalRed/vistasRed;
 return publicacion===media?'media':publicacion>media?'encima':'debajo';
}
