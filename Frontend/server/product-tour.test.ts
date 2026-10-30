import assert from 'node:assert/strict';
import test from 'node:test';
import {createRequire} from 'node:module';
import {buildSync} from 'esbuild';
import {resolve} from 'node:path';
import {FRONTEND_ROOT} from './paths';
const require=createRequire(new URL('../package.json',import.meta.url));
const compiled=buildSync({entryPoints:[resolve(FRONTEND_ROOT,'app/product-tour.tsx')],absWorkingDir:FRONTEND_ROOT,bundle:true,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'},write:false,logLevel:'silent'}).outputFiles[0].text;
const mod={exports:{} as any};new Function('require','module','exports',compiled)(require,mod,mod.exports);const {tourPosition,tourSteps}=mod.exports;
test('tour cards remain within narrow and short viewports, including anchors near screen edges',()=>{
 for(const width of [320,390,768,1280])for(const height of [480,844])for(const rect of [null,{top:0,left:0,width:100,height:40},{top:height-70,left:width-80,width:60,height:40}]){const p=tourPosition(rect,width,height,260);assert.ok(p.left>=16);assert.ok(p.left+p.width<=width-16);assert.ok(p.top>=16);assert.ok(p.top+260<=height-16);}
});
test('guidance uses the vocabulary and controls of each active page',()=>{assert.ok(tourSteps('workflow').some((s:any)=>s.target==='settings'&&s.panel==='settings'));assert.ok(tourSteps('nodes').some((s:any)=>s.target==='task-form'));assert.ok(tourSteps('run').some((s:any)=>s.target==='run-submit'));assert.ok(tourSteps('workflows').some((s:any)=>s.target==='create-workflow'));});

test('tour can be suppressed after dismissal even when browser storage is blocked',()=>{
 const before=Object.getOwnPropertyDescriptor(globalThis,'localStorage');
 Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem(){throw Error('차단');}}});
 try{assert.equal(mod.exports.shouldStartTour('intro'),false);assert.equal(mod.exports.shouldStartTour('preview'),false);assert.equal(mod.exports.shouldStartTour('workflow'),false);}finally{if(before)Object.defineProperty(globalThis,'localStorage',before);else Reflect.deleteProperty(globalThis,'localStorage');}
});
