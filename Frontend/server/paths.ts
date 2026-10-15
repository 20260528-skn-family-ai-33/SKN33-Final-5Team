import {existsSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

// Both server/ source files and the bundled build-server/ entry live in Frontend.
export const FRONTEND_ROOT=resolve(fileURLToPath(new URL('..',import.meta.url)));
export const PROJECT_ROOT=resolve(FRONTEND_ROOT,'..');
export const BACKEND_ROOT=resolve(PROJECT_ROOT,'Backend');
export const frontendPath=(...parts:string[])=>resolve(FRONTEND_ROOT,...parts);
export const backendPath=(...parts:string[])=>resolve(BACKEND_ROOT,...parts);
export const examplePath=(...parts:string[])=>resolve(PROJECT_ROOT,'docs','examples',...parts);
export const documentationPath=(...parts:string[])=>resolve(PROJECT_ROOT,'docs',...parts);
export const dataDirectory=(...parts:string[])=>resolve(PROJECT_ROOT,process.env.EASYFLOW_DATA_DIR||'data',...parts);
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
