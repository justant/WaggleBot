# Upload Interface Contract v1.0

## upload_post() 시그니처
def upload_post(post: Post, content: Content, session: Session) -> bool

## 입력 조건
- content.video_path: MEDIA_DIR 상대 경로 (str). 파일 존재 필수.
- post.status == PostStatus.RENDERED

## 반환값
- True: 전체 플랫폼 업로드 성공
- False: 하나 이상 실패 (상세: content.upload_meta)

## upload_meta 구조
{
  "youtube": {
    "video_id": "abc123",
    "url": "https://youtube.com/shorts/abc123",
    "uploaded_at": "2026-02-21T12:00:00Z"
  }
}
