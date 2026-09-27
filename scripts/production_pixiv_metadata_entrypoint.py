"""Fixed local provider identity; private canary JSON is never an executable policy."""
import hashlib
import json
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def verify_metadata_entrypoint(auth, *, command=None):
    anchor=json.loads((ROOT/'docs/state/production-pixiv-a2-metadata-entrypoint.json').read_text(encoding='utf-8'))
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
