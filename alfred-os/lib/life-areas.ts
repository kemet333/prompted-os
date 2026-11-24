import type {Project} from './alfred-data';

export const lifeAreas = [
 {id:'business',name:'Business',description:'Empresas, productos y operaciones.'},
 {id:'personal',name:'Personal',description:'Salud, vida y organización personal.'},
 {id:'growth',name:'Growth',description:'Aprendizaje, relaciones y desarrollo.'},
] as const;
export type LifeAreaId=(typeof lifeAreas)[number]['id'];
// Tus unidades de negocio: un proyecto cuya «division» coincide con una de ellas cae ahí. Cámbialas por las tuyas.
export const businessUnits=[
 {id:'trabajo',name:'TRABAJO',description:'Tu empresa, tus clientes y tus operaciones.',featured:[] as string[]},
 {id:'proyectos',name:'PROYECTOS',description:'Productos, ideas y experimentos.',featured:[] as string[]},
] as const;
export const lifeCategories={
 personal:[{id:'health',name:'Salud y fitness',description:'Entrenamiento, salud y retos físicos.'},{id:'nutrition',name:'Nutrición',description:'Alimentación y hábitos.'},{id:'sleep',name:'Sueño',description:'Descanso y recuperación.'},{id:'travel',name:'Viajes',description:'Experiencias y planes fuera de casa.'},{id:'relationships',name:'Vida personal',description:'Relaciones y tiempo para ti.'},{id:'organization',name:'Organización',description:'Tu sistema y tus herramientas personales.'}],
 growth:[{id:'learning',name:'Aprendizaje',description:'Estudios, habilidades y conocimiento.'},{id:'mindset',name:'Mindset',description:'Criterio, reflexión y desarrollo personal.'},{id:'social-skills',name:'Habilidades sociales',description:'Comunicación y relaciones.'},{id:'networking',name:'Networking',description:'Conexiones y colaboraciones.'}],
} as const;
export function isLifeArea(value:unknown):value is LifeAreaId{return lifeAreas.some(a=>a.id===value);}
export function validLifeCategory(area:LifeAreaId,value:string){return area!=='business'&&lifeCategories[area].some(c=>c.id===value);}
const healthIds=new Set(['fitness','health','salud','deporte']);
const learningIds=new Set(['studies','estudios','learning','university','aprender']);
export function placement(p:Project):{area:LifeAreaId;category:string;unit:string}{
 const explicit=isLifeArea(p.lifeArea)?p.lifeArea:null;
 const area:LifeAreaId=explicit||(businessUnits.some(u=>u.id===p.division)?'business':learningIds.has(p.id)||learningIds.has(p.division)?'growth':'personal');
 const category=p.lifeCategory&&validLifeCategory(area,p.lifeCategory)?p.lifeCategory:area==='growth'?'learning':healthIds.has(p.id)?'health':'organization';
 const unit=businessUnits.some(u=>u.id===p.division)?p.division:'unassigned';
 return {area,category,unit};
}
export function projectLabel(p:Project){return p.name;}
export function areaProjects(list:Project[],area:LifeAreaId){return list.filter(p=>placement(p).area===area);}
export function matchesProject(p:Project,query:string){const q=query.trim().toLocaleLowerCase('es');return !q||[p.name,projectLabel(p),p.line].some(s=>s.toLocaleLowerCase('es').includes(q));}
