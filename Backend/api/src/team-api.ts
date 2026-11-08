import type {IncomingMessage,ServerResponse} from 'node:http';
import type {AuthUser} from './auth-users';
import {AuthError,authJson} from './cognito-auth';
import {TeamService,teamId} from './team-service';

export async function teamRequestBody(req:IncomingMessage,keys:string[],max=4096){
 let size=0;const chunks:Buffer[]=[];
 for await(const chunk of req){size+=chunk.length;if(size>max)throw new AuthError(413,'팀 요청 크기를 줄여주세요.');chunks.push(Buffer.from(chunk));}
 let body:any;try{body=JSON.parse(Buffer.concat(chunks).toString('utf8')||'{}');}catch{throw new AuthError(400,'요청 내용을 확인해주세요.');}
 if(!body||typeof body!=='object'||Array.isArray(body)||Object.keys(body).some(key=>!keys.includes(key)))throw new AuthError(400,'팀 요청 형식을 확인해주세요.');return body;
}
function method(req:IncomingMessage,res:ServerResponse,allowed:string[]){
 if(allowed.includes(req.method||'GET'))return true;
 res.setHeader('Allow',allowed.join(', '));authJson(res,405,{error:'지원하지 않는 요청입니다.'});return false;
}
export async function handleTeamApi(req:IncomingMessage,res:ServerResponse,url:URL,user:AuthUser,teams:TeamService){
 const path=url.pathname;
 if(!/^\/api\/(teams|team-invitations)(\/|$)/.test(path))return false;
 if(path.includes('/library/'))return false;
 if(url.search)throw new AuthError(400,'팀 조회 조건을 확인해주세요.');
 if(path==='/api/teams'){
  if(!method(req,res,['GET','POST']))return true;
  if(req.method==='POST'){const body=await teamRequestBody(req,['name']);authJson(res,200,{team:await teams.create(user,body.name)});}
  else authJson(res,200,{teams:await teams.list(user)});return true;
 }
 if(path==='/api/team-invitations'){
  if(method(req,res,['GET']))authJson(res,200,{invitations:await teams.received(user)});return true;
 }
 const response=/^\/api\/team-invitations\/([^/]+)\/(accept|decline)$/.exec(path);
 if(response){if(method(req,res,['POST'])){await teamRequestBody(req,[]);authJson(res,200,await teams.respond(user,teamId(response[1]),response[2]==='accept'));}return true;}
 const members=/^\/api\/teams\/([^/]+)\/members(?:\/([^/]+))?$/.exec(path);
 if(members){
  const id=teamId(members[1]);
  if(!members[2]){if(method(req,res,['GET']))authJson(res,200,await teams.members(user,id));return true;}
  if(!method(req,res,['PATCH','DELETE']))return true;
  const userId=teamId(members[2]);
  if(req.method==='PATCH'){const body=await teamRequestBody(req,['role']);if(body.role===undefined)throw new AuthError(400,'변경할 팀 권한을 선택해주세요.');authJson(res,200,await teams.changeMember(user,id,userId,body.role));}
  else {await teamRequestBody(req,[]);authJson(res,200,await teams.changeMember(user,id,userId));}return true;
 }
 const invitations=/^\/api\/teams\/([^/]+)\/invitations(?:\/([^/]+))?$/.exec(path);
 if(invitations){
  const id=teamId(invitations[1]);
  if(invitations[2]){if(method(req,res,['DELETE'])){await teamRequestBody(req,[]);authJson(res,200,await teams.cancel(user,id,teamId(invitations[2])));}return true;}
  if(!method(req,res,['GET','POST']))return true;
  if(req.method==='POST'){const body=await teamRequestBody(req,['email','role']);authJson(res,200,{invitation:await teams.invite(user,id,body.email,body.role)});}
  else authJson(res,200,{invitations:await teams.invitations(user,id)});return true;
 }
 throw new AuthError(404,'팀 기능을 찾을 수 없습니다.');
}
