import {existsSync,mkdirSync} from 'node:fs';
import {dirname,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

// Both src/ source files and the bundled build-server/ entry live one level below Backend/api.
export const API_ROOT=resolve(fileURLToPath(new URL('..',import.meta.url)));
export const BACKEND_ROOT=resolve(API_ROOT,'..');
export const PROJECT_ROOT=resolve(BACKEND_ROOT,'..');
// The screens are built into Frontend/dist-app and served by this API server.
export const FRONTEND_ROOT=resolve(PROJECT_ROOT,'Frontend');
export const apiPath=(...parts:string[])=>resolve(API_ROOT,...parts);
export const frontendPath=(...parts:string[])=>resolve(FRONTEND_ROOT,...parts);
export const backendPath=(...parts:string[])=>resolve(BACKEND_ROOT,...parts);
// Sample files for tests and dev scripts; the server never reads them.
export const examplePath=(...parts:string[])=>resolve(BACKEND_ROOT,'tests','fixtures','examples',...parts);
export const dataDirectory=(...parts:string[])=>resolve(PROJECT_ROOT,process.env.EASYFLOW_DATA_DIR||'data',...parts);
// Live-check reports from dev scripts go to the (git-ignored) data directory.
export const documentationPath=(...parts:string[])=>{
 const path=dataDirectory('live-checks',...parts);mkdirSync(dirname(path),{recursive:true});return path;
};
const pythonExecutable=process.platform==='win32'?'Scripts/python.exe':'bin/python';

export function agentPython():string {
 if(process.env.A4B_AGENT_PYTHON)return resolve(PROJECT_ROOT,process.env.A4B_AGENT_PYTHON);
 const candidates=[backendPath('.venv-agent',pythonExecutable),resolve(PROJECT_ROOT,'.venv-agent',pythonExecutable)];
 return candidates.find(existsSync)||candidates[0];
}

export function doclingPython():string|undefined {
 if(process.env.EASYFLOW_DOCLING_PYTHON)return resolve(PROJECT_ROOT,process.env.EASYFLOW_DOCLING_PYTHON);
 return [backendPath('.venv-docling',pythonExecutable),resolve(PROJECT_ROOT,'.venv-docling',pythonExecutable),resolve(PROJECT_ROOT,'..','.venv-docling',pythonExecutable)].find(existsSync);
}

export function documentPython():string {
 if(process.env.A4B_DOCUMENT_PYTHON)return resolve(PROJECT_ROOT,process.env.A4B_DOCUMENT_PYTHON);
 return [backendPath('.venv-document-analysis',pythonExecutable),agentPython(),doclingPython()].find((p):p is string=>!!p&&existsSync(p))||
  (process.platform==='win32'?'python':'python3');
}
