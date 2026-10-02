import * as T from '../../core/time.js';
let input='';
for await (const chunk of process.stdin) input+=chunk;
const cases=JSON.parse(input);
process.stdout.write(JSON.stringify(cases.map((c)=>{
  if(c.kind==='wall'){
    const off=T.offsetEastMin(c.tz,c.ms);
    const wall=T.wallFromUtcMs(c.ms,off,0);
    return {wall,back:T.utcFromWallMs(wall,off,0)};
  }
  return c.date!=null?T.zoneOffsetAtLocalNoon(c.tz,c.date):T.offsetEastMin(c.tz,c.ms);
})));
