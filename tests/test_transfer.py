import hashlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('transfer_core', Path(__file__).parents[1] / 'core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

class Response(io.BytesIO):
    def __init__(self, data, status, headers):
        super().__init__(data)
        self.status = status
        self.headers = headers

class Clock:
    def __init__(self): self.now = 0.0
    def monotonic(self): return self.now
    def sleep(self, seconds): self.now += seconds

class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/tmp')
        self.root = Path(self.tmp.name)
        self.queue = core.Queue(self.root)
        self.url = 'https://example.com/data'
        self.data = b'a' * 2048
        self.job = self.queue.create_download(self.url, hashlib.sha256(self.data).hexdigest())['job_id']

    def tearDown(self): self.tmp.cleanup()

    def test_resume_and_verify(self):
        clock = Clock()
        responses = [Response(self.data, 200, {'Content-Length': '2048', 'ETag': '"one"'}), Response(self.data[1024:], 206, {'Content-Range': 'bytes 1024-2047/2048', 'ETag': '"one"'})]
        with patch.object(core, 'open_request', side_effect=responses) as request, patch.object(core.time, 'monotonic', clock.monotonic), patch.object(core.time, 'sleep', clock.sleep):
            first = self.queue.run_download(self.job, self.url, 1024, 1)
            self.assertEqual(first['received_bytes'], 1024)
            second = self.queue.run_download(self.job, self.url, 1024, 2)
            self.assertEqual(second['status'], 'verified')
            self.assertEqual(Path(second['output_path']).read_bytes(), self.data)
            self.assertEqual(request.call_args.args[0].get_header('Range'), 'bytes=1024-')

    def test_checksum_mismatch_and_url_validation(self):
        response = Response(b'b' * 2048, 200, {'Content-Length': '2048', 'ETag': '"one"'})
        clock = Clock()
        with patch.object(core, 'open_request', return_value=response), patch.object(core.time, 'monotonic', clock.monotonic), patch.object(core.time, 'sleep', clock.sleep):
            result = self.queue.run_download(self.job, self.url, 2048, 2)
        self.assertEqual(result['status'], 'checksum_mismatch')
        self.assertIsNone(result['output_path'])
        with self.assertRaises(ValueError): self.queue.create_download('https://example.com/file?token=not-a-real-token', 'a' * 64)

    def test_new_job_and_download_are_private_with_permissive_umask(self):
        previous = os.umask(0)
        try:
            job = self.queue.create_download(self.url, hashlib.sha256(self.data).hexdigest())['job_id']
            meta, part, final = self.queue.paths(job)
            self.assertEqual(meta.stat().st_mode & 0o777, 0o600)
            clock = Clock()
            with patch.object(core, 'open_request', return_value=Response(self.data, 200, {'Content-Length':'2048','ETag':'"one"'})), patch.object(core.time, 'monotonic', clock.monotonic), patch.object(core.time, 'sleep', clock.sleep):
                result = self.queue.run_download(job, self.url, 2048, 2)
            self.assertEqual(result['status'], 'verified')
            self.assertEqual(meta.stat().st_mode & 0o777, 0o600)
            self.assertEqual(final.stat().st_mode & 0o777, 0o600)
        finally:
            os.umask(previous)

    def test_recover_completed_partial(self):
        meta, part, final = self.queue.paths(self.job)
        state = __import__('json').loads(meta.read_text())
        state['total'] = len(self.data)
        meta.write_text(__import__('json').dumps(state))
        part.write_bytes(self.data)
        result = self.queue.run_download(self.job, self.url, 1024)
        self.assertEqual(result['status'], 'verified')
        self.assertTrue(final.exists())

    def test_list_recovers_jobs_without_disclosing_sources(self):
        upload_id = 'a' * 32
        (self.root / (upload_id + '.upload.json')).write_text('{"status":"queued","source":"sensitive-local-path"}')
        result = self.queue.list_jobs()
        jobs = {x['job_id']: x['direction'] for x in result['jobs']}
        self.assertEqual(jobs[self.job], 'download')
        self.assertEqual(jobs[upload_id], 'upload')
        self.assertNotIn('sensitive-local-path', str(result))

if __name__ == '__main__':
    unittest.main()
