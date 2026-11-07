"""Bounded tus 1.0 PATCH uploads to an explicitly supplied existing resource."""
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid
from . import core

PRIVATE = {'.ssh', '.gnupg', '.aws', '.azure', '.codex', '.docker', '.kube', '.pki', '.config', '.hermes'}


def source_file(value, profile_data=None, allow_profile_media=False):
    p = Path(value).expanduser()
    original = p.parts
    if p.is_symlink() or any(x.casefold() in PRIVATE - {'.hermes'} for x in original):
        raise ValueError('Private paths and symlink inputs are unsupported')
    p = p.resolve(strict=True)
    data_root = Path(profile_data).resolve() if profile_data is not None else None
    profile_home = data_root.parent if data_root is not None and data_root.name.casefold() == 'plugin-data' else None
    allowed = (allow_profile_media and profile_home is not None and p.is_relative_to(data_root) and
               p.suffix.lower() in ('.mp4', '.mkv', '.mov', '.webm', '.m4b', '.wav', '.srt', '.png'))
    protected = any(x.casefold() == '.hermes' for x in original) or any(x.casefold() == '.hermes' for x in p.parts) or (profile_home is not None and p.is_relative_to(profile_home))
    if (any(x.casefold() in PRIVATE - {'.hermes'} for x in p.parts) or protected and not allowed or not p.is_file()):
        raise ValueError('Choose a file outside private configuration or explicitly allow active-profile plugin media')
    if p.stat().st_size <= 0 or p.stat().st_size > core.MAX_BYTES: raise ValueError('Upload size outside supported range')
    return p


def identity(path):
    stat = path.stat()
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]


def response_offset(response, total, require_length=False):
    if response.headers.get('Tus-Resumable') != '1.0.0': raise ValueError('Server did not confirm tus 1.0.0')
    length, offset = response.headers.get('Upload-Length'), response.headers.get('Upload-Offset', '')
    if (require_length and length is None) or (length is not None and (not length.isdigit() or int(length) != total)): raise ValueError('Remote upload length differs from the local file')
    if not offset.isdigit() or int(offset) > total: raise ValueError('Server returned an invalid upload offset')
    return int(offset)


class Uploads(core.Queue):
    def upload_path(self, job_id):
        if not isinstance(job_id, str) or len(job_id) != 32 or any(c not in '0123456789abcdef' for c in job_id): raise ValueError('Invalid job ID')
        path = self.root / (job_id + '.upload.json')
        if path.is_symlink(): raise ValueError('Invalid upload path')
        return path

    def create_upload(self, source_path, url, allow_profile_media=False):
        if type(allow_profile_media) is not bool: raise ValueError('allow_profile_media must be boolean')
        url_fingerprint = core.url_hash(url)
        with self.lock():
            path = source_file(source_path, self.root.parent, allow_profile_media)
            before = identity(path)
            sha = core.file_hash(path)
            if identity(path) != before: raise ValueError('Source changed during hashing')
            job_id = uuid.uuid4().hex
            self.save(self.upload_path(job_id), {'source': str(path), 'identity': before, 'sha256': sha,
                'url_hash': url_fingerprint, 'profile_media_allowed': allow_profile_media, 'offset': 0, 'pending': None, 'status': 'queued'})
            return {'job_id': job_id, 'source_bytes': before[2], 'source_sha256': sha, 'status': 'queued'}

    def upload_status(self, job_id):
        with self.lock():
            state = json.loads(self.upload_path(job_id).read_text())
            return {'job_id': job_id, 'status': state['status'], 'acknowledged_bytes': state['offset'],
                    'total_bytes': state['identity'][2], 'uncertain_chunk_bytes': state['pending'],
                    'notice': 'Remote offset acknowledgment is not a server-side content checksum.'}

    def upload_preflight(self, job_id, url):
        fingerprint = core.url_hash(url)
        with self.lock():
            state = json.loads(self.upload_path(job_id).read_text())
            if state['url_hash'] != fingerprint: raise ValueError('URL differs from the original job')
            source = source_file(state['source'], self.root.parent, bool(state.get('profile_media_allowed')))
            if identity(source) != state['identity']: raise ValueError('Source changed; create a new upload job')
            total = state['identity'][2]
            request = urllib.request.Request(url, headers={'Tus-Resumable': '1.0.0', 'Cache-Control': 'no-store'}, method='HEAD')
            try:
                with core.open_request(request) as response:
                    if response.status not in (200, 204): raise ValueError('Unexpected tus HEAD status')
                    remote = response_offset(response, total, require_length=True)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise RuntimeError('Upload preflight could not read the remote offset') from exc
            if identity(source) != state['identity']: raise ValueError('Source changed during preflight')
            pending = state['pending']
            if pending is not None:
                status = 'reconciliation_needed' if remote in (state['offset'], state['offset'] + pending) else 'offset_conflict'
            elif remote != state['offset']:
                status = 'offset_conflict'
            else:
                status = 'complete_offset' if remote == total else 'ready'
            return {'job_id': job_id, 'status': status, 'remote_offset': remote, 'total_bytes': total,
                    'remaining_bytes': total - remote, 'source_sha256': state['sha256'],
                    'notice': 'HEAD only; no file bytes sent or local state changed. Remote offset does not verify remote content; reconciliation still requires an explicit run.'}

    def run_upload(self, job_id, url, bytes_per_second):
        fingerprint = core.url_hash(url)
        if type(bytes_per_second) is not int or not 1024 <= bytes_per_second <= 20 * 1024**2:
            raise ValueError('Invalid transfer rate')
        with self.lock():
            path = self.upload_path(job_id)
            state = json.loads(path.read_text())
            if state['url_hash'] != fingerprint: raise ValueError('URL differs from the original job')
            source = source_file(state['source'], self.root.parent, bool(state.get('profile_media_allowed')))
            if identity(source) != state['identity']: raise ValueError('Source changed; create a new upload job')
            if state['status'] == 'accepted':
                return {'job_id': job_id, 'status': 'accepted', 'acknowledged_bytes': state['offset'], 'remote_checksum_verified': False}
            total = state['identity'][2]
            headers = {'Tus-Resumable': '1.0.0', 'Cache-Control': 'no-store'}
            try:
                head = urllib.request.Request(url, headers=headers, method='HEAD')
                with core.open_request(head) as response:
                    if response.status not in (200, 204): raise ValueError('Unexpected tus HEAD status')
                    remote = response_offset(response, total, require_length=True)
                pending = state['pending']
                if pending is not None:
                    if remote not in (state['offset'], state['offset'] + pending):
                        raise ValueError('Remote offset does not match either pending outcome; review the upload')
                    state['offset'], state['pending'] = remote, None
                    state['status'] = 'reconciled'
                    self.save(path, state)
                    return {'job_id': job_id, 'status': 'reconciled', 'acknowledged_bytes': remote,
                            'notice': 'The previous request was reconciled. Run again explicitly for the next chunk.'}
                if remote != state['offset']: raise ValueError('Remote offset changed independently; review the upload')
                if remote == total:
                    if core.file_hash(source) != state['sha256'] or identity(source) != state['identity']: raise ValueError('Source content changed during upload')
                    state['status'] = 'accepted'
                    self.save(path, state)
                    return {'job_id': job_id, 'status': 'accepted', 'acknowledged_bytes': remote, 'remote_checksum_verified': False}
                count = min(65536, bytes_per_second, total - remote)
                with source.open('rb') as f:
                    f.seek(remote)
                    data = f.read(count)
                if len(data) != count or identity(source) != state['identity']: raise ValueError('Source changed while reading')
                state.update({'pending': count, 'status': 'outcome_unknown'})
                self.save(path, state)
                request = urllib.request.Request(url, data=data, method='PATCH', headers={**headers,
                    'Content-Type': 'application/offset+octet-stream', 'Upload-Offset': str(remote)})
                start = time.monotonic()
                with core.open_request(request) as response:
                    if response.status != 204 or response_offset(response, total) != remote + count:
                        raise ValueError('Server response could not confirm the uploaded chunk')
                wait = count / bytes_per_second - (time.monotonic() - start)
                if wait > 0: time.sleep(wait)
                state.update({'offset': remote + count, 'pending': None, 'status': 'in_progress'})
                if state['offset'] == total:
                    if identity(source) != state['identity'] or core.file_hash(source) != state['sha256'] or identity(source) != state['identity']:
                        raise ValueError('Source changed during upload; remote content requires review')
                    state['status'] = 'accepted'
                self.save(path, state)
                return {'job_id': job_id, 'status': state['status'], 'acknowledged_bytes': state['offset'],
                        'total_bytes': total, 'remote_checksum_verified': False}
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise RuntimeError('Upload request failed or outcome is unknown; check job before explicit recovery') from exc
