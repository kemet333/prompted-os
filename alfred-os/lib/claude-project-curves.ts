// Restored from the last active Claude Projects tree (b68bc66, approved15Oct).
// A branch as a filled shape that thins from its source to its leaf (stroke width cannot taper in SVG).
export function taper(x1:number,y1:number,x2:number,y2:number,w1:number,w2:number){
 const dx=(x2-x1)*.55,pts:[number,number,number][]=[];
 for(let i=0;i<=28;i++){const t=i/28,u=1-t;
  const x=u*u*u*x1+3*u*u*t*(x1+dx)+3*u*t*t*(x2-dx)+t*t*t*x2,y=u*u*u*y1+3*u*u*t*y1+3*u*t*t*y2+t*t*t*y2;
  const tx=3*u*u*dx+6*u*t*((x2-dx)-(x1+dx))+3*t*t*dx,ty=6*u*t*(y2-y1),len=Math.hypot(tx,ty)||1;
  pts.push([x+(-ty/len)*(w1+(w2-w1)*t)/2,y+(tx/len)*(w1+(w2-w1)*t)/2,0]);
  pts.push([x-(-ty/len)*(w1+(w2-w1)*t)/2,y-(tx/len)*(w1+(w2-w1)*t)/2,1]);}
 const a=pts.filter(p=>!p[2]),b=pts.filter(p=>p[2]).reverse();
 return 'M'+a.map(p=>p[0].toFixed(1)+' '+p[1].toFixed(1)).join('L')+'L'+b.map(p=>p[0].toFixed(1)+' '+p[1].toFixed(1)).join('L')+'Z';
}
export const spine=(x1:number,y1:number,x2:number,y2:number)=>{const dx=(x2-x1)*.55;return `M${x1} ${y1}C${x1+dx} ${y1} ${x2-dx} ${y2} ${x2} ${y2}`;};
