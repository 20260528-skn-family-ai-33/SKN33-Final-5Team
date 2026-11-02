import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .benchmark_baseline import run
from .contracts import AnalysisError


class Engines:
    def __init__(self, fail_docling=False):
        self.attempts = []
        self.fail_docling = fail_docling

    def run(self, engine, **kwargs):
        self.attempts.append(dict(id=str(len(self.attempts)), processor=engine, phase=kwargs['phase'], status='succeeded', elapsed_seconds=0))
        if engine == 'docling':
            if self.fail_docling:
                self.attempts[-1]['status'] = 'failed'
                raise AnalysisError('engine_error', 'fixture failure')
            return dict(version='test', blocks=[dict(kind='text', text='원문 00123', location={'page': 1})])
        return dict(version='test', outputs=[dict(source=str(image), result=dict(rec_texts=['원문00123', '추가 문자'], rec_scores=[1.0, 0.5]))
                                            for image in kwargs['sources']])


class BaselinePolicyTest(unittest.TestCase):
    def fixture(self, fail=False):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work = Path(tmp.name).resolve()
        source = work / 'original.pdf'
        source.write_bytes(b'fixture, not a real PDF')
        images = [work / 'page1.png', work / 'page2.png']
        engines = Engines(fail)
        with patch('document_analysis.benchmark_baseline.prepare', return_value=(source, images, [])):
            result = run(source, work, engines=engines)
        return result, engines

    def test_ocr_runs_on_every_page_even_with_complete_docling_text(self):
        result, engines = self.fixture()
        self.assertEqual([a['processor'] for a in engines.attempts], ['docling', 'paddleocr'])
        analysis = result['document_analysis']
        self.assertEqual(analysis['coverage']['rendered_pages'], 2)
        duplicates = [b for b in analysis['blocks'] if b['text'] == '원문00123' and b['native_location']['page'] == 1]
        self.assertEqual(len(duplicates), 1)
        self.assertFalse(duplicates[0]['selected'])
        self.assertIn('추가 문자', result['text'])

    def test_docling_failure_does_not_skip_paddle(self):
        result, engines = self.fixture(True)
        self.assertEqual([a['processor'] for a in engines.attempts], ['docling', 'paddleocr'])
        self.assertEqual(result['parser_status'], 'partial')
        self.assertIn('원문00123', result['text'])
        self.assertFalse(result['document_analysis']['coverage']['docling_succeeded'])


if __name__ == '__main__':
    unittest.main()
