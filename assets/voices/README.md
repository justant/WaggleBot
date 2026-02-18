# 참조 오디오 (Voice Presets)

Fish Speech 1.5 zero-shot 클로닝용 참조 오디오 파일.

## 요구 사항
- 포맷: WAV, 16kHz 이상, 모노
- 길이: 10~30초 (너무 짧으면 품질 저하)
- 내용: 잡음 없는 깨끗한 음성

## 파일 목록
| 파일명 | 설명 | 용도 |
|---|---|---|
| korean_man_default.wav | 한국어 남성 내레이터 | 기본 (default) |

## 추가 방법
1. wav 파일을 이 디렉터리에 복사
2. `config/settings.py`의 `VOICE_PRESETS` dict에 키-파일명 등록

## 주의
- `korean_man_default.wav`는 직접 준비 (녹음 or 다운로드)
- 이 디렉터리의 wav 파일은 `.gitignore`에 추가 권장 (용량 크고 저작권 고려)
