/* Does the page print a bare coefficient anywhere?

   Standing rule: never show a correlation coefficient. "r = 0.45" is not a number anyone
   acts on and it is not a percentage. The same fact belongs on the page as a gap between
   two measured halves — "those ads' cost per purchase moved +5%, the other half −7%, a gap
   of 12% (6 ads against 7)" — which can be argued with.

   This catches the shapes a coefficient comes back in: a printed r, an R², a "strong /
   moderate / weak" grader, and a toFixed on something named like a correlation.

   usage: node tools/nocoef.js [path-to-index.html] */
const fs=require('fs');
const p=process.argv[2]||(__dirname+'/../index.html');
const s=fs.readFileSync(p,'utf8');
const PATS=[
  [/\br\s*=\s*'\s*\+/g,            "a printed  r = ..."],
  [/'\s*\(r\s*=/g,                 "an inline (r=..."],
  [/\bR²|\bR\^2|\br²/g,            "an R squared"],
  [/\brho\b\s*\.toFixed/g,         "a printed rho"],
  [/\bcorr(el)?\w*\s*\.toFixed/g,  "a printed correlation"],
  [/>=\s*0?\.7\s*\?\s*'strong'/g,  "a strong/moderate/weak coefficient grader"],
];
const lines=s.split('\n');
let bad=0;
PATS.forEach(([re,what])=>{
  lines.forEach((ln,i)=>{
    re.lastIndex=0;
    if(re.test(ln)){
      // a comment explaining why coefficients are banned is not a violation
      if(/^\s*(\/\*|\*|\/\/)/.test(ln)||/never print r|not a number anyone acts on/i.test(ln))return;
      bad++; console.log('  line '+(i+1)+'  '+what+'\n    '+ln.trim().slice(0,120));
    }});
});
console.log(bad?('\n'+bad+' place(s) still print a coefficient — state the gap between two measured halves as a percentage instead (halfSplit() in index.html).')
               :'no bare coefficients: every relationship is stated as a percentage gap between two measured groups');
process.exit(bad?1:0);
