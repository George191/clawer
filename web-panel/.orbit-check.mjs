import * as T from 'three';
export function samples(w,h,inc,tilt,scale=.075,N=720) {
 const cam=new T.PerspectiveCamera(42,w/h,.1,1600);cam.position.z=18;cam.updateMatrixWorld();
 const t=Math.tan(21*Math.PI/180),earth=new T.Vector3(-.4*24*t*w/h,-.55*24*t,-6),R=Math.min(w*.38,h*.68)*24*2*t/h,r=Math.max(R*Number(process.argv[3]||1.1),R*1.006+.075*5.1*1.05+.15), mr=scale*5.1*1.05;
 const n=cam.position.clone().sub(earth).normalize(),u=new T.Vector3(1,0,0);u.addScaledVector(n,-n.x).normalize();const v=new T.Vector3().crossVectors(n,u).normalize();
 const x=u.clone().multiplyScalar(Math.cos(inc)).addScaledVector(v,Math.sin(inc)),y=u.clone().multiplyScalar(-Math.sin(inc)).addScaledVector(v,Math.cos(inc)).multiplyScalar(Math.cos(tilt)).addScaledVector(n,Math.sin(tilt)),off=cam.position.clone().sub(earth);
 return Array.from({length:N},(_,k)=>{const a=k/N*2*Math.PI,p=earth.clone().addScaledVector(x,r*Math.cos(a)).addScaledVector(y,r*Math.sin(a)),d=p.clone().sub(cam.position),len=d.length();d.normalize();const b=off.dot(d),disc=b*b-off.lengthSq()+(R*1.006+mr)**2,hit=disc<0?Infinity:-b-Math.sqrt(disc);
 if(hit>0&&hit<len)return false;
 let left=Infinity,right=-Infinity,top=Infinity,bottom=-Infinity;
 for(let i=0;i<8;i++){const q=p.clone().add(new T.Vector3(i&1?mr:-mr,i&2?mr:-mr,i&4?mr:-mr)).project(cam);const px=(q.x+1)*w/2,py=(1-q.y)*h/2;left=Math.min(left,px);right=Math.max(right,px);top=Math.min(top,py);bottom=Math.max(bottom,py);}
 // Approximate desktop form and left copy bounds, then verify against measured DOM bounds in browser.
 const rects=[{x:w*.78-215,y:h/2-270,w:430,h:540},{x:Math.min(112,Math.max(36,w*.07)),y:h*.9-245,w:w*.56-100,h:245}];
 return left>=20&&right<=w-20&&top>=20&&bottom<=h-20;
 });
}
if(process.argv[2]==='scan')for(const [w,h] of [[1920,1080],[1440,900],[1366,768],[1024,768],[768,1024],[390,844]]){
 const configs=[];for(let inc=-3.1;inc<3.2;inc+=.1)for(let tilt=.2;tilt<3;tilt+=.1){const s=samples(w,h,inc,tilt);let run=0,max=0,start=0;for(let k=0;k<1440;k++){run=s[k%720]?run+1:0;if(run>max){max=run;start=k-run+1;}}configs.push({inc,tilt,len:s.filter(Boolean).length,run:Math.min(720,max),start:start%720});}configs.sort((a,b)=>b.len-a.len);console.log(w,h,configs.slice(0,3));
}
