// Run after npm run build; writes a QA page into the local build only.
import {build} from 'esbuild';
import {readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../',import.meta.url));
await build({entryPoints:[root+'scripts/visual-theme-review.tsx'],outfile:root+'dist-app/visual-theme-review.js',bundle:true,format:'esm',platform:'browser',jsx:'automatic',alias:{'@':root},logLevel:'warning'});
const index=await readFile(root+'dist-app/index.html','utf8');
const css=index.match(/href="([^\"]+\.css)"/)[1];
await writeFile(root+'dist-app/visual-theme-review.html',`<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>A4B local visual QA</title><link rel="stylesheet" href="${css}"><style>.qa-page{max-width:1100px;margin:auto;padding:24px}.qa-page>h1{font-size:24px}.qa-page>p{margin:12px 0 24px}.qa-section{margin:24px 0;padding:24px;border:1px solid var(--border);border-radius:12px;min-width:0}.qa-section>h2{font-size:18px;margin-bottom:18px}.qa-row{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:16px 0}.qa-row label{display:flex;align-items:center;gap:8px}.qa-fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px}.qa-fields label{display:grid;gap:8px}.qa-section>.as-main{padding:0}@media(max-width:480px){.qa-page{padding:12px}.qa-section{padding:14px}}</style></head><body><div id="root"></div><script type="module" src="/visual-theme-review.js"></script></body></html>`);
console.log('Local visual QA: /visual-theme-review.html');
