import http.server
import importlib.util
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

root = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('transfer_tls_fixture', root / '__init__.py', submodule_search_locations=[str(root)])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
core = sys.modules['transfer_tls_fixture.core']

@unittest.skipUnless(shutil.which('openssl'), 'openssl required for an isolated TLS fixture')
class LocalTusTests(unittest.TestCase):
    def test_local_tls_upload(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as tmp:
            folder = Path(tmp)
            key, cert = folder / 'fixture-key.pem', folder / 'fixture-cert.pem'
            subprocess.run([shutil.which('openssl'), 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost', '-keyout', str(key), '-out', str(cert)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=True, env={'PATH': os.defpath})
            class Handler(http.server.BaseHTTPRequestHandler):
                data = b''
                total = 1500
                def do_HEAD(self):
                    self.send_response(200)
                    self.send_header('Tus-Resumable', '1.0.0')
                    self.send_header('Upload-Offset', str(len(type(self).data)))
                    self.send_header('Upload-Length', str(type(self).total))
                    self.end_headers()
                def do_PATCH(self):
                    if int(self.headers['Upload-Offset']) != len(type(self).data):
                        self.send_error(409)
                        return
                    type(self).data += self.rfile.read(int(self.headers['Content-Length']))
                    self.send_response(204)
                    self.send_header('Tus-Resumable', '1.0.0')
                    self.send_header('Upload-Offset', str(len(type(self).data)))
                    self.end_headers()
                def log_message(self, *args): pass
            server = http.server.HTTPServer(('localhost', 0), Handler)
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.load_cert_chain(str(cert), str(key))
            server.socket = tls.wrap_socket(server.socket, server_side=True)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                context = ssl.create_default_context(cafile=str(cert))
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context), core.NoRedirect())
                path = folder / 'payload.bin'
                path.write_bytes(b'a' * Handler.total)
                url = f'https://localhost:{server.server_port}/files/fixture'
                queue = module.Uploads(folder / 'queue')
                job = queue.create_upload(str(path), url)['job_id']
                with patch.object(core, 'open_request', side_effect=lambda req: opener.open(req, timeout=5)), patch.object(module.upload.time, 'sleep'):
                    preflight = queue.upload_preflight(job, url)
                    self.assertEqual(preflight['status'], 'ready')
                    self.assertEqual(Handler.data, b'')
                    queue.run_upload(job, url, 1024)
                    result = queue.run_upload(job, url, 1024)
                self.assertEqual(result['status'], 'accepted')
                self.assertEqual(Handler.data, path.read_bytes())
                self.assertFalse(result['remote_checksum_verified'])
            finally:
                server.shutdown()
                worker.join(timeout=5)
                server.server_close()

if __name__ == '__main__': unittest.main()
