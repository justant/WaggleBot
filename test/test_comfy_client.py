"""ComfyUI 클라이언트 단위 테스트.

실행 방법:
  docker compose exec ai_worker python -m pytest test/test_comfy_client.py -v

또는 ComfyUI가 실행 중인 상태에서:
  python test/test_comfy_client.py

테스트 결과물 위치:
  test/test_comfy_client_output/
  test/test_comfy_client_output/test_result.md
"""
import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("test/test_comfy_client_output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


class TestComfyUIClient:
    """ComfyUI 클라이언트 테스트 (Mock 기반)."""

    def test_health_check_success(self):
        """ComfyUI 서버가 정상일 때 health_check가 True를 반환한다."""
        from ai_worker.video.comfy_client import ComfyUIClient

        client = ComfyUIClient("http://localhost:8188")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"system": {"vram_total": 12000}}

        with patch("ai_worker.video.comfy_client.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.get.return_value = mock_resp
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_http

            result = asyncio.run(client.health_check())
            assert result is True

    def test_health_check_failure(self):
        """ComfyUI 서버가 다운일 때 health_check가 False를 반환한다."""
        from ai_worker.video.comfy_client import ComfyUIClient

        client = ComfyUIClient("http://localhost:8188")

        with patch("ai_worker.video.comfy_client.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.get.side_effect = Exception("Connection refused")
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_http

            result = asyncio.run(client.health_check())
            assert result is False

    def test_load_workflow(self):
        """t2v_ltx.json 워크플로우를 정상 로드한다."""
        workflow_path = Path("comfyui_workflows/t2v_ltx.json")
        assert workflow_path.exists(), f"워크플로우 파일 없음: {workflow_path}"

        data = json.loads(workflow_path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert len(data) > 0

    def test_patch_workflow(self):
        """_patch_workflow가 노드 inputs를 정확히 교체한다."""
        from ai_worker.video.comfy_client import ComfyUIClient

        client = ComfyUIClient("http://localhost:8188")

        workflow = {
            "1": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "positive_prompt"},
            },
            "2": {
                "class_type": "KSampler",
                "inputs": {"steps": 20, "cfg": 3.5},
            },
        }

        params = {
            "positive_prompt": "A beautiful sunset",
            "steps": 30,
        }

        patched = client._patch_workflow(workflow, params)
        assert patched["1"]["inputs"]["text"] == "A beautiful sunset"
        assert patched["2"]["inputs"]["steps"] == 30

    def test_generate_t2v_mock(self):
        """T2V 생성 전체 흐름을 Mock으로 테스트한다."""
        from ai_worker.video.comfy_client import ComfyUIClient

        client = ComfyUIClient("http://localhost:8188")

        with patch.object(client, "_queue_and_wait", new_callable=AsyncMock) as mock_q:
            mock_q.return_value = Path("/tmp/test_output.mp4")

            result = asyncio.run(
                client.generate_t2v(
                    prompt="A beautiful sunset over mountains",
                    negative_prompt="blurry",
                    width=512, height=512,
                    num_frames=81, steps=20,
                    cfg_scale=3.5,
                )
            )
            assert result == Path("/tmp/test_output.mp4")
            mock_q.assert_called_once()

    def test_generate_i2v_mock(self):
        """I2V 생성 전체 흐름을 Mock으로 테스트한다."""
        from ai_worker.video.comfy_client import ComfyUIClient

        client = ComfyUIClient("http://localhost:8188")

        with patch.object(client, "_queue_and_wait", new_callable=AsyncMock) as mock_q, \
             patch.object(client, "_upload_image", new_callable=AsyncMock) as mock_upload:
            mock_q.return_value = Path("/tmp/test_i2v.mp4")
            mock_upload.return_value = "uploaded_image.png"

            result = asyncio.run(
                client.generate_i2v(
                    prompt="Animate this scene",
                    init_image_path=Path("/tmp/test.jpg"),
                    width=512, height=512,
                    num_frames=81, steps=20,
                    cfg_scale=3.5,
                )
            )
            assert result == Path("/tmp/test_i2v.mp4")

    def test_timeout_handling(self):
        """타임아웃 시 RuntimeError가 발생한다."""
        from ai_worker.video.comfy_client import ComfyUIClient

        client = ComfyUIClient("http://localhost:8188")

        with patch.object(client, "_queue_and_wait", new_callable=AsyncMock) as mock_q:
            mock_q.side_effect = asyncio.TimeoutError("Timeout")

            with pytest.raises((asyncio.TimeoutError, RuntimeError)):
                asyncio.run(
                    client.generate_t2v(
                        prompt="test", negative_prompt="",
                        width=512, height=512,
                        num_frames=81, steps=20,
                        cfg_scale=3.5, timeout=1,
                    )
                )


def generate_test_result():
    """테스트 결과 MD 파일 생성."""
    result_md = OUTPUT_DIR / "test_result.md"
    result_md.write_text("""# ComfyUI Client 테스트 결과

## 실행 환경
- Python: 3.12
- GPU: RTX 3080 Ti / 없음(Mock)

## 실행 방법
```bash
# Mock 테스트 (서버 불필요)
python -m pytest test/test_comfy_client.py -v --tb=short

# 통합 테스트 (Docker 필요)
docker compose exec ai_worker python -m pytest test/test_comfy_client.py -v
```

## 테스트 항목
| 테스트 | 설명 | 상태 |
|--------|------|------|
| test_health_check_success | 서버 정상 시 True 반환 | ⬜ |
| test_health_check_failure | 서버 다운 시 False 반환 | ⬜ |
| test_load_workflow | 워크플로우 JSON 로드 | ⬜ |
| test_patch_workflow | 노드 입력값 교체 | ⬜ |
| test_generate_t2v_mock | T2V Mock 전체 흐름 | ⬜ |
| test_generate_i2v_mock | I2V Mock 전체 흐름 | ⬜ |
| test_timeout_handling | 타임아웃 RuntimeError | ⬜ |

## 실제 ComfyUI 통합 테스트 (수동)
```bash
python -c "
import asyncio
from ai_worker.video.comfy_client import ComfyUIClient
client = ComfyUIClient('http://localhost:8188')
print('Health:', asyncio.run(client.health_check()))
"
```
""", encoding="utf-8")


if __name__ == "__main__":
    generate_test_result()
    pytest.main([__file__, "-v"])
