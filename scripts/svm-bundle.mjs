// npm commands load the root .env using Node's existing env-file support.
// Never print the environment or invoke a network/model server.
import { existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const configured = process.env.A4B_AGENT_PYTHON;
const python = configured
  ? (path.isAbsolute(configured) ? configured : path.resolve(root, configured))
  : path.join(root, 'Backend', '.venv-agent', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
if (!existsSync(python)) {
  console.error(JSON.stringify({ ok: false, status: 'python_missing', python,
    next_action: 'npm run setup으로 서비스 Python 환경을 준비하거나 A4B_AGENT_PYTHON에 실제 서비스 Python 경로를 지정하세요.' }));
  process.exitCode = 1;
} else {
  const backend = path.join(root, 'Backend');
  const result = spawnSync(python, ['-m', 'ml.svm56.bundle_cli', ...process.argv.slice(2)], {
    cwd: root, stdio: 'inherit', shell: false,
    env: { ...process.env, PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1',
      PYTHONPATH: [backend, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter) },
  });
  if (result.error) console.error(JSON.stringify({ ok: false, status: 'python_failed', code: result.error.code }));
  process.exitCode = result.status ?? 1;
}
