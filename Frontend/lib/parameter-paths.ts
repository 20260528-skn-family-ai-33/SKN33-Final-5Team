import type {Json} from './workflow';
const blocked=new Set(['__proto__','prototype','constructor']);
const parts=(path:string)=>{const values=path.split('.');if(values.some(value=>!value||blocked.has(value)))throw new Error('설정 항목의 경로를 확인해주세요.');return values;};
export function getParameter(parameters:Json,path:string):any {
 const keys=parts(path);let value:any=parameters;
 for(const key of keys){if(!value||typeof value!=='object'||!Object.hasOwn(value,key))return Object.hasOwn(parameters,path)?parameters[path]:undefined;value=value[key];}
 return value;
}
export function setParameter(parameters:Json,path:string,value:any):Json {
 const keys=parts(path),next=JSON.parse(JSON.stringify(parameters));let target=next;
 for(const key of keys.slice(0,-1)){if(!target[key]||typeof target[key]!=='object'||Array.isArray(target[key]))target[key]={};target=target[key];}
 target[keys.at(-1)!]=value;if(keys.length>1)delete next[path];return next;
}
export function parseJsonParameter(value:string):any {return value.trim().startsWith('=')?value:JSON.parse(value);}
