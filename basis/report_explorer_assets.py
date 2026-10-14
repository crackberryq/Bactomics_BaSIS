"""Self-contained genome explorer assets, with no third-party runtime."""

CSS = r"""
.gx { min-width:0; }
.gx .gx-intro { background:linear-gradient(120deg,#f9fdfc,#fff); }
.gx .gx-controls { display:flex; flex-wrap:wrap; align-items:flex-end; gap:12px; margin:21px 0 15px; }
.gx-controls label,.gx-window-controls label { display:flex; flex-direction:column; gap:5px; color:#516677; font-size:11px; font-weight:650; }
.gx-controls input,.gx-controls select,.gx-window-controls input { border:1px solid #cbd8df; border-radius:7px; min-height:39px; padding:8px 11px; background:#fff; color:#20394e; font-size:12px; max-width:100%; }
.gx-controls select { min-width:135px; }
.gx-controls .gx-search-label { flex:1 1 280px; min-width:0; }
.gx-search-results { padding:10px; border:1px solid #bcd2df; background:#f6fafd; border-radius:9px; max-height:270px; overflow:auto; margin-bottom:15px; }
.gx-search-results > p { padding:4px 8px; font-size:11px; color:#586f7e; }
.gx-search-results button { display:block; width:100%; padding:9px 11px; text-align:left; font-size:12px; border:0; border-radius:6px; background:transparent; color:#294a62; overflow-wrap:anywhere; }
.gx-search-results button:hover,.gx-search-results button:focus { background:#e5f0f5; }
.gx-search-results button small { display:block; color:#607784; margin-top:2px; }
.gx-track-list { display:flex; flex-wrap:wrap; gap:8px; margin:14px 0; }
.gx-track { display:inline-flex; align-items:center; gap:6px; min-height:31px; border:1px solid #d8e2e8; padding:4px 9px; border-radius:6px; color:#43596b; background:#fff; font-size:11px; cursor:pointer; }
.gx-track input { accent-color:#167e7a; margin:0; }
.gx-track i,.gx-legend i { width:8px; height:8px; border-radius:2px; display:inline-block; flex-shrink:0; }
.gx-track.gx-unavailable { border-style:dashed; color:#7b8791; background:#f4f6f7; cursor:default; }
.gx-track small { color:#7a8894; font-size:10px; }
.gx-note { font-size:11px; color:#617685; margin:12px 0; overflow-wrap:anywhere; }
.gx-legend { display:flex; flex-wrap:wrap; gap:10px 20px; color:#64798b; font-size:10px; margin-top:14px; }
.gx-legend span { display:inline-flex; align-items:center; gap:6px; }
.gx-scroll { max-width:100%; overflow-x:auto; border:1px solid #e0e7ec; border-radius:9px; margin:12px 0; background:#fff; }
.gx-svg { display:block; width:100%; min-width:900px; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
.gx-scroll-hint { display:none; color:#607a89; font-size:11px; margin:12px 0; }
.gx-svg [role=button] { cursor:pointer; }
.gx-svg [role=button]:focus { outline:none; stroke:#d89925; stroke-width:3px; }
.gx-svg [role=button]:hover { opacity:.78; }
.gx-window-controls { display:flex; flex-wrap:wrap; align-items:flex-end; gap:9px; padding:17px 0 11px; border-top:1px solid #e1e8ed; margin-top:18px; }
.gx-window-controls > strong { align-self:center; margin-right:auto; font-size:13px; color:#29495f; }
.gx-window-controls input { width:125px; }
.gx-navigation { display:flex; flex-wrap:wrap; gap:5px; }
.gx-navigation button { padding-left:12px; padding-right:12px; }
.gx-export { display:flex; gap:6px; flex-wrap:wrap; }
.gx .gx-gene-detail { border:1px solid #cee0e5; background:linear-gradient(120deg,#f0f8f7,#f6f9fc); border-radius:10px; padding:20px; margin-top:18px; }
.gx-detail-heading { display:flex; justify-content:space-between; align-items:flex-start; gap:15px; flex-wrap:wrap; }
.gx-detail-heading h4 { margin:0 0 6px; font-size:16px; color:#244a5b; }
.gx-detail-heading p { color:#55707c; font-size:12px; overflow-wrap:anywhere; }
.gx-detail-kv { display:flex; gap:12px 27px; flex-wrap:wrap; font-size:11px; color:#537182; margin:15px 0; }
.gx-detail-kv span strong { display:block; font-size:10px; font-weight:650; color:#76909c; text-transform:uppercase; letter-spacing:.6px; margin-bottom:3px; }
.gx-labels { display:flex; flex-wrap:wrap; gap:6px; margin-top:12px; }
.gx-labels span { background:#fff; color:#3d6276; border:1px solid #d7e5eb; border-radius:5px; padding:4px 8px; font-size:10px; overflow-wrap:anywhere; }
.gx-hold { color:#875809; background:#fff4dd; border:1px solid #f0dfb5; border-radius:6px; padding:9px 12px; margin-top:12px; font-size:11px; }
.gx-provenance > summary { color:#37566b; font-size:12px; font-weight:650; cursor:pointer; }
.gx-provenance[open] > summary { margin-bottom:15px; }
.gx-source-notes { padding-left:20px; font-size:11px; color:#5d7282; overflow-wrap:anywhere; }
@media(max-width:1100px) { .gx-scroll-hint { display:block; } }
@media(max-width:760px) { .gx .panel-head { flex-wrap:wrap; } .gx-controls label:not(.gx-search-label) { flex:1 1 100px; min-width:0; } .gx-controls select { min-width:0; width:100%; } .gx-window-controls > strong { flex-basis:100%; } .gx-window-controls input { width:105px; } .gx-navigation { flex-basis:100%; } .gx-export .button { font-size:10px; } .gx-gene-detail { padding:15px!important; } }
@media print { .gx-controls,.gx-window-controls,.gx-export,.gx-search-results { display:none!important; } .gx-track input { display:none; } .gx-scroll { overflow:visible; break-inside:avoid; } .gx-svg { min-width:0; } .gx-provenance { display:none; } }
"""

JS = r"""
(() => {
  'use strict';
  const root=document.getElementById('genome-explorer'), dataNode=document.getElementById('basis-genome-data');
  if(!root || !dataNode) return;
  let data;
  try { data=JSON.parse(dataNode.textContent); } catch(error) { return; }
  const samples=Array.isArray(data.samples)?data.samples:[];
  if(!samples.length) return;
  const $=id=>document.getElementById(id);
  const ns='http://www.w3.org/2000/svg';
  const COLORS={amr:'#b95947',vfdb:'#aa761a',provirus:'#8060ae',bgc:'#21846c',integron:'#ab588f',plasmid:'#3c92a4',ml_attention:'#4779ba',selected:'#203f59',genomic_islands:'#718292'};
  const DISPLAY_LABELS={amr:'AMR candidates',vfdb:'VFDB matches',provirus:'Viruses / proviruses',bgc:'Biosynthetic regions',integron:'Integron predictions',plasmid:'Plasmid predictions',ml_attention:'ML attention priorities',selected:'DeepSafe candidates',genomic_islands:'Genomic islands'};
  const W=1060,L=190,R=1030,P=R-L;
  const fmt=n=>Number(n).toLocaleString('en-US');
  const human=v=>String(v||'not recorded').replaceAll('_',' ');
  const finite=v=>typeof v==='number' && Number.isFinite(v);
  const node=(tag,text,cls)=>{ const x=document.createElement(tag); if(text!==null && text!==undefined)x.textContent=text; if(cls)x.className=cls;return x;};
  const sv=(tag,attrs={},text)=>{const x=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>x.setAttribute(k,String(v)));if(text!==undefined)x.textContent=text;return x;};
  const list=x=>Array.isArray(x)?x:[];
  let sample=samples[0],contig=list(sample.contigs)[0],start=1,end=1,currentGene=null,currentRegion=null;
  const visible=new Map();
  let overviewSvg=null,neighborhoodSvg=null;
  const genes=()=>list(contig&&contig.genes),regions=()=>list(contig&&contig.regions);
  const extent=()=>{
    if(contig&&finite(contig.length)&&contig.length>0)return contig.length;
    return Math.max(1,...genes().map(x=>Number(x.end)||1),...regions().map(x=>Number(x.end)||1));
  };
  const hasLength=()=>contig&&finite(contig.length)&&contig.length>0;
  const visibilityKey=id=>sample.id+'\u0000'+id;
  const visibleTracks=()=>list(sample.tracks).filter(t=>visible.get(visibilityKey(t.id))!==false);
  const colorGene=g=>g.queue==='ml_attention'?'#4779ba':g.candidate_anchor?'#087c78':'#9baeba';
  const eligibleAnchor=a=>typeof a==='string' && /^[A-Za-z][A-Za-z0-9_:.\-]*$/.test(a);
  function button(text,fn,cls='button'){const x=node('button',text,cls);x.type='button';x.addEventListener('click',fn);return x;}
  function svgButton(x,description,fn){x.setAttribute('role','button');x.setAttribute('tabindex','0');x.setAttribute('aria-label',description);x.append(sv('title',{},description));x.addEventListener('click',e=>{e.stopPropagation();fn();});x.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();fn();}});return x;}
  function newSvg(height,label){const x=sv('svg',{viewBox:`0 0 ${W} ${height}`,class:'gx-svg',role:'img','aria-label':label,xmlns:ns});x.append(sv('rect',{width:W,height,fill:'#fff'}));return x;}
  function mountSvg(id,svg){const holder=node('div',null,'gx-scroll');holder.append(svg);$(id).replaceChildren(holder);}
  function ticks(svg,lo,hi,y,left=L,right=R){
    const span=hi-lo+1;
    for(let i=0;i<=4;i++){const pos=i===4?hi:Math.round(lo+(span-1)*i/4),x=left+(right-left)*i/4;
      svg.append(sv('line',{x1:x,x2:x,y1:y+5,y2:y+9,stroke:'#b4c4d0'}));
      svg.append(sv('text',{x,y,'font-size':10,fill:'#697f8e','text-anchor':i===0?'start':i===4?'end':'middle'},fmt(pos)+' bp'));
    }
  }
  function setWindow(lo,hi){
    const n=extent();let span=Math.max(1,Math.min(n,Math.round(hi-lo+1)));lo=Math.round(lo);hi=lo+span-1;
    if(lo<1){lo=1;hi=span;} if(hi>n){hi=n;lo=Math.max(1,hi-span+1);}
    start=lo;end=hi;$('gx-start').value=String(start);$('gx-end').value=String(end);
    renderOverview();renderNeighborhood();
  }
  function changeWindow(scale,shift=0){const span=end-start+1,center=(start+end)/2+span*shift,newSpan=Math.min(extent(),Math.max(250,Math.round(span*scale)));setWindow(center-newSpan/2,center+newSpan/2-1);}
  function selectContig(id,initial=true){
    contig=list(sample.contigs).find(c=>c.id===id)||list(sample.contigs)[0];currentGene=null;currentRegion=null;
    if(!contig){$('gx-overview').replaceChildren(node('p','No coordinate-resolved contigs recorded.','empty'));$('gx-neighborhood').replaceChildren();$('gx-gene-detail').replaceChildren();$('gx-metadata').textContent='No contig coordinates available.';return;}
    $('gx-contig').value=contig.id;
    const max=String(extent());$('gx-start').max=max;$('gx-end').max=max;
    const selected=genes().filter(g=>g.candidate_anchor).sort((a,b)=>a.start-b.start),g=selected[0]||genes()[0];
    if(g&&initial)focusGene(g,true,false);else setWindow(1,Math.min(extent(),40000));
    metadata();
  }
  function selectSample(id){
    sample=samples.find(s=>s.id===id)||samples[0];$('gx-sample').value=sample.id;
    $('gx-contig').replaceChildren(...list(sample.contigs).map(c=>{const o=node('option',String(c.id));o.value=c.id;return o;}));
    buildTracks();selectContig(list(sample.contigs)[0]?.id);search();
  }
  function buildTracks(){
    $('gx-track-list').replaceChildren();
    for(const t of list(sample.tracks)){
      const available=!['not_assessed','unavailable','failed'].includes(t.status),label=node('label',null,'gx-track'+(available?'':' gx-unavailable'));
      const key=visibilityKey(t.id),check=node('input');check.type='checkbox';check.checked=available && visible.get(key)!==false;check.disabled=!available;
      check.setAttribute('aria-label',t.label+' track; '+human(t.status));
      if(!visible.has(key))visible.set(key,available);
      const dot=node('i');dot.style.background=COLORS[t.id]||'#718292';dot.setAttribute('aria-hidden','true');
      label.append(check,dot,node('span',t.label),node('small',available?'('+String(t.count||0)+')':human(t.status)));
      check.addEventListener('change',()=>{visible.set(key,check.checked);renderOverview();renderNeighborhood();});$('gx-track-list').append(label);
    }
  }
  function metadata(){
    if(!contig)return;
    const len=hasLength()?fmt(contig.length)+' bp · length source: '+human(contig.length_source):'Length not recorded; displayed extent ends at the last recorded feature ('+fmt(extent())+' bp)';
    $('gx-metadata').textContent=sample.id+' · contig '+contig.id+' · '+len+' · '+fmt(genes().length)+' mapped proteins. Linear display does not assert physical chromosome topology.';
  }
  function renderOverview(){
    if(!contig)return;
    const n=extent(),tracks=visibleTracks().filter(t=>!['not_assessed','unavailable','failed'].includes(t.status)),gc=list(contig.gc),hasGC=gc.some(g=>finite(g.gc_pct));
    const height=68+tracks.length*31+(hasGC?112:0),svg=newSvg(height,'Genome overview for '+sample.id+', contig '+contig.id);
    ticks(svg,1,n,18);
    const axis=sv('rect',{x:L,y:23,width:P,height:Math.max(18,tracks.length*31+10),fill:'#f8fafb',rx:4});
    axis.append(sv('title',{},'Click this coordinate area to reposition the neighborhood window.'));svg.append(axis);
    axis.addEventListener('click',e=>{const pt=svg.createSVGPoint();pt.x=e.clientX;pt.y=e.clientY;const p=pt.matrixTransform(svg.getScreenCTM().inverse()),center=Math.round((p.x-L)/P*n+1),span=end-start+1;setWindow(center-span/2,center+span/2-1);});
    const x=L+(start-1)/n*P,w=Math.max(2,(end-start+1)/n*P);
    svg.append(sv('rect',{x,y:25,width:w,height:Math.max(18,tracks.length*31+8),fill:'#cfe9e5','fill-opacity':.58,stroke:'#2c8f85','stroke-width':1,'pointer-events':'none'}));
    tracks.forEach((t,i)=>{
      const y=45+i*31,all=regions().filter(r=>r.track===t.id);
      const label=sv('text',{x:12,y:y+4,fill:'#3d586b','font-size':11,'font-weight':600},DISPLAY_LABELS[t.id]||String(t.label).slice(0,25));
      label.append(sv('title',{},t.label));svg.append(label);
      svg.append(sv('line',{x1:L,x2:R,y1:y,y2:y,stroke:'#dce6eb','stroke-width':3,'pointer-events':'none'}));
      all.forEach(r=>{const mark=sv('rect',{x:L+(r.start-1)/n*P,y:y-7,width:Math.max(3,(r.end-r.start+1)/n*P),height:14,rx:2,fill:COLORS[t.id]||'#718292'});
        const description=(r.label||t.label)+' · '+fmt(r.start)+'–'+fmt(r.end)+(r.review_hold?' · '+human(r.review_hold):'');
        svg.append(svgButton(mark,description,()=>focusRegion(r)));});
      if(!all.length)svg.append(sv('text',{x:L+8,y:y+4,fill:'#8698a6','font-size':10},'No mapped records on this contig'));
    });
    if(hasGC)renderGC(svg,gc,tracks.length*31+77,n);
    svg.append(sv('text',{x:L,y:height-10,fill:'#4c7a79','font-size':10},'Highlighted window: '+fmt(start)+'–'+fmt(end)+' bp'));
    overviewSvg=svg;mountSvg('gx-overview',svg);
  }
  function renderGC(svg,gc,y,n){
    svg.append(sv('text',{x:12,y:y+13,fill:'#567082','font-size':11},'GC content (%)'));
    svg.append(sv('text',{x:12,y:y+58,fill:'#567082','font-size':11},'GC skew'));
    svg.append(sv('line',{x1:L,x2:R,y1:y+20,y2:y+20,stroke:'#dce6eb'}));
    svg.append(sv('line',{x1:L,x2:R,y1:y+63,y2:y+63,stroke:'#dce6eb'}));
    const positions=gc.map(g=>({x:L+((g.start+g.end)/2-1)/n*P,g}));
    let connected=false,previousEnd=null;
    const path=positions.map(p=>{
      if(!finite(p.g.gc_pct)){connected=false;previousEnd=null;return '';}
      const command=connected && previousEnd!==null && p.g.start<=previousEnd+1?'L':'M';
      connected=true;previousEnd=p.g.end;
      return command+p.x.toFixed(2)+','+(y+40-p.g.gc_pct*.4).toFixed(2);
    }).join(' ');
    svg.append(sv('path',{d:path,fill:'none',stroke:'#3e918b','stroke-width':1.2}));
    positions.forEach(({x,g})=>{if(finite(g.gc_skew))svg.append(sv('line',{x1:x,x2:x,y1:y+63,y2:y+63-g.gc_skew*18,stroke:g.gc_skew>=0?'#6c72ad':'#c39056','stroke-width':Math.max(1,(g.end-g.start+1)/n*P)}));});
    svg.append(sv('text',{x:R+3,y:y+4,fill:'#8698a6','font-size':8},'100'));
    svg.append(sv('text',{x:R+3,y:y+41,fill:'#8698a6','font-size':8},'0'));
    svg.append(sv('text',{x:R+3,y:y+63,fill:'#8698a6','font-size':8},'0'));
  }
  function renderNeighborhood(){
    if(!contig)return;
    const selectedTracks=new Set(visibleTracks().map(t=>t.id)),near=genes().filter(g=>g.end>=start&&g.start<=end),span=end-start+1;
    const regionGroups=visibleTracks().filter(t=>regions().some(r=>r.track===t.id&&r.end>=start&&r.start<=end));
    const baseY=90,arrowView=near.length<=200;
    const svg=newSvg(arrowView?240+regionGroups.length*27:145+regionGroups.length*27,'Gene neighborhood '+sample.id+' contig '+contig.id+' '+start+' to '+end+' bp');
    ticks(svg,start,end,23);
    const project=p=>L+(p-start)/span*P;
    svg.append(sv('text',{x:12,y:baseY-18,fill:'#466479','font-size':11,'font-weight':600},arrowView?'Protein-coding genes':'Protein density'));
    if(arrowView){
      const occupied={'+':[-Infinity,-Infinity],'-':[-Infinity,-Infinity],'?':[-Infinity]};
      ['+','-'].forEach((strand,i)=>{const y=baseY+i*51;svg.append(sv('text',{x:L-12,y:y+5,'text-anchor':'end','font-size':11,fill:'#6e8594'},strand));svg.append(sv('line',{x1:L,x2:R,y1:y,y2:y,stroke:'#e1e8ed'}));});
      near.sort((a,b)=>a.start-b.start).forEach(g=>{
        const strand=['+','-'].includes(g.strand)?g.strand:'?',lanes=occupied[strand];
        let lane=lanes.findIndex(last=>last<g.start);if(lane<0)lane=lanes.indexOf(Math.min(...lanes));lanes[lane]=g.end;
        const y=baseY+(strand==='-'?51:strand==='?'?101:0)+lane*19;
        const x1=project(Math.max(start,g.start)),x2=Math.max(x1+1.5,project(Math.min(end+1,g.end+1))),head=Math.min(7,(x2-x1)*.33);
        let points;
        if(strand==='+'&&g.end<=end)points=`${x1},${y-6} ${x2-head},${y-6} ${x2},${y} ${x2-head},${y+6} ${x1},${y+6}`;
        else if(strand==='-'&&g.start>=start)points=`${x1},${y} ${x1+head},${y-6} ${x2},${y-6} ${x2},${y+6} ${x1+head},${y+6}`;
        else points=`${x1},${y-6} ${x2},${y-6} ${x2},${y+6} ${x1},${y+6}`;
        const isSelected=currentGene&&currentGene.id===g.id,mark=sv('polygon',{points,fill:colorGene(g),stroke:isSelected?'#122e40':'#fff','stroke-width':isSelected?2:1});
        const description=g.id+' · '+fmt(g.start)+'–'+fmt(g.end)+' · strand '+strand+(g.product?' · '+g.product:'')+(g.review_hold?' · '+human(g.review_hold):'');
        svg.append(svgButton(mark,description,()=>focusGene(g,false)));
        if(isSelected)svg.append(sv('text',{x:(x1+x2)/2,y:y-12,'text-anchor':'middle','font-size':10,'font-weight':650,fill:'#264e63'},g.id));
      });
    }else{
      const bins=new Array(150).fill(0);near.forEach(g=>{const a=Math.max(0,Math.floor((Math.max(start,g.start)-start)/span*150)),b=Math.min(149,Math.floor((Math.min(end,g.end)-start)/span*150));for(let i=a;i<=b;i++)bins[i]++;});
      const max=Math.max(1,...bins);bins.forEach((n,i)=>svg.append(sv('rect',{x:L+i*P/150,y:baseY-n/max*31,width:P/150,height:n/max*31,fill:'#9baeba'})));
      svg.append(sv('text',{x:L,y:baseY+21,fill:'#617c8d','font-size':11},fmt(near.length)+' proteins overlap this window. Zoom in for individual gene arrows.'));
    }
    const tracksY=arrowView?213:118;
    regionGroups.forEach((t,i)=>{
      const y=tracksY+i*27,label=sv('text',{x:12,y:y+4,fill:'#526b7d','font-size':10},DISPLAY_LABELS[t.id]||String(t.label).slice(0,25));
      label.append(sv('title',{},t.label));svg.append(label);
      svg.append(sv('line',{x1:L,x2:R,y1:y,y2:y,stroke:'#e6edf1','stroke-width':3}));
      regions().filter(r=>r.track===t.id&&r.end>=start&&r.start<=end).forEach(r=>{
        const x=project(Math.max(start,r.start)),width=Math.max(2,project(Math.min(end+1,r.end+1))-x),mark=sv('rect',{x,y:y-6,width,height:12,rx:2,fill:COLORS[t.id]||'#718292'});
        svg.append(svgButton(mark,(r.label||t.label)+' · '+fmt(r.start)+'–'+fmt(r.end)+(r.review_hold?' · '+human(r.review_hold):''),()=>focusRegion(r)));
      });
    });
    $('gx-window-status').textContent=fmt(start)+'–'+fmt(end)+' bp · '+fmt(span)+' bp window · '+fmt(near.length)+' overlapping proteins'+(!hasLength()?' · displayed feature extent, not a measured contig length':'');
    neighborhoodSvg=svg;mountSvg('gx-neighborhood',svg);renderDetail();
  }
  function detailKv(entries){const wrap=node('div',null,'gx-detail-kv');entries.forEach(([label,value])=>{const item=node('span');item.append(node('strong',label),document.createTextNode(value));wrap.append(item);});return wrap;}
  function renderDetail(){
    const d=$('gx-gene-detail');d.replaceChildren();const g=currentGene,r=currentRegion;
    if(!g&&!r){d.append(node('p','Select a protein arrow or region to inspect its recorded evidence.','small muted'));return;}
    const item=g||r,head=node('div',null,'gx-detail-heading'),title=node('div');
    title.append(node('h4',g?g.id:(r.label||r.id)),node('p',g?(g.product||'Product annotation not included in this evidence snapshot.'):'Recorded '+human(r.module)+' region / source annotation.'));
    head.append(title);
    const anchor=item.candidate_anchor;
    if(eligibleAnchor(anchor))head.append(button('Open DeepSafe evidence',()=>document.dispatchEvent(new CustomEvent('basis:open-candidate',{detail:{anchor}}))));
    d.append(head,detailKv([['Coordinates',fmt(item.start)+'–'+fmt(item.end)+' bp'],['Span',fmt(item.end-item.start+1)+' bp'],['Contig',String(contig.id)],...(g?[['Strand',g.strand||'?'],['Selection',g.queue?human(g.queue):'Not selected for DeepSafe']]:[['Track',human(r.track)]])]));
    if(g&&list(g.labels).length){d.append(node('p','Recorded source annotations (not established functions)','small muted'));const labels=node('div',null,'gx-labels');g.labels.forEach(label=>labels.append(node('span',label)));d.append(labels);}
    if(item.review_hold)d.append(node('p','Interpretation hold: '+human(item.review_hold)+'. This source call is displayed for review.','gx-hold'));
    if(item.interpretation)d.append(node('p','Recorded interpretation: '+human(item.interpretation),'small muted'));
    const overlaps=g?regions().filter(x=>['provirus','bgc','integron','plasmid','genomic_islands'].includes(x.track)&&x.end>=g.start&&x.start<=g.end):[];
    if(g)d.append(node('p',overlaps.length?'Overlapping recorded regions: '+overlaps.map(x=>x.label+' ('+human(x.track)+')').join('; '):'No overlap with the mapped regional predictions in this snapshot.','small muted'));
  }
  function focusGene(g,recenter=true,emit=true){
    currentGene=g;currentRegion=null;
    if(recenter){const middle=(g.start+g.end)/2,half=Math.max(15000,(g.end-g.start+1)*2);setWindow(middle-half,middle+half);}else renderNeighborhood();
    if(emit)document.dispatchEvent(new CustomEvent('basis:focus-locus',{detail:{sample_id:sample.id,protein_id:g.id,origin:'genome-explorer'}}));
  }
  function focusRegion(r){
    const g=r.protein_id&&genes().find(x=>x.id===r.protein_id);
    if(g){focusGene(g);return;}
    currentGene=null;currentRegion=r;const flank=Math.max(10000,(r.end-r.start+1)*.2);setWindow(r.start-flank,r.end+flank);
  }
  function focus(sampleId,proteinId){
    const s=samples.find(x=>x.id===sampleId);if(!s)return false;
    const c=list(s.contigs).find(x=>list(x.genes).some(g=>g.id===proteinId));if(!c)return false;
    if(sample.id!==s.id)selectSample(s.id);if(!contig||contig.id!==c.id)selectContig(c.id,false);
    focusGene(list(c.genes).find(g=>g.id===proteinId),true,false);return true;
  }
  function search(){
    const query=$('gx-search').value.trim().toLowerCase(),out=$('gx-search-results');out.replaceChildren();out.hidden=!query;if(!query)return;
    const found=[];let count=0;
    for(const s of samples)for(const c of list(s.contigs))for(const g of list(c.genes)){
      if([g.id,g.product,...list(g.labels)].join(' ').toLowerCase().includes(query)){count++;if(found.length<30)found.push({s,c,g});}
    }
    out.append(node('p',count?fmt(count)+' matching proteins'+(count>30?' · first 30 shown; refine your search':''):'No matching proteins. Search by protein ID or a recorded annotation.'));
    found.forEach(({s,c,g})=>{const b=button(g.id,()=>{focus(s.id,g.id);out.hidden=true;document.dispatchEvent(new CustomEvent('basis:focus-locus',{detail:{sample_id:s.id,protein_id:g.id,origin:'genome-explorer'}}));},'');b.append(node('small',s.id+' · '+c.id+' · '+fmt(g.start)+'–'+fmt(g.end)+' · '+(g.product||list(g.labels).slice(0,2).join('; ')||'Product not recorded')));out.append(b);});
  }
  function exportSvg(){
    if(!overviewSvg||!neighborhoodSvg)return null;
    const a=overviewSvg.viewBox.baseVal.height,b=neighborhoodSvg.viewBox.baseVal.height,total=a+b+110;
    const svg=sv('svg',{xmlns:ns,width:W,height:total,viewBox:`0 0 ${W} ${total}`});svg.append(sv('rect',{width:W,height:total,fill:'#fff'}));
    svg.append(sv('style',{},'text{font-family:Arial,sans-serif}'));
    svg.append(sv('text',{x:15,y:26,'font-family':'Arial,sans-serif','font-size':18,'font-weight':700,fill:'#203f59'},'BaSIS | '+sample.id+' | contig '+contig.id));
    svg.append(sv('text',{x:15,y:45,'font-size':11,fill:'#536e81'},'Recorded evidence · 1-based inclusive coordinates · neighborhood '+fmt(start)+'–'+fmt(end)+' bp'));
    [[overviewSvg,60],[neighborhoodSvg,a+75]].forEach(([original,y])=>{const g=sv('g',{transform:`translate(0 ${y})`});Array.from(original.childNodes).forEach(x=>g.append(x.cloneNode(true)));svg.append(g);});
    svg.append(sv('text',{x:15,y:total-9,'font-size':10,fill:'#6a7f8e'},'Source annotations and spatial overlaps do not establish gene function or organism safety.'));
    return {text:new XMLSerializer().serializeToString(svg),height:total};
  }
  function download(blob,suffix){const url=URL.createObjectURL(blob),a=node('a');a.href=url;a.download=('BaSIS_'+sample.id+'_'+contig.id+'_'+start+'-'+end).replace(/[^a-zA-Z0-9_.-]/g,'_')+'.'+suffix;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  $('gx-export-svg').addEventListener('click',()=>{const x=exportSvg();if(x)download(new Blob([x.text],{type:'image/svg+xml;charset=utf-8'}),'svg');});
  $('gx-export-png').addEventListener('click',()=>{const x=exportSvg();if(!x)return;const blob=new Blob([x.text],{type:'image/svg+xml;charset=utf-8'}),url=URL.createObjectURL(blob),img=new Image();
    img.onload=()=>{try{const canvas=document.createElement('canvas');canvas.width=W*2;canvas.height=x.height*2;const ctx=canvas.getContext('2d');ctx.scale(2,2);ctx.drawImage(img,0,0,W,x.height);canvas.toBlob(b=>{if(b)download(b,'png');else $('gx-window-status').textContent='PNG export could not be created; use SVG export.';},'image/png');}catch(e){$('gx-window-status').textContent='PNG export unavailable in this browser; use SVG export.';}finally{URL.revokeObjectURL(url);}};
    img.onerror=()=>{URL.revokeObjectURL(url);$('gx-window-status').textContent='PNG export unavailable in this browser; use SVG export.';};img.src=url;
  });
  $('gx-sample').replaceChildren(...samples.map(s=>{const x=node('option',s.id);x.value=s.id;return x;}));
  $('gx-sample').addEventListener('change',()=>selectSample($('gx-sample').value));
  $('gx-contig').addEventListener('change',()=>selectContig($('gx-contig').value));
  let searchTimer;$('gx-search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(search,120);});
  $('gx-search').addEventListener('keydown',e=>{if(e.key==='Escape')$('gx-search-results').hidden=true;if(e.key==='ArrowDown'){$('gx-search-results').querySelector('button')?.focus();e.preventDefault();}});
  function apply(){const lo=Number($('gx-start').value),hi=Number($('gx-end').value);if(!Number.isInteger(lo)||!Number.isInteger(hi)||lo<1||hi<lo||hi>extent()){$('gx-window-status').textContent='Enter whole-number coordinates between 1 and '+fmt(extent())+', with end at or after start.';return;}setWindow(lo,hi);}
  $('gx-apply').addEventListener('click',apply);['gx-start','gx-end'].forEach(id=>$(id).addEventListener('keydown',e=>{if(e.key==='Enter')apply();}));
  $('gx-pan-left').addEventListener('click',()=>changeWindow(1,-.65));$('gx-pan-right').addEventListener('click',()=>changeWindow(1,.65));
  $('gx-zoom-in').addEventListener('click',()=>changeWindow(.5));$('gx-zoom-out').addEventListener('click',()=>changeWindow(2));
  $('gx-full').addEventListener('click',()=>setWindow(1,extent()));
  document.addEventListener('basis:focus-locus',e=>{
    if(e.detail&&e.detail.origin!=='genome-explorer'&&focus(e.detail.sample_id,e.detail.protein_id)){
      root.scrollIntoView({behavior:'smooth',block:'start'});
    }
  });
  window.BaSISGenome={focus};
  ['gx-controls','gx-interactive','gx-export'].forEach(id=>$(id).hidden=false);
  selectSample(samples[0].id);
})();
"""
