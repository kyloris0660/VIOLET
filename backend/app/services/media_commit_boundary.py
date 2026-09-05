"""Distinguish durable media from temporary files after a response failure."""

from fastapi import HTTPException


class MediaCommittedError(HTTPException):
    media_committed = True

    def __init__(self, media_id):
        self.media_id = media_id
        super().__init__(500, detail={
            'code': 'media_committed_response_failed',
            'media_id': media_id,
            'message': '图片已保存，响应处理失败；文件已保留，请刷新图库核对。',
        })
