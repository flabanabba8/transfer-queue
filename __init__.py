import json
from .core import Queue
from .upload import Uploads

TOOLS = {
    'transfer_create_download': ('Create a serialized HTTPS download job with a required expected SHA-256. URLs with credentials, query strings or redirects are unsupported.', {'url': {'type': 'string'}, 'sha256': {'type': 'string'}}, ['url', 'sha256']),
    'transfer_run_download': ('Run one bounded download slice at an explicit byte-per-second limit; resumable servers need a strong ETag. Supply the identical URL again.', {'job_id': {'type': 'string'}, 'url': {'type': 'string'}, 'bytes_per_second': {'type': 'integer', 'minimum': 1024, 'maximum': 20971520}, 'max_seconds': {'type': 'integer', 'minimum': 1, 'maximum': 30}}, ['job_id', 'url', 'bytes_per_second']),
    'transfer_create_upload': ('Register an existing tus 1.0 HTTPS upload resource and fingerprint an explicit local file. This does not upload yet.', {'source_path': {'type': 'string'}, 'url': {'type': 'string'}, 'allow_profile_media': {'type': 'boolean', 'description': 'Explicitly allow selected media under the active profile plugin-data directory.'}}, ['source_path', 'url']),
    'transfer_upload_preflight': ('Read the selected tus resource length and offset before sending file bytes; reports uncertain prior chunks without changing local state.', {'job_id': {'type': 'string'}, 'url': {'type': 'string'}}, ['job_id', 'url']),
    'transfer_run_upload': ('Send one small rate-limited chunk to the existing tus resource, or reconcile an uncertain prior response without automatically resending it.', {'job_id': {'type': 'string'}, 'url': {'type': 'string'}, 'bytes_per_second': {'type': 'integer', 'minimum': 1024, 'maximum': 20971520}}, ['job_id', 'url', 'bytes_per_second']),
    'transfer_upload_status': ('Show the last acknowledged upload offset and whether a chunk outcome is uncertain.', {'job_id': {'type': 'string'}}, ['job_id']),
    'transfer_status': ('Report local progress and verified output path if complete.', {'job_id': {'type': 'string'}}, ['job_id']),
    'transfer_list_jobs': ('List local upload and download job IDs without contacting servers or revealing source paths.', {}, []),
}

def invoke(name, args):
    from plugins.plugin_storage import plugin_data_dir
    try:
        upload = name in {'transfer_create_upload', 'transfer_run_upload', 'transfer_upload_status', 'transfer_upload_preflight'}
        worker = (Uploads if upload else Queue)(plugin_data_dir('transfer-queue'))
        return json.dumps(getattr(worker, name.removeprefix('transfer_'))(**args))
    except (ValueError, TypeError, OSError, KeyError, RuntimeError) as exc:
        return json.dumps({'error': str(exc)})

def register(ctx):
    for name, (description, properties, required) in TOOLS.items():
        ctx.register_tool(name=name, toolset='transfer_queue', schema={'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}},
            handler=lambda args, _name=name, **kwargs: invoke(_name, args))
