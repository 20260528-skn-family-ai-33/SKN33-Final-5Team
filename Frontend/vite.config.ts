import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {fileURLToPath} from 'node:url';
export default defineConfig({
 plugins:[react()],
 resolve:{alias:{'@':fileURLToPath(new URL('.',import.meta.url))}},
 build:{outDir:'dist-app',emptyOutDir:true},
 server:{host:'127.0.0.1',proxy:{'/api':{
  target:'http://127.0.0.1:3030',changeOrigin:true,
  configure(proxy){
   proxy.on('proxyReq',(upstream,request)=>{
    // Match only same-origin loopback requests; foreign origins must still be rejected.
    const host=request.headers.host||'';
    if(/^(?:127\.0\.0\.1|localhost|\[::1\])(?::\d+)?$/.test(host)&&request.headers.origin===`http://${host}`){
     upstream.setHeader('origin',`http://${upstream.getHeader('host')}`);
    }
   });
  },
 }}},
});
