"""Fixed local provider identity; private canary JSON is never an executable policy."""
import hashlib
import json
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def verify_metadata_entrypoint(auth, *, command=None):
    public=json.loads((ROOT/'docs/state/production-pixiv-a2-metadata-entrypoint.json').read_text(encoding='utf-8'))
    private_root=(ROOT/'.local_manifests/pixiv-a2').resolve(strict=True)
    try:
        path=(private_root/public['private_identity_file']).resolve(strict=True)
        if not path.is_relative_to(private_root) or not path.is_file():
            raise ValueError('private identity outside root')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=public['private_identity_sha256']:
            raise ValueError('private identity digest changed')
        anchor=json.loads(raw)
    except (OSError,KeyError,TypeError,ValueError) as exc:
        raise ValueError('a2_metadata_entrypoint_private_identity_changed') from exc
    digest=hashlib.sha256(json.dumps(auth,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if digest!=anchor['accepted_canary_fingerprint'] or auth.get('route_viable') is not True:
        raise ValueError('a2_metadata_entrypoint_canary_changed')
    if auth.get('entrypoint')!=anchor['entrypoint']:
        raise ValueError('a2_metadata_entrypoint_changed')
    for row in anchor['files']:
        path=Path(row['path'])
        if (not path.is_absolute() or not path.is_file() or path.is_symlink()
            or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']):
            raise ValueError('a2_metadata_entrypoint_file_changed')
    prefix=[*anchor['entrypoint']['command'],'--retries','0','--sleep-request','2']
    if command is not None:
        if (not isinstance(command,list) or command[:-1]!=[*prefix,'--dump-json','--no-download']
            or not isinstance(command[-1],str)
            or not re.fullmatch(r'https://www\.pixiv\.net/artworks/[1-9][0-9]*',command[-1])):
            raise ValueError('a2_metadata_entrypoint_arguments_changed')
    return prefix
