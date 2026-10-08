/* Which sub-keys does the page read that the payload never sends?

   The inverse of unused.js. unused.js finds data nobody renders; THIS finds renderers
   reading a field that does not exist — which is worse, because the card does not break,
   it quietly shows its empty state on live data.

   Found 2026-10-09: promoCard() guarded on `O.promo.codes`, a key the collector has never
   emitted (it sends `on` and `off`). The card had been printing "waiting for the first run
   that carries promo data" forever while 116 codes and E£12.5M of revenue sat in the
   payload and rendered fine on another tab.

   usage: node tools/ghost.js <path-to-data.js> [path-to-index.html] */
const fs=require('fs');
const dataPath=process.argv[2]||(__dirname+'/../okv/data.js');
const htmlPath=process.argv[3]||(__dirname+'/../index.html');
global.window={};
eval(fs.readFileSync(dataPath,'utf8'));
const O=window.O||{};
let s=fs.readFileSync(htmlPath,'utf8');
/* strip comments first: a comment explaining why a field was NOT used is not a read.
   O.why.prods lives only inside the note saying a product waterfall was the wrong build. */
s=s.replace(/\/\*[\s\S]*?\*\//g,' ').replace(/^\s*\/\/.*$/gm,' ');
/* fields the PAGE assigns at runtime rather than the collector sending them */
const RUNTIME=new Set(['ad.mclkDef','ad.mclkAll']);

/* only check top-level keys that EXIST and are plain objects -- for those we know the full
   set of sub-keys the collector sends, so a read of anything else is a ghost */
const checkable=Object.keys(O).filter(k=>{
  const v=O[k];
  return v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length>0;});

const ghosts=[];
checkable.forEach(k=>{
  const have=new Set(Object.keys(O[k]));
  /* O.k.sub — only a bare identifier, so O.pos['Dokki'] and dynamic lookups are skipped */
  const re=new RegExp('[^A-Za-z0-9_$.]O\\s*\\.\\s*'+k+'\\s*\\.\\s*([A-Za-z_$][A-Za-z0-9_$]*)','g');
  let m;
  while((m=re.exec(s))){
    const sub=m[1];
    if(have.has(sub))continue;
    if(RUNTIME.has(k+'.'+sub))continue;
    /* methods and the shapes a guard legitimately probes on any object */
    if(['length','forEach','map','filter','slice','keys','hasOwnProperty','toString','concat','sort','reduce','some','every','find','indexOf','join','push','split','replace','match','trim','toFixed','includes'].includes(sub))continue;
    const line=s.slice(0,m.index).split('\n').length;
    ghosts.push({key:'O.'+k+'.'+sub, line});
  }});

/* dedupe on the field, keep the first line */
const seen={};
ghosts.forEach(g=>{if(!seen[g.key])seen[g.key]=g.line;});
const list=Object.keys(seen).sort();
console.log('payload objects checked: '+checkable.length+'  ·  fields read but never sent: '+list.length);
if(list.length){
  console.log('\nTHE PAYLOAD NEVER SENDS THESE, BUT THE PAGE READS THEM:');
  list.forEach(k=>console.log('  '+k+'   first read at line '+seen[k]));
  console.log('\nEach one is a card that silently shows its empty state on live data.');
}
process.exit(list.length?1:0);
