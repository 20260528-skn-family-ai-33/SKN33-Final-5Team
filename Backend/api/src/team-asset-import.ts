import {readFile} from 'node:fs/promises';
import {baseDataDirectory,dataDirectory,withDataDirectory} from './paths';
import {builderWorker,persistLibraryUpload} from './agent-builder';
import {readWorkspaceFile} from './workspace-files';
import {AuthError} from './cognito-auth';
import {withTeamLibraryAccess} from './team-library-access';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** A downloaded original becomes an independent personal upload, never a live cross-team reference. */
export async function importTeamLibraryAsset(teamId:string,userId:string,assetId:unknown,
 requireAccess:()=>Promise<{id:string;name:string}>):Promise<{asset:any}>{
 if(!uuid.test(teamId)||!uuid.test(userId)||typeof assetId!=='string'||!uuid.test(assetId))
  throw new AuthError(400,'연결할 팀 자료를 확인해주세요.');
 const team=await requireAccess();
 const source=await withTeamLibraryAccess(requireAccess,()=>withDataDirectory(baseDataDirectory('teams',teamId),async()=>{
  const {asset}=await builderWorker({action:'asset',id:assetId});
  if(!/^builder-originals\/[a-f0-9]{64}\.[a-z0-9]+$/.test(asset.original_path||''))
   throw new AuthError(400,'원본 파일이 없는 자료입니다. 팀 자료함에 파일을 다시 올려주세요.');
  await requireAccess();
  const bytes=await readWorkspaceFile(asset.original_path,()=>readFile(dataDirectory(asset.original_path)));
  if(!bytes.length||bytes.length>8*1024*1024)throw new AuthError(400,'파일은 8MB 이하로 연결하세요.');
  return {name:String(asset.name),bytes};
 }));
 await requireAccess();
 return withTeamLibraryAccess(requireAccess,()=>withDataDirectory(baseDataDirectory('users',userId),()=>persistLibraryUpload(
  {fileName:source.name,data:source.bytes.toString('base64')},null,
  {team_id:team.id,team_name:team.name,asset_id:assetId},requireAccess)));
}
