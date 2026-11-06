import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

root = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('transfer_dashboard_fixture', root/'dashboard'/'plugin_api.py')
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)

class DashboardTests(unittest.TestCase):
    def test_create_and_reconnect_without_network(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as tmp:
            root = Path(tmp)
            source = root/'original.bin'
            source.write_bytes(b'fixture')
            app = FastAPI()
            app.include_router(dashboard.router)
            client = TestClient(app)
            with patch('plugins.plugin_storage.plugin_data_dir', return_value=root/'plugin-data'):
                response = client.post('/action', json={'operation':'transfer_create_upload','arguments':{'source_path':str(source),'url':'https://example.com/upload'}})
                self.assertEqual(response.status_code, 200)
                job = response.json()['job_id']
                status = client.post('/action', json={'operation':'transfer_upload_status','arguments':{'job_id':job}})
                self.assertEqual(status.status_code, 200)
                self.assertEqual(status.json()['status'], 'queued')
                self.assertEqual(status.json()['acknowledged_bytes'], 0)
                self.assertEqual(client.post('/action', json={'operation':'not_allowed','arguments':{}}).status_code, 400)
                self.assertEqual(source.read_bytes(), b'fixture')
            private = root/'.hermes'/'plugin-data'
            media = private/'tutorial'/'demo.mp4'
            media.parent.mkdir(parents=True)
            media.write_bytes(b'media fixture')
            with patch('plugins.plugin_storage.plugin_data_dir', return_value=private/'transfer-queue'):
                args = {'source_path':str(media), 'url':'https://example.com/upload'}
                self.assertEqual(client.post('/action', json={'operation':'transfer_create_upload','arguments':args}).status_code, 400)
                args['allow_profile_media'] = True
                self.assertEqual(client.post('/action', json={'operation':'transfer_create_upload','arguments':args}).status_code, 200)

if __name__ == '__main__': unittest.main()
