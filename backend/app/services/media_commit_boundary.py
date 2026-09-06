"""Distinguish durable media from temporary files after a response failure."""

from fastapi import HTTPException


class MediaCommittedError(HTTPException):
    media_committed = True

    def __init__(self, media_id, *, file_hash=None, path=None):
        self.media_id = media_id
        self.file_hash = file_hash
        self.path = path
        super().__init__(500, detail={
            'code': 'media_committed_response_failed',
            'media_id': media_id,
            'media_committed': True,
            'requires_reupload': False,
            'message': '图片已保存，响应处理失败；文件已保留，请刷新图库核对。',
        })


def recover_committed_media(db, error, *, expected_hash=None):
    """Recover the committed identity without refresh/serialization or re-import."""
    from ..models import Media
    db.rollback()
    row = db.get(Media, error.media_id, populate_existing=True)
    if (row is None or not error.file_hash or not error.path
        or row.hash != error.file_hash or row.path != error.path
        or (expected_hash is not None and row.hash != expected_hash)):
        error.detail = {**error.detail, 'code': 'media_committed_identity_recovery_required',
                        'message': '已提交结果的身份核对失败；请按 Media ID 恢复登记，勿重新上传。'}
        raise error
    return row


def committed_media_response(db, error, *, expected_hash=None):
    from fastapi.responses import JSONResponse
    saved = recover_committed_media(db, error, expected_hash=expected_hash)
    return JSONResponse(status_code=202, content={
        'id': saved.id, 'media_id': saved.id, 'media_committed': True,
        'status': 'imported_recovery_pending', 'requires_reupload': False,
        'detail_url': f'/media/{saved.id}',
        'message': '图片已保存，后续响应处理待恢复；请打开详情，勿重新上传。',
    })
