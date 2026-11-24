/** Positions are shared by the DOM nodes and their SVG connections. Narrow canvases use normal flow. */
export function projectTreeLayout(width:number,branches:number,leaves:number){
 if(!Number.isFinite(width)||width<720)return null;
 const height=Math.max(440,branches*82+64,leaves*72+64);
 const root={x:Math.max(72,width*.075),y:height/2};
 const branchX=Math.max(184,width*.25),branchWidth=width<900?174:202;
 const start=branchX+branchWidth+68,depth=Math.max(0,Math.min(140,width-236-start));
 const rows=Array.from({length:branches},(_,i)=>({x:branchX,y:height/2+(i-(branches-1)/2)*82,width:branchWidth}));
 const nodes=Array.from({length:leaves},(_,i)=>{const y=height/2+(i-(leaves-1)/2)*72,half=Math.max(1,leaves*72/2),arc=Math.sqrt(Math.max(0,1-((y-height/2)/(half*1.15))**2));const x=start+depth*arc;return{x,y,width:Math.min(290,width-x-16)};});
 return{width,height,root,branches:rows,leaves:nodes};
}
export const treeCurve=(x1:number,y1:number,x2:number,y2:number)=>{const bend=(x2-x1)*.53;return `M${x1} ${y1}C${x1+bend} ${y1} ${x2-bend} ${y2} ${x2} ${y2}`;};
