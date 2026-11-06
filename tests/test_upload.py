import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

root = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('transfer_package_fixture', root / '__init__.py', submodule_search_locations=[str(root)])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
core = sys.modules['transfer_package_fixture.core']

class Response(io.BytesIO):
    def __init__(self, status, headers):
        super().__init__(b'')
        self.status = status
        self.headers = headers

class FakeTus:
    def __init__(self, length):
        self.data = b''
        self.length = length
        self.fail_after_accept = False
        self.patch_count = 0
    def __call__(self, request):
        if request.method == 'HEAD':
            return Response(200, {'Tus-Resumable': '1.0.0', 'Upload-Offset': str(len(self.data)), 'Upload-Length': str(self.length)})
        assert request.method == 'PATCH'
        assert int(request.get_header('Upload-offset')) == len(self.data)
        self.patch_count += 1
        self.data += request.data
        if self.fail_after_accept:
            self.fail_after_accept = False
            raise TimeoutError()
        return Response(204, {'Tus-Resumable': '1.0.0', 'Upload-Offset': str(len(self.data))})

class UploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/tmp')
        self.root = Path(self.tmp.name)
        self.source = self.root / 'original.bin'
        self.source.write_bytes(b'a' * 1500)
        self.url = 'https://example.com/upload/resource'
        self.queue = module.Uploads(self.root / 'queue')
        self.job = self.queue.create_upload(str(self.source), self.url)['job_id']
        self.server = FakeTus(1500)

    def tearDown(self): self.tmp.cleanup()

    def test_upload_in_small_serialized_chunks(self):
        with patch.object(core, 'open_request', side_effect=self.server), patch.object(module.upload.time, 'sleep'):
            first = self.queue.run_upload(self.job, self.url, 1024)
            self.assertEqual(first['acknowledged_bytes'], 1024)
            second = self.queue.run_upload(self.job, self.url, 1024)
        self.assertEqual(second['status'], 'accepted')
        self.assertFalse(second['remote_checksum_verified'])
        self.assertEqual(self.server.data, self.source.read_bytes())

    def test_unknown_response_requires_explicit_reconciliation(self):
        self.server.fail_after_accept = True
        with patch.object(core, 'open_request', side_effect=self.server), patch.object(module.upload.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'unknown'):
                self.queue.run_upload(self.job, self.url, 1024)
            self.assertEqual(self.queue.upload_status(self.job)['status'], 'outcome_unknown')
            reconciled = self.queue.run_upload(self.job, self.url, 1024)
            self.assertEqual(reconciled['status'], 'reconciled')
            self.assertEqual(self.server.patch_count, 1)
            final = self.queue.run_upload(self.job, self.url, 1024)
            self.assertEqual(final['status'], 'accepted')
            self.assertEqual(self.server.patch_count, 2)

    def test_active_profile_media_requires_explicit_opt_in(self):
        private = self.root/'.hermes'
        source_dir = private/'plugin-data'/'tutorial'
        source_dir.mkdir(parents=True)
        media = source_dir/'demo.mp4'
        media.write_bytes(b'media fixture')
        queue = module.Uploads(private/'plugin-data'/'transfer-queue')
        with self.assertRaisesRegex(ValueError, 'private configuration'):
            queue.create_upload(str(media), self.url)
        job = queue.create_upload(str(media), self.url, allow_profile_media=True)
        self.assertEqual(job['source_bytes'], len(b'media fixture'))
        unrelated = private/'unrelated.mp4'
        unrelated.write_bytes(b'media fixture')
        with self.assertRaisesRegex(ValueError, 'private configuration'):
            queue.create_upload(str(unrelated), self.url, allow_profile_media=True)
        non_media = source_dir/'secret.json'
        non_media.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'private configuration'):
            queue.create_upload(str(non_media), self.url, allow_profile_media=True)

    def test_custom_profile_configuration_stays_blocked(self):
        profile = self.root/'custom-profile'
        profile.mkdir()
        source = profile/'unrelated.mp4'
        source.write_bytes(b'media fixture')
        queue = module.Uploads(profile/'plugin-data'/'transfer-queue')
        with self.assertRaisesRegex(ValueError, 'private configuration'):
            queue.create_upload(str(source), self.url, allow_profile_media=True)

    def test_changed_source_and_remote_offset_rejected(self):
        self.source.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.queue.run_upload(self.job, self.url, 1024)
        other = self.root / 'other.bin'
        other.write_bytes(b'a' * 1500)
        job = self.queue.create_upload(str(other), self.url)['job_id']
        self.server.data = b'x'
        with patch.object(core, 'open_request', side_effect=self.server):
            with self.assertRaisesRegex(ValueError, 'independently'):
                self.queue.run_upload(job, self.url, 1024)

if __name__ == '__main__': unittest.main()
