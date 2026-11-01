"""File parser boundary without importing optional document packages eagerly."""
from pathlib import Path
from zipfile import BadZipFile
from xml.etree.ElementTree import ParseError
import zlib

class DocumentParseError(ValueError):
    def __init__(self,file_format,exception_type):
        super().__init__("Document could not be parsed")
        self.file_format=file_format;self.exception_type=exception_type

def is_parser_exception(error):
    if isinstance(error,(BadZipFile,zlib.error,ParseError)):return True
    # Match known base classes in the MRO, including subclasses such as PdfStreamError.
    # Those packages are optional; their modules need not exist to list/run other tools.
    bases={('pypdf.errors','PdfReadError'),('pypdf.errors','ParseError'),
           ('pypdf.errors','EmptyImageDataError'),('pypdf.errors','XmpDocumentError'),
           ('pypdf.errors','LimitReachedError'),('docx.opc.exceptions','OpcError'),
           ('docx.oxml.exceptions','InvalidXmlError'),('lxml.etree','XMLSyntaxError'),
           ('openpyxl.utils.exceptions','InvalidFileException')}
    return any((cls.__module__,cls.__name__) in bases for cls in type(error).__mro__)

def parse_file(reader,file_format,source,**options):
    """Only the parser load stage is wrapped; later user parameter errors stay distinct."""
    # python-docx otherwise turns a missing file into PackageNotFoundError.
    p=Path(source);p.stat()
    if p.is_dir():raise IsADirectoryError("Expected a document file")
    try:return reader(source,**options)
    except (OSError,ImportError,MemoryError):raise
    except Exception as error:
        # Includes malformed package XML and missing ZIP members in an existing file.
        raise DocumentParseError(file_format,type(error).__name__) from error

def argument_file_format(arguments):
    for key in ('source_format','format'):
        value=arguments.get(key)
        if isinstance(value,str) and value.lower() in {'docx','xlsx','pdf'}:return value.lower()
    for key in ('asset','source','workbook','document','reference'):
        value=arguments.get(key)
        if isinstance(value,dict):value=value.get('path')
        if isinstance(value,str) and Path(value).suffix.lower() in {'.docx','.xlsx','.pdf'}:
            return Path(value).suffix.lower()[1:]
    return None
