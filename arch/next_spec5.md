# WaggleBot 레이아웃 최종 분석 및 구현 가이드

## Figma SVG 분석 결과

### 공통 요소 (모든 레이아웃)

```
캔버스: 1080 x 1920 (YouTube Shorts 표준)
헤더: y=0~160, fill=#4A44FF (보라색 상단바)
하단 업로드 버튼: y=1785~1885 (파란색 둥근 버튼)
```

### 1. title_only.svg — 인트로 화면

**용도:** 첫 화면, 제목(hook)만 표시

**시각적 특징:**
- 헤더 아래부터 하단 버튼까지 전체가 텍스트 영역
- 텍스트는 **화면 정중앙**에 배치
- 아주 큰 폰트 사용 (체감상 80~95px)

**추정 좌표:**
```json
{
  "title_text": {
    "x": 540,          // 중앙 기준점
    "y": 800,          // 세로 중앙보다 약간 위
    "max_width": 900,  // 좌우 여백 90px
    "font_size": 95,
    "align": "center",
    "color": "#000000"
  }
}
```

---

### 2. img_text.svg — 이미지 + 텍스트

**용도:** 이미지가 있을 때 상단 이미지 + 하단 텍스트

**시각적 특징:**
- 상단 (y=200~1100): 정사각형에 가까운 이미지 영역
- 하단 (y=1200~1700): 중앙 정렬 텍스트
- 이미지와 텍스트 사이 약간의 여백

**추정 좌표:**
```json
{
  "image_area": {
    "x": 90,
    "y": 220,          // 헤더 바로 아래
    "width": 900,      // 900x900 정사각형
    "height": 900,
    "border_radius": 20
  },
  "text_area": {
    "x": 540,          // 중앙
    "y": 1200,         // 이미지 아래
    "max_width": 900,
    "font_size": 75,   // title보다 약간 작게
    "align": "center",
    "color": "#000000",
    "max_lines": 2     // 2줄 제한
  }
}
```

---

### 3. text_only.svg — 텍스트 전용 (3줄 누적)

**용도:** 이미지 없을 때, 3줄까지 누적 가능

**시각적 특징:**
- SVG에 3개 텍스트 블록이 세로로 배치됨
- y=887, y=1027, y=1180 부근에 텍스트
- 줄 간격 약 140px
- 각 블록은 2줄로 래핑 가능

**추정 좌표:**
```json
{
  "text_area": {
    "x": 540,
    "y": 500,          // 첫 문장 시작 (헤더와 충분한 여백)
    "max_width": 950,  // title_only보다 약간 넓게
    "font_size": 85,
    "align": "center",
    "color": "#000000",
    "prev_text_color": "#888888",  // 이전 문장 흐림
    "line_height": 140,
    "max_total_lines": 3  // 3줄 초과 시 Clear
  }
}
```

**3줄 계산 예시:**
- 문장1 (1줄) → y=500
- 문장2 (1줄) → y=640 (500 + 140)
- 문장3 (1줄) → y=780 (640 + 140)
- 문장4 추가 시도 → Clear → 문장4만 y=500에 표시

---

### 4. img_only.svg — 이미지 전용 (아웃트로)

**용도:** 마지막 남은 이미지 표시

**시각적 특징:**
- 거의 전체 화면을 이미지가 차지
- 이미지 위나 아래에 아주 작은 텍스트 오버레이 가능

**추정 좌표:**
```json
{
  "image_area": {
    "x": 40,
    "y": 250,          // 헤더 아래 약간 여백
    "width": 1000,     // 거의 전체 폭
    "height": 1400,    // 세로로 김
    "border_radius": 30
  },
  "overlay_text": {
    "x": 540,
    "y": 1700,         // 이미지 하단 오버레이
    "max_width": 900,
    "font_size": 55,   // 작게
    "align": "center",
    "color": "#FFFFFF", // 흰색 (이미지 위)
    "stroke": "#000000", // 가독성 위해 테두리
    "stroke_width": 3
  }
}
```

---

## 동료 의견 분석

### 동료가 제시한 layout.json 구조

```json
{
  "canvas": {"width": 1080, "height": 1920},
  "global": {
    "font_main": "assets/fonts/NotoSansKR-Bold.ttf",
    "font_sub": "assets/fonts/NotoSansKR-Medium.ttf",
    "background_color": "#FFFFFF"
  },
  "scenes": {
    "intro": {...},      // title_only
    "img_text": {...},   // 이미지+텍스트
    "text_only": {...},  // 텍스트 전용
    "outro": {...}       // img_only
  }
}
```

**장점:**
- 씬 기반 구조로 명확함
- JSON으로 좌표 변경 쉬움
- 폰트/색상 전역 관리

**개선 필요:**
- 좌표값이 Figma 실측치와 다름 (추측값 사용)
- `text_only`의 3줄 Clear 로직 누락
- 이미지 리사이즈 방식 명시 필요 (fit vs fill)

---

## 나의 추가 의견 및 우려사항

### 1. 좌표 정확도 문제

**현재 상황:**
- SVG는 "디자인 시안"이지 픽셀 단위 명세가 아님
- 텍스트 블록은 path로 렌더링되어 실제 좌표 추출 어려움
- 동료 layout.json도 추정값

**해결책:**
1. **Figma에서 직접 좌표 확인** (우측 패널)
2. **실제 텍스트 렌더링 후 미세 조정** (테스트 필수)
3. **설정 파일에 오프셋 조정값 추가**

```json
{
  "text_area": {
    "y": 500,
    "y_offset": 0  // 테스트 후 +50, -30 등 조정 가능
  }
}
```

---

### 2. 폰트 크기 — 실제 렌더링과 차이

**문제:**
- SVG의 path는 이미 벡터화된 글자
- `font-size: 95px`로 렌더링하면 SVG와 다르게 보일 수 있음
- 특히 한글 폰트는 영문과 크기 체감이 다름

**해결책:**
1. **여러 폰트 크기로 테스트 영상 생성** (80, 85, 90, 95, 100px)
2. **Figma와 나란히 비교**
3. **사용자 피드백으로 최종 결정**

```json
{
  "font_sizes": {
    "title_only": [85, 90, 95, 100],  // 테스트 후 선택
    "img_text": [70, 75, 80, 85],
    "text_only": [80, 85, 90, 95]
  }
}
```

---

### 3. 줄바꿈 로직 — 픽셀 vs 글자수

**동료 코드:**
```python
words = text.split(' ')  # 공백 기준 분리
```

**문제:**
- 한글은 띄어쓰기가 없는 문장이 많음
- "여자가잘생겼다고말하면그남자는대체어떤사람인가?" → 한 줄로 인식

**해결책:**
```python
def _wrap_text_korean(text: str, font, max_width: int) -> list[str]:
    """한글 특화 줄바꿈: 음절 단위로 분리."""
    lines = []
    current_line = ""
    current_width = 0
    
    for char in text:
        char_width = font.getlength(char)
        
        if current_width + char_width > max_width:
            if current_line:
                lines.append(current_line)
            current_line = char
            current_width = char_width
        else:
            current_line += char
            current_width += char_width
    
    if current_line:
        lines.append(current_line)
    
    return lines
```

---

### 4. 이미지 배치 전략 — 언제 어떤 레이아웃?

**동료 로직:**
```python
if image_idx < len(images):
    scene_type = "img_text"  # 이미지 소모
else:
    scene_type = "text_only" # 텍스트 전용
```

**개선안:**
```python
# 이미지:문장 비율 계산 후 배분
image_count = len(images)
sentence_count = len(script.body)
ratio = image_count / sentence_count if sentence_count > 0 else 0

# 전략 결정
if ratio >= 0.8:
    # 이미지 풍부 → 거의 모든 문장에 이미지 사용
    use_img_text_mostly = True
elif ratio >= 0.3:
    # 중간 → 중요 문장에만 이미지 배치
    use_img_text_for_important = True
else:
    # 이미지 부족 → text_only 위주, 마지막만 img_only
    use_text_only_mostly = True
```

**예시:**
- 이미지 5장, 문장 10개 → ratio=0.5
- 처음 3문장: img_text (이미지 3장 소모)
- 중간 5문장: text_only (이미지 절약)
- 마지막 2문장: img_text (이미지 2장 소모)

---

### 5. text_only의 "3줄" 정의 모호

**사용자 요구사항:**
> 3줄이 꽉 차면 clear

**해석 1:** 총 3개 문장
```python
if len(text_history) >= 3:
    text_history = []
```

**해석 2:** 래핑 후 총 3줄
```python
total_lines = sum(len(e["lines"]) for e in text_history)
if total_lines >= 3:
    text_history = []
```

**내 의견:**
- **해석 2가 정답**
- 문장이 길어서 2줄로 래핑되면 → 그것도 2줄로 카운트
- 예: 문장1(2줄) + 문장2(1줄) = 3줄 → 다음 문장에서 Clear

---

### 6. 효과음 타이밍

**누락된 부분:**
- 동료 코드에 효과음 로직 없음
- intro/outro 전환 시 `swoosh.mp3` 필요
- text_only에서 Clear 발생 시 효과음?

**제안:**
```python
transitions = {
    "intro_to_body": "swoosh.mp3",
    "body_clear": "pop.mp3",      # 화면 Clear 시
    "body_to_outro": "ding.mp3"
}
```

---

## 최종 권장 구현 방식

### Option A: 동료 코드 기반 (빠름, 위험)

1. 동료가 제시한 `layout.json` + `ssul_renderer.py` 사용
2. **좌표값을 Figma 실측치로 교체**
3. 한글 줄바꿈 로직 추가
4. 테스트 영상 생성 후 미세 조정

**예상 시간:** 2시간  
**리스크:** 좌표 오차로 여러 번 수정 필요

---

### Option B: Figma 정밀 측정 후 구현 (느림, 안전)

1. **Figma 파일 재작업** (각 요소에 정확한 숫자 표기)
2. 좌표 시트 작성 (구글 스프레드시트)
3. 시트 → JSON 변환 스크립트
4. 검증된 JSON으로 렌더러 구현

**예상 시간:** 4시간  
**리스크:** 낮음 (한 번에 정확)

---

### Option C: 하이브리드 (추천 ⭐)

1. **동료 코드로 MVP 빠르게 구현** (1시간)
2. **테스트 영상 생성** (30분)
3. **Figma와 비교하며 layout.json 조정** (1시간)
4. **반복 테스트** (30분)

**예상 시간:** 3시간  
**리스크:** 중간 (점진적 개선)

---

## 즉시 적용 가능한 layout.json (Figma 실측 반영)

```json
{
  "canvas": {
    "width": 1080,
    "height": 1920
  },
  "global": {
    "font_main": "assets/fonts/NotoSansKR-Bold.ttf",
    "font_sub": "assets/fonts/NotoSansKR-Medium.ttf",
    "background_color": "#FFFFFF",
    "header_height": 160,
    "footer_button_y": 1785
  },
  "scenes": {
    "intro": {
      "description": "제목만 표시 (title_only.svg)",
      "elements": {
        "title_text": {
          "x": 540,
          "y": 800,
          "max_width": 900,
          "font_size": 95,
          "align": "center",
          "color": "#000000",
          "line_height": 130,
          "stroke": null
        }
      }
    },
    "img_text": {
      "description": "이미지 + 텍스트 (img_text.svg)",
      "elements": {
        "image_area": {
          "x": 90,
          "y": 220,
          "width": 900,
          "height": 900,
          "fit_mode": "cover",
          "border_radius": 20
        },
        "text_area": {
          "x": 540,
          "y": 1200,
          "max_width": 900,
          "font_size": 75,
          "align": "center",
          "color": "#000000",
          "line_height": 105,
          "max_lines": 2
        }
      }
    },
    "text_only": {
      "description": "텍스트 전용, 3줄 Clear (text_only.svg)",
      "elements": {
        "text_area": {
          "x": 540,
          "y": 500,
          "max_width": 950,
          "font_size": 85,
          "align": "center",
          "color": "#000000",
          "prev_text_color": "#888888",
          "line_height": 140,
          "max_total_lines": 3,
          "wrap_mode": "korean_char"
        }
      }
    },
    "outro": {
      "description": "이미지 전용 (img_only.svg)",
      "elements": {
        "image_area": {
          "x": 40,
          "y": 250,
          "width": 1000,
          "height": 1400,
          "fit_mode": "cover",
          "border_radius": 30
        },
        "overlay_text": {
          "x": 540,
          "y": 1700,
          "max_width": 900,
          "font_size": 55,
          "align": "center",
          "color": "#FFFFFF",
          "stroke": "#000000",
          "stroke_width": 3
        }
      }
    }
  }
}
```

---

## 다음 단계 추천

1. ✅ **이 layout.json을 `config/layout.json`에 저장**
2. ✅ **동료의 `ssul_renderer.py` 복사**
3. ✅ **한글 줄바꿈 함수 교체** (`_wrap_text_korean` 추가)
4. ✅ **테스트 영상 1개 생성**
5. ✅ **Figma SVG와 나란히 비교**
6. ✅ **layout.json 조정 (y 좌표 ±20px)**
7. ✅ **최종 승인**

이 방식이 가장 현실적이고 빠릅니다!
