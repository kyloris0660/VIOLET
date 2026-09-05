"""Fictional provider-shaped rows for PX3 contract and browser QA."""
from ..models import Media, SourceMetadataRecord, SourceTagObservation
from ..enums import FileTypeEnum
from .pixiv_metadata_ingestion_service import PIXIV_METADATA_NORMALIZER_VERSION


def seed_media_binding_fixture(db, offset=0):
    """Fictional provider-shaped metadata; no file reads or provider requests."""
    # Current/old names share creator ID across works, but neither name is on
    # every work. The homonym creator must never inherit the historical alias.
    specs = [
        ('910000001', '920000001', 'AsterCurrent', 'MoonGarden'),
        ('910000001', '920000001', 'AsterCurrent', 'MoonGarden'),
        ('910000002', '920000001', 'AsterHistorical', 'SunGarden'),
        ('910000003', '920000002', 'AsterCurrent', 'OtherGarden'),
    ]
    for index, (work, creator, name, title) in enumerate(specs, 1):
        media = Media(id=offset + index, filename=f'px3-{index}.png',
                      path=f'original/px3-{index}.png', hash=f'px3-{index}',
                      file_type=FileTypeEnum.image, mime_type='image/png',
                      file_size=100, width=32, height=32)
        db.add(media)
        db.flush()
        db.add(SourceMetadataRecord(
            id=offset + 100 + index, media_id=media.id, provider='pixiv',
            provider_record_key=f'pixiv:{work}:0:local:{index}',
            source_work_id=work, source_page_index=0,
            metadata_kind='provider_metadata', data_type_label='authenticated_provider_metadata',
            status='observed', artist_id=creator, artist_name=name, title=title,
            raw_metadata_json={'id': int(work), 'num': 0, 'page_count': 1,
                               '_pixiv_metadata_normalizer_version': PIXIV_METADATA_NORMALIZER_VERSION},
            provenance={'source': 'gallery_dl_authenticated_metadata',
                        'parser_version': 'px3_synthetic_fixture_v1',
                        'stable_identity_key': {'provider': 'pixiv', 'work_id': work, 'page_index': 0}},
        ))
        db.flush()
        tag = 'MoonPetal' if work == '910000001' else 'SunPetal'
        db.add(SourceTagObservation(
            source_metadata_record_id=offset + 100 + index, provider='pixiv',
            observation_key='fixture-tag', raw_tag=tag, normalized_tag=tag.lower(),
            canonical_tag_key=tag.lower(), source_category_raw='general', status='observed',
        ))
    db.commit()
    return [offset + n for n in range(1, 5)]
