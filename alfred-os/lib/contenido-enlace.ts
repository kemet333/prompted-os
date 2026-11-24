export function baseEnlaceValida(base:string):boolean{
 try{return new URL(base.trim()).protocol==='https:';}catch{return false;}
}

export function avisoEnlacePerfil(brand:string,enlace:string):''|'vacio'|'invalido'{
 if(brand==='beta'||baseEnlaceValida(enlace))return '';
 return enlace.trim()===''?'vacio':'invalido';
}

export function primerPerfilSinEnlace(perfiles:{brand:string;red:string;enlace:string}[]):string{
 const p=perfiles.find(p=>avisoEnlacePerfil(p.brand,p.enlace)!=='');
 return p?p.brand+p.red:'';
}

export function ofreceLanzado(lanzar:string[],brand:string,enlace:string):boolean{
 return lanzar.length>0&&enlace.trim()!==''&&avisoEnlacePerfil(brand,enlace)==='';
}

export function perfilParaLanzar(perfiles:{brand:string;red:string;enlace:string}[]):string{
 const sin=primerPerfilSinEnlace(perfiles);
 if(sin!=='')return sin;
 const p=perfiles.find(p=>p.brand!=='beta'&&baseEnlaceValida(p.enlace));
 return p?p.brand+p.red:'';
}

export function etiquetaEnlace(enlace:string):string{
 try{
  const url=new URL(enlace);
  return url.protocol==='https:'?url.searchParams.get('utm_content')||'':'';
 }catch{return '';}
}

export function sinEnlaceMedido(items:{brand:string;marca:string;canal:string;estado:string;fecha:string|null;enlace:string}[],ahora:number,dias=7):{marca:string;canal:string;n:number}[]{
 const grupos=new Map<string,{marca:string;canal:string;n:number}>();
 for(const it of items){
  if(it.brand==='beta'||it.estado==='publicado'||it.fecha===null||it.enlace!=='')continue;
  const t=Date.parse(it.fecha);
  if(!Number.isFinite(t)||!(ahora<=t&&t<ahora+dias*86400000))continue;
  const key=JSON.stringify([it.marca,it.canal]);
  const grupo=grupos.get(key)||{marca:it.marca,canal:it.canal,n:0};
  grupo.n++;grupos.set(key,grupo);
 }
 return [...grupos.values()].sort((a,b)=>b.n-a.n||a.marca.localeCompare(b.marca)||a.canal.localeCompare(b.canal));
}

export function sinEnlaceHaceDias(historial:unknown,hoy:string):{dias:number;n:number}|null{
 if(!historial||typeof historial!=='object'||Array.isArray(historial))return null;
 const fecha=Date.parse(hoy+'T00:00:00Z');if(!Number.isFinite(fecha))return null;
 for(const dias of [7,8,6]){
  const entrada=(historial as Record<string,unknown>)[new Date(fecha-dias*86400000).toISOString().slice(0,10)];
  if(entrada&&typeof entrada==='object'&&!Array.isArray(entrada)&&'total' in entrada&&typeof entrada.total==='number'&&Number.isInteger(entrada.total)&&entrada.total>=0)return {dias,n:entrada.total};
 }
 return null;
}

export function avisoSinEnlace(items:{brand:string;marca:string;canal:string;estado:string;fecha:string|null;enlace:string}[],ahora:number,previo?:{dias:number;n:number}|null):string{
 const grupos=sinEnlaceMedido(items,ahora);if(!grupos.length)return '';
 const redes:Record<string,string>={instagram:'Instagram',tiktok:'TikTok',youtube:'YouTube',facebook:'Facebook',x:'X'};
 const perfiles=grupos.slice(0,3).map(g=>`${g.marca} en ${Object.hasOwn(redes,g.canal)?redes[g.canal]:g.canal}`).join(', ');
 const total=grupos.reduce((n,g)=>n+g.n,0),resto=grupos.length-3;
 const urgentes=sinEnlaceMedido(items,ahora,2).reduce((n,g)=>n+g.n,0);
 const detalle=[...(urgentes>0?[`${urgentes} en las próximas 48 h`]:[]),...(previo?[`hace ${previo.dias} días: ${previo.n}`]:[])];
 return `poner un enlace https en el perfil de ${perfiles}${resto>0?' y '+resto+' más':''}: ${total} publicaci${total===1?'ón':'ones'} de esta semana saldría${total===1?'':'n'} sin enlace medido`+(detalle.length?` (${detalle.join('; ')})`:'');
}
