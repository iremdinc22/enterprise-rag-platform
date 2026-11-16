"""Bounded multipart/PDF preparation and no-follow immutable upload storage."""
from dataclasses import dataclass
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import stat
import subprocess
import sys
import unicodedata


class UploadRejected(ValueError):
    def __init__(self, status=422, detail="Invalid PDF upload"):
        super().__init__(detail)
        self.status, self.detail = status, detail


def safe_filename(value):
    if not isinstance(value, str): raise UploadRejected()
    try:
        valid = (1 <= len(value.encode('utf-8')) <= 180
            and value not in ('.', '..') and not any(c in value for c in '/\\:')
            and not any(unicodedata.category(c).startswith('C') for c in value)
            and value.lower().endswith('.pdf'))
    except UnicodeError:
        valid = False
    if not valid:
        raise UploadRejected()
    return value


def parse_multipart(body, content_type, max_file_bytes):
    from python_multipart import MultipartParser
    from python_multipart.multipart import parse_options_header
    try:
        media, options = parse_options_header(content_type.encode('latin-1'))
        boundary = options.get(b'boundary', b'')
        if media != b'multipart/form-data' or not 1 <= len(boundary) <= 70:
            raise UploadRejected(415, "Expected one multipart PDF file")
        if any(c < 33 or c > 126 for c in boundary):
            raise UploadRejected()
        headers = {}; field = bytearray(); value = bytearray(); data = bytearray()
        count = 0; ended = False; filename = None; header_bytes = 0
        def begin():
            nonlocal count
            count += 1
            if count != 1: raise UploadRejected()
        def header_field(raw, start, end):
            nonlocal header_bytes
            header_bytes += end-start
            if header_bytes > 65536: raise UploadRejected(413, "Multipart headers too large")
            field.extend(raw[start:end])
        def header_value(raw, start, end):
            nonlocal header_bytes
            header_bytes += end-start
            if header_bytes > 65536: raise UploadRejected(413, "Multipart headers too large")
            value.extend(raw[start:end])
        def header_end():
            key = bytes(field).lower()
            if key in headers: raise UploadRejected()
            headers[key] = bytes(value); field.clear(); value.clear()
        def headers_finished():
            nonlocal filename
            disposition_header = headers.get(b'content-disposition', b'')
            # python-multipart strips IE-style Windows paths; reject before it can normalize.
            if b'\\' in disposition_header: raise UploadRejected()
            disposition, params = parse_options_header(disposition_header)
            if disposition != b'form-data' or params.get(b'name') != b'file' or b'filename' not in params:
                raise UploadRejected()
            if headers.get(b'content-type', b'').lower() != b'application/pdf':
                raise UploadRejected(415, "Expected one multipart PDF file")
            if b'content-transfer-encoding' in headers: raise UploadRejected()
            filename = safe_filename(params[b'filename'].decode('utf-8'))
        def part_data(raw, start, end):
            if len(data)+end-start > max_file_bytes:
                raise UploadRejected(413, "PDF file too large")
            data.extend(raw[start:end])
        def finish():
            nonlocal ended
            ended = True
        parser = MultipartParser(boundary, {'on_part_begin': begin, 'on_header_field': header_field,
            'on_header_value': header_value, 'on_header_end': header_end,
            'on_headers_finished': headers_finished, 'on_part_data': part_data, 'on_end': finish})
        parser.logger = logging.Logger('demo.multipart', level=logging.CRITICAL+1)
        parser.write(body); parser.finalize()
        if not ended or count != 1 or filename is None or not data: raise UploadRejected()
        return filename, bytes(data)
    except UploadRejected:
        raise
    except Exception:
        raise UploadRejected() from None


def inspect_pdf(data, max_pages=10, max_chunks=200, max_text=100000):
    from pypdf import PdfReader
    from src.ingestion.chunker import chunk_text
    try:
        if not data.startswith(b'%PDF-') or b'%%EOF' not in data[-1024:]: raise UploadRejected()
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted: raise UploadRejected()
        pages = len(reader.pages)
        if not 1 <= pages <= max_pages: raise UploadRejected(413, "PDF page limit exceeded")
        text_count = chunks = 0
        for page in reader.pages:
            text = page.extract_text() or ''
            text_count += len(text)
            if text_count > max_text: raise UploadRejected(413, "PDF text limit exceeded")
            chunks += len(chunk_text(text))
            if chunks > max_chunks: raise UploadRejected(413, "PDF chunk limit exceeded")
        if chunks == 0: raise UploadRejected()
        return {'page_count': pages, 'chunk_count': chunks, 'text_characters': text_count}
    except UploadRejected:
        raise
    except Exception:
        raise UploadRejected() from None


def validate_pdf(data, max_pages=10, max_chunks=200):
    # Untrusted PDF extraction runs in a bounded child, not the API/worker process.
    try:
        result = subprocess.run([sys.executable, '-B', '-m', 'src.demo.upload_files',
            str(max_pages), str(max_chunks)], input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10,
            env={"PYTHON_DOTENV_DISABLED":"1", "PYTHONDONTWRITEBYTECODE":"1"})
        response = json.loads(result.stdout)
        if result.returncode:
            raise UploadRejected(response.get('status', 422), response.get('detail', 'Invalid PDF upload'))
        return response
    except UploadRejected:
        raise
    except Exception:
        raise UploadRejected(422, "PDF validation failed") from None


def open_directory(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts: raise UploadRejected()
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = next_fd
        return fd
    except BaseException:
        os.close(fd); raise


@dataclass
class StoredFile:
    tenant_fd: int
    document_fd: int
    document_id: str
    filename: str

    def discard(self):
        os.unlink(self.filename, dir_fd=self.document_fd)
        os.rmdir(self.document_id, dir_fd=self.tenant_fd)

    def close(self):
        os.close(self.document_fd); os.close(self.tenant_fd)


class UploadStorage:
    def __init__(self, root): self.root = Path(root)

    def store(self, tenant_id, document_id, filename, data):
        from src.identifiers import validate_identifier
        validate_identifier(tenant_id); validate_identifier(document_id); safe_filename(filename)
        root_fd = open_directory(self.root); tenant_fd = document_fd = None; created = False
        try:
            try: os.mkdir(tenant_id, 0o700, dir_fd=root_fd)
            except FileExistsError: pass
            tenant_fd = os.open(tenant_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            os.mkdir(document_id, 0o700, dir_fd=tenant_fd); created = True
            document_fd = os.open(document_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=tenant_fd)
            fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=document_fd)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.fsync(document_fd); os.fsync(tenant_fd); os.fsync(root_fd)
            result = StoredFile(tenant_fd, document_fd, document_id, filename)
            tenant_fd = document_fd = None
            return result
        except BaseException:
            if created and document_fd is not None:
                try: os.unlink(filename, dir_fd=document_fd)
                except FileNotFoundError: pass
                os.rmdir(document_id, dir_fd=tenant_fd)
            raise
        finally:
            os.close(root_fd)
            if document_fd is not None: os.close(document_fd)
            if tenant_fd is not None: os.close(tenant_fd)

    def read(self, record, max_bytes):
        from src.identifiers import validate_identifier
        validate_identifier(record['tenant_id']); validate_identifier(record['document_id'])
        safe_filename(record['filename'])
        fd = open_directory(self.root / record['tenant_id'] / record['document_id'])
        try:
            source = os.open(record['filename'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(source, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode): raise UploadRejected()
                data = stream.read(max_bytes+1)
                if not data or len(data)>max_bytes: raise UploadRejected()
                return data
        finally: os.close(fd)


if __name__ == '__main__':
    try:
        import resource
        logging.disable(logging.CRITICAL)
        if sys.platform.startswith('linux'):
            resource.setrlimit(resource.RLIMIT_AS, (512*1024*1024, 512*1024*1024))
        elif sys.platform != 'darwin':
            raise ValueError('Unsupported PDF validation platform')
        # Darwin does not support this address-space cap; CPU/wall limits remain.
        resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
        limits = [int(value) for value in sys.argv[1:]]
        result = inspect_pdf(sys.stdin.buffer.read(5*1024*1024+1), *limits)
        print(json.dumps(result))
    except UploadRejected as error:
        print(json.dumps({'status': error.status, 'detail': error.detail})); raise SystemExit(2)
    except Exception:
        print(json.dumps({'status':422,'detail':'PDF validation failed'})); raise SystemExit(2)
