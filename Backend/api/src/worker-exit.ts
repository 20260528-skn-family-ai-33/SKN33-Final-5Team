import type {Worker} from 'node:worker_threads';

// On Windows, process.exit() while a worker thread is still shutting down aborts Node
// (libuv "Assertion failed: !(handle->flags & UV_HANDLE_CLOSING)", src\win\async.c).
// Workers are tracked until they have fully exited and server shutdown waits for them.

const running=new Set<Promise<void>>();

export function trackWorker(worker:Worker){
 const exited=new Promise<void>(resolve=>worker.once('exit',()=>resolve()));
 running.add(exited);void exited.then(()=>running.delete(exited));
}

export async function waitForWorkers(limitMs=3000){
 let timer:ReturnType<typeof setTimeout>|undefined;
 await Promise.race([Promise.allSettled([...running]),new Promise<void>(resolve=>{timer=setTimeout(resolve,limitMs);})]);
 if(timer)clearTimeout(timer);
}
