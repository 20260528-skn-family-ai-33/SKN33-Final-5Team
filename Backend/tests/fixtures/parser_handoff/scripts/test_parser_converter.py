"""Configuration regression without model downloads/inference."""
import os
import unittest
from unittest.mock import patch
from parser_converter import make_converter, parser_config, parser_metadata


class ParserConverterTests(unittest.TestCase):
    def settings(self, **values):
        return patch.dict(os.environ, values, clear=True)

    def test_default_standard_cpu_without_lighton(self):
        with self.settings(), patch('torch.cuda.is_available',return_value=False):
            config=parser_config()
            self.assertEqual(config['mode'],'standard')
            self.assertEqual(config['device'],'cpu')
            self.assertTrue(config['ocr'] and config['table_structure'])
            self.assertFalse(config['lighton_enabled'])

    def test_standard_options_are_actually_enabled(self):
        from docling.datamodel.base_models import InputFormat
        with self.settings(A4B_PDF_DEVICE='cpu'):
            converter=make_converter()
            options=converter.format_to_options[InputFormat.PDF].pipeline_options
            self.assertTrue(options.do_ocr and options.do_table_structure)
            self.assertEqual(options.ocr_options.lang,['ko','en'])
            self.assertFalse(options.ocr_options.download_enabled)
            self.assertEqual(options.accelerator_options.device.value,'cpu')

    def test_explicit_native_only(self):
        from docling.datamodel.base_models import InputFormat
        from docling.document_converter import NativePdfFormatOption
        with self.settings(A4B_PDF_PARSER_MODE='native',A4B_PDF_DEVICE='cpu'):
            config=parser_config()
            self.assertFalse(config['ocr'] or config['table_structure'])
            self.assertIsInstance(make_converter(config).format_to_options[InputFormat.PDF],NativePdfFormatOption)

    def test_bad_mode_and_device_are_rejected(self):
        for variable in ['A4B_PDF_DEVICE','A4B_PDF_PARSER_MODE']:
            with self.settings(**{variable:'typo'}),self.assertRaises(ValueError):parser_config()

    def test_unavailable_cuda_not_silently_used(self):
        with self.settings(A4B_PDF_DEVICE='cuda'),patch('torch.cuda.is_available',return_value=False),self.assertRaisesRegex(ValueError,'CUDA'):
            parser_config()

    def test_lighton_requires_cuda(self):
        with self.settings(A4B_PDF_DEVICE='cpu',PDF_LIGHTON_ENABLED='1'),self.assertRaisesRegex(ValueError,'LightOn'):
            parser_config()

    def test_native_and_lighton_conflict_is_explicit(self):
        with self.settings(A4B_PDF_DEVICE='cuda',A4B_PDF_PARSER_MODE='native',PDF_LIGHTON_ENABLED='1'),patch('torch.cuda.is_available',return_value=True),self.assertRaisesRegex(ValueError,'LightOn'):
            parser_config()

    def test_metadata_has_exact_code_hashes(self):
        with self.settings(A4B_PDF_DEVICE='cpu'):
            metadata=parser_metadata(parser_config())
            self.assertEqual(metadata['parser_pipeline_version'],'docling-structure-hybrid-v2')
            self.assertEqual(len(metadata['parser_code_sha256']),7)
            self.assertTrue(all(len(x)==64 for x in metadata['parser_code_sha256'].values()))


if __name__=='__main__':unittest.main()
