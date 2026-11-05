"""Exercise Git release selection, exact artifact bytes, and the EC2 cutover gate."""
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
MODEL = 'runtime/ml-bundle/model/recommender_char_svm_5431.joblib'
CATALOG = 'runtime/ml-bundle/catalog/catalog_56.json'
SELECTOR = ROOT / 'scripts/deploy/a4b-deploy-targets.py'


def pinned_hashes():
    module = ast.parse((ROOT / 'Backend/ml/svm56/svm56_service_hook.py').read_text())
    values = {node.targets[0].id: ast.literal_eval(node.value) for node in module.body
              if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
              and node.targets[0].id in ('MODEL_SHA256', 'CATALOG_SHA256')}
    return {MODEL: values['MODEL_SHA256'], CATALOG: values['CATALOG_SHA256']}


class ReleaseSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='a4b-svm-release-')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'
        self.repo.mkdir()
        self.env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}
        self.git('init', '-q', '--initial-branch=main')
        self.git('config', 'user.name', 'A4B release test')
        self.git('config', 'user.email', 'test@example.invalid')
        for path in (MODEL, CATALOG, 'Frontend/app.ts', 'docs/note.md'):
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('baseline\n')
        self.base = self.commit()

    def git(self, *args):
        return subprocess.check_output(['git', '-c', 'core.hooksPath=' + os.devnull, *args],
                                       cwd=self.repo, env=self.env, stderr=subprocess.PIPE).decode().strip()

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'release fixture')
        return self.git('rev-parse', 'HEAD')

    def select(self, requested, frontend=None, backend=None):
        return subprocess.check_output([sys.executable, str(SELECTOR), requested,
                                        frontend or self.base, backend or self.base],
                                       cwd=self.repo, env=self.env, text=True).strip()

    def test_model_and_catalog_changes_deploy_backend(self):
        for path in (MODEL, CATALOG):
            with self.subTest(path=path):
                (self.repo / path).write_text('new artifact\n')
                revision = self.commit()
                self.assertEqual(self.select(revision), 'backend')
                self.assertEqual(self.select(revision, backend=revision), 'none')

    def test_frontend_only_does_not_restart_backend(self):
        (self.repo / 'Frontend/app.ts').write_text('new frontend\n')
        self.assertEqual(self.select(self.commit()), 'frontend')

    def test_mixed_release_tracks_each_live_revision(self):
        (self.repo / MODEL).write_text('new model\n')
        (self.repo / 'Frontend/app.ts').write_text('new frontend\n')
        revision = self.commit()
        self.assertEqual(self.select(revision), 'all')
        self.assertEqual(self.select(revision, frontend=revision), 'backend')
        self.assertEqual(self.select(revision, backend=revision), 'frontend')

    def test_document_only_change_has_no_deployment(self):
        (self.repo / 'docs/note.md').write_text('updated docs\n')
        self.assertEqual(self.select(self.commit()), 'none')

    def test_attributes_change_rebuilds_images(self):
        shutil.copyfile(ROOT / '.gitattributes', self.repo / '.gitattributes')
        self.assertEqual(self.select(self.commit()), 'all')

    def test_fresh_checkout_preserves_pinned_model_and_catalog(self):
        shutil.copyfile(ROOT / '.gitattributes', self.repo / '.gitattributes')
        for path in (MODEL, CATALOG):
            shutil.copyfile(ROOT / path, self.repo / path)
        self.git('config', 'core.autocrlf', 'true')
        self.commit()
        checkout = Path(self.temp.name) / 'checkout'
        self.git('-c', 'core.autocrlf=true', 'clone', '-q', '--no-hardlinks', str(self.repo), str(checkout))
        for path, expected in pinned_hashes().items():
            with self.subTest(path=path):
                self.assertEqual(hashlib.sha256((checkout / path).read_bytes()).hexdigest(), expected)


class PreflightTests(unittest.TestCase):
    def test_bad_catalog_is_rejected_before_loading_model(self):
        with tempfile.TemporaryDirectory(prefix='a4b-bad-catalog-') as folder:
            catalog = Path(folder) / 'catalog.json'
            catalog.write_text('[]')
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(('A4B_SVM_', 'A4B_TOOL_ADVICE_'))}
            env['A4B_SVM_CATALOG_PATH'] = str(catalog)
            result = subprocess.run([sys.executable, '-m', 'ml.svm56.bundle_cli', 'check', '--probe'],
                                    cwd=ROOT / 'Backend', env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'hash_mismatch')

    @unittest.skipUnless(sys.platform == 'linux', 'EC2 release commands require Linux')
    def test_failed_svm_keeps_running_container_and_rollback_works(self):
        with tempfile.TemporaryDirectory(prefix='a4b-ec2-guards-') as folder:
            subprocess.run(['bash', str(ROOT / 'Backend/tests/infrastructure/ec2_storage_cutover.sh'),
                            str(ROOT / 'scripts/deploy/deploy-a4b-ec2.sh'), folder], check=True)


if __name__ == '__main__':
    unittest.main()
