import {rm} from 'node:fs/promises';
import type {ChildProcess} from 'node:child_process';

// Test helpers that behave the same on macOS, Linux and Windows.

// Starting server/index.ts through tsx takes about 20-25 s on Windows (tsx resolves the
// @langchain packages far slower there) versus a few seconds on macOS. Server start/stop limits
// in tests are multiplied on Windows; the macOS values stay as written in each test.
export const PLATFORM_SLOWDOWN=process.platform==='win32'?10:1;
export const scaled=(milliseconds:number)=>milliseconds*PLATFORM_SLOWDOWN;

// Spawned servers get an IPC channel so Windows can request a graceful stop.
export const SERVER_STDIO:['ignore','pipe','pipe','ipc']=['ignore','pipe','pipe','ipc'];

// Windows has no SIGTERM: child.kill('SIGTERM') terminates the process immediately and skips
// the database shutdown. On Windows the same shutdown() runs through an IPC request instead.
export function requestShutdown(child:ChildProcess){
 if(process.platform==='win32'&&child.connected)child.send('shutdown');
 else child.kill('SIGTERM');
}

// Windows keeps a directory locked for a moment after a process using it exits (EBUSY/EPERM).
export function removeDirectory(path:string){
 return rm(path,{recursive:true,force:true,maxRetries:20,retryDelay:100});
}
