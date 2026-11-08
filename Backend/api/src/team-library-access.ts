import {AsyncLocalStorage} from 'node:async_hooks';

// Requests recheck central membership immediately before every Python worker.
const libraryAccess=new AsyncLocalStorage<()=>Promise<unknown>>();
export const withTeamLibraryAccess=<T>(authorize:()=>Promise<unknown>,action:()=>T):T=>libraryAccess.run(authorize,action);
export async function assertTeamLibraryAccess(){await libraryAccess.getStore()?.();}
