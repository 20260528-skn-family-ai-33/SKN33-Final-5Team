import type {IncomingMessage,ServerResponse} from 'node:http';
import {handleAgentBuilder} from './agent-builder';
import {AuthError,authJson} from './cognito-auth';
import type {Store} from './store';
import {withTeamLibraryAccess} from './team-library-access';

const permissions:Record<string,Record<string,boolean>>={
 library:{GET:false},assets:{GET:false,DELETE:true},asset_preview:{GET:false},original:{GET:false},
 document_preview:{GET:false},document_review:{GET:false,POST:true},library_upload:{POST:true},
 folders:{POST:true,PATCH:true,DELETE:true},asset_move:{POST:true},
};
export function teamLibraryPermission(action:string,method:string){
 if(!Object.hasOwn(permissions,action))throw new AuthError(404,'팀 자료함 기능을 찾을 수 없습니다.');
 if(!Object.hasOwn(permissions[action],method))throw new AuthError(405,'지원하지 않는 팀 자료 요청입니다.');
 return permissions[action][method];
}
async function body(req:IncomingMessage,max=4096){
 let size=0;const chunks:Buffer[]=[];
 for await(const chunk of req){size+=chunk.length;if(size>max)throw new AuthError(413,'요청 크기를 줄여주세요.');chunks.push(Buffer.from(chunk));}
 try{return JSON.parse(Buffer.concat(chunks).toString('utf8')||'{}');}catch{throw new AuthError(400,'자료 요청 내용을 확인해주세요.');}
}
/** Deliberately contains no workflow store, scheduler, OAuth client or restore loop. */
export async function handleTeamLibrary(req:IncomingMessage,res:ServerResponse,url:URL,authorize:()=>Promise<unknown>){
 await authorize();
 return withTeamLibraryAccess(authorize,async()=>{
  try{
   const handled=await handleAgentBuilder(req,res,url,{} as Store,body,authJson);
   if(!handled)throw new AuthError(404,'팀 자료함 기능을 찾을 수 없습니다.');
  }catch(error){if(error instanceof AuthError)throw error;if(!res.writableEnded&&!res.destroyed)authJson(res,400,{error:(error as Error).message});}
 });
}
