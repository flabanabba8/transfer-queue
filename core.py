"""Serialized bounded HTTPS downloads; no credentials, upload or automatic retry."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

MAX_BYTES = 512 * 1024**2
CONTENT_RANGE = re.compile(r'^bytes (\d+)-(\d+)/(\d+)$')


def url_hash(value):
    if not isinstance(value, str) or len(value) > 4096: raise ValueError('Invalid URL')
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError('Use HTTPS without credentials, query string or fragment')
    return hashlib.sha256(value.encode()).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for data in iter(lambda: f.read(1024 * 1024), b''): h.update(data)
    return h.hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_request(request):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    return opener.open(request, timeout=10)


class Queue:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    @contextlib.contextmanager
    def lock(self):
        with (self.root / '.lock').open('a') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield

    def paths(self, job_id):
        if not isinstance(job_id, str) or len(job_id) != 32 or any(c not in '0123456789abcdef' for c in job_id): raise ValueError('Invalid job ID')
        paths = [self.root / (job_id + suffix) for suffix in ('.json', '.part', '.bin')]
        if any(path.is_symlink() for path in paths): raise ValueError('Invalid job path')
        return paths

    def save(self, path, state):
        temp = self.root / (uuid.uuid4().hex + '.tmp')
        try:
            with temp.open('x') as f: json.dump(state, f)
            os.replace(temp, path)
        finally: temp.unlink(missing_ok=True)

    def create_download(self, url, sha256):
        fingerprint = url_hash(url)
        if not isinstance(sha256, str) or not re.fullmatch('[0-9a-f]{64}', sha256): raise ValueError('Provide a lowercase expected SHA-256')
        with self.lock():
            job = uuid.uuid4().hex
            meta, part, final = self.paths(job)
            self.save(meta, {'url_hash': fingerprint, 'sha256': sha256, 'etag': None, 'total': None, 'status': 'queued'})
            return {'job_id': job, 'status': 'queued'}

    def status(self, job_id):
        with self.lock(): return self.status_locked(job_id)

    def list_jobs(self):
        with self.lock():
            paths = sorted(self.root.glob('*.json'))
            if len(paths) > 1000: raise ValueError('More than 1,000 job records; narrow the queue first')
            jobs, unreadable = [], 0
            for path in paths:
                match = re.fullmatch(r'([0-9a-f]{32})(\.upload)?\.json', path.name)
                if not match: continue
                try:
                    if path.is_symlink(): raise ValueError('Symlink job record')
                    state = json.loads(path.read_text(encoding='utf-8'))
                    if not isinstance(state.get('status'), str): raise ValueError('Invalid job status')
                    jobs.append({'job_id': match.group(1), 'direction': 'upload' if match.group(2) else 'download', 'status': state['status']})
                except (ValueError, OSError, TypeError):
                    unreadable += 1
            return {'jobs': jobs, 'unreadable_records': unreadable,
                    'notice': 'This listing is local only; it does not contact servers or confirm remote state.'}

    def status_locked(self, job_id):
        meta, part, final = self.paths(job_id)
        state = json.loads(meta.read_text())
        size = final.stat().st_size if final.exists() else part.stat().st_size if part.exists() else 0
        return {'job_id': job_id, 'status': state['status'], 'received_bytes': size, 'total_bytes': state['total'],
                'output_path': str(final) if state['status'] == 'verified' and final.is_file() else None}

    def run_download(self, job_id, url, bytes_per_second, max_seconds=15):
        fingerprint = url_hash(url)
        if type(bytes_per_second) is not int or not 1024 <= bytes_per_second <= 20 * 1024**2 or type(max_seconds) is not int or not 1 <= max_seconds <= 30:
            raise ValueError('Invalid transfer rate or time slice')
        with self.lock():
            meta, part, final = self.paths(job_id)
            state = json.loads(meta.read_text())
            if state['url_hash'] != fingerprint: raise ValueError('URL differs from the original job')
            if state['status'] == 'verified': return self.status_locked(job_id)
            if state['status'] == 'checksum_mismatch': raise ValueError('Checksum mismatch; create a new job after reviewing the source')
            # Recover a complete file if the previous process stopped before saving its receipt.
            complete = final if final.exists() else part if part.exists() and part.stat().st_size == state['total'] else None
            if complete:
                if complete.stat().st_size != state['total'] or file_hash(complete) != state['sha256']:
                    state['status'] = 'checksum_mismatch'
                else:
                    if complete == part: os.replace(part, final)
                    state['status'] = 'verified'
                self.save(meta, state)
                return self.status_locked(job_id)
            offset = part.stat().st_size if part.exists() else 0
            if offset > MAX_BYTES: raise ValueError('Partial file exceeds size limit')
            headers = {'Accept-Encoding': 'identity', 'User-Agent': 'Hermes-Transfer-Queue/0.1'}
            if offset:
                if not state['etag']: raise ValueError('Server did not supply a strong ETag; this partial download cannot resume')
                headers.update({'Range': f'bytes={offset}-', 'If-Range': state['etag']})
            request = urllib.request.Request(url, headers=headers, method='GET')
            try:
                with open_request(request) as response:
                    status = response.status
                    etag = response.headers.get('ETag', '')
                    if etag.startswith('W/') or not (etag.startswith('"') and etag.endswith('"')): etag = None
                    if status == 206:
                        match = CONTENT_RANGE.fullmatch(response.headers.get('Content-Range', ''))
                        if not offset or not match or int(match.group(1)) != offset or int(match.group(2)) != int(match.group(3))-1 or etag != state['etag']:
                            raise ValueError('Server returned an invalid or changed range')
                        total = int(match.group(3))
                        mode = 'ab'
                    elif status == 200:
                        length = response.headers.get('Content-Length', '')
                        if not length.isdigit(): raise ValueError('Server must provide Content-Length')
                        total = int(length)
                        offset, mode = 0, 'wb'
                    else:
                        raise ValueError('Unsupported HTTP response')
                    if total <= 0 or total > MAX_BYTES or offset >= total: raise ValueError('Download size outside supported range')
                    state.update({'etag': etag, 'total': total, 'status': 'in_progress'})
                    self.save(meta, state)
                    start = time.monotonic()
                    received = 0
                    with part.open(mode) as out:
                        while offset + received < total and time.monotonic() - start < max_seconds:
                            chunk = response.read(min(65536, bytes_per_second, total - offset - received))
                            if not chunk: break
                            out.write(chunk)
                            received += len(chunk)
                            delay = received / bytes_per_second - (time.monotonic() - start)
                            if delay > 0: time.sleep(delay)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise RuntimeError('Download interrupted; inspect job before continuing') from exc
            size = part.stat().st_size
            if size == state['total']:
                if file_hash(part) != state['sha256']:
                    state['status'] = 'checksum_mismatch'
                    self.save(meta, state)
                else:
                    os.replace(part, final)
                    state['status'] = 'verified'
                    self.save(meta, state)
            return self.status_locked(job_id)
