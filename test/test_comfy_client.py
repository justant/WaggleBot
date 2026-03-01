"""ComfyUI 클라이언트 단위 테스트 (LTX-2).

실행 방법:
  docker compose exec ai_worker python -m pytest test/test_comfy_client.py -v

또는 ComfyUI가 실행 중인 상태에서:
  python test/test_comfy_client.py
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
    """ComfyUI 클라이언트 테스트 (Mock 기반, LTX-2)."""

    def test_health_check_success(self):
        """ComfyUI 서버가 정상일 때 health_check가 True를 반환한다."""
        from ai_worker.video.comfy_client import ComfyUIClient

        client = ComfyUIClient("http://localhost:8188")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"system": {"vram_total": 24000}}

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

    def test_load_workflow_t2v(self):
        """t2v_ltx2.json 워크플로우를 정상 로드한다."""
        workflow_path = Path("comfyui_workflows/t2v_ltx2.json")
        assert workflow_path.exists(), f"워크플로우 파일 없음: {workflow_path}"

        data = json.loads(workflow_path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert len(data) >= 13  # 오디오 노드 제거 후 13개

    def test_load_workflow_distilled(self):
        """t2v_ltx2_distilled.json 워크플로우를 정상 로드한다."""
        workflow_path = Path("comfyui_workflows/t2v_ltx2_distilled.json")
        assert workflow_path.exists()

        data = json.loads(workflow_path.read_text(encoding="utf-8"))
        # Distilled은 ManualSigmas 노드를 사용한다
        has_manual_sigmas = any(
            node.get("class_type") == "ManualSigmas"
            for node in data.values()
        )
        assert has_manual_sigmas, "Distilled 워크플로우에 ManualSigmas 노드 없음"

    def test_load_workflow_i2v(self):
        """i2v_ltx2.json 워크플로우를 정상 로드한다."""
        workflow_path = Path("comfyui_workflows/i2v_ltx2.json")
        assert workflow_path.exists()

        data = json.loads(workflow_path.read_text(encoding="utf-8"))
        # I2V는 LoadImage + LTXVImgToVideoInplace 노드가 필요하다
        class_types = {node.get("class_type") for node in data.values()}
        assert "LoadImage" in class_types
        assert "LTXVImgToVideoInplace" in class_types

    def test_load_workflow_upscale(self):
        """t2v_ltx2_upscale.json 워크플로우를 정상 로드한다."""
        workflow_path = Path("comfyui_workflows/t2v_ltx2_upscale.json")
        assert workflow_path.exists()

        data = json.loads(workflow_path.read_text(encoding="utf-8"))
        # 업스케일은 LTXVLatentUpscale 노드가 있어야 한다
        class_types = {node.get("class_type") for node in data.values()}
        assert "LTXVLatentUpscale" in class_types

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
                "class_type": "LTXVScheduler",
                "inputs": {"steps": 20, "max_shift": 2.05},
            },
            "3": {
                "class_type": "CFGGuider",
                "inputs": {"cfg": 3.5},
            },
            "4": {
                "class_type": "RandomNoise",
                "inputs": {"noise_seed": 0},
            },
        }

        params = {
            "positive_prompt": "A beautiful sunset in Seoul",
            "steps": 30,
            "cfg": 1.0,
            "noise_seed": 42,
        }

        patched = client._patch_workflow(workflow, params)
        assert patched["1"]["inputs"]["text"] == "A beautiful sunset in Seoul"
        assert patched["2"]["inputs"]["steps"] == 30
        assert patched["3"]["inputs"]["cfg"] == 1.0
        assert patched["4"]["inputs"]["noise_seed"] == 42

    def test_generate_t2v_mock(self):
        """T2V 생성 전체 흐름을 Mock으로 테스트한다 (LTX-2 기본)."""
        from ai_worker.video.comfy_client import ComfyUIClient

        client = ComfyUIClient("http://localhost:8188")

        with patch.object(client, "_queue_and_wait", new_callable=AsyncMock) as mock_q:
            mock_q.return_value = Path("/tmp/test_output.mp4")

            result = asyncio.run(
                client.generate_t2v(
                    prompt="A beautiful sunset over mountains in Seoul",
                    negative_prompt="blurry",
                    width=1280, height=720,
                    num_frames=97, steps=20,
                    cfg=3.5,
                )
            )
            assert result == Path("/tmp/test_output.mp4")
            mock_q.assert_called_once()

    def test_generate_t2v_distilled_mock(self):
        """Distilled T2V 생성을 Mock으로 테스트한다."""
        from ai_worker.video.comfy_client import ComfyUIClient

        client = ComfyUIClient("http://localhost:8188")

        with patch.object(client, "_queue_and_wait", new_callable=AsyncMock) as mock_q:
            mock_q.return_value = Path("/tmp/test_distilled.mp4")

            result = asyncio.run(
                client.generate_t2v(
                    prompt="Test prompt",
                    use_distilled=True,
                    steps=8,
                    cfg=1.0,
                )
            )
            assert result == Path("/tmp/test_distilled.mp4")

    def test_generate_i2v_mock(self):
        """I2V 생성 전체 흐름을 Mock으로 테스트한다."""
        from ai_worker.video.comfy_client import ComfyUIClient

        client = ComfyUIClient("http://localhost:8188")

        with patch.object(client, "_queue_and_wait", new_callable=AsyncMock) as mock_q, \
             patch.object(client, "_upload_image", new_callable=AsyncMock) as mock_upload, \
             patch.object(client, "_resize_image", return_value=Path("/tmp/test.jpg")) as mock_resize:
            mock_q.return_value = Path("/tmp/test_i2v.mp4")
            mock_upload.return_value = "uploaded_image.png"

            result = asyncio.run(
                client.generate_i2v(
                    prompt="Animate this scene",
                    init_image_path=Path("/tmp/test.jpg"),
                    width=1280, height=720,
                    num_frames=97, steps=20,
                    cfg=3.5,
                    strength=0.75,
                )
            )
            assert result == Path("/tmp/test_i2v.mp4")
            mock_resize.assert_called_once()

    def test_generate_upscale_mock(self):
        """2-Stage 업스케일 생성을 Mock으로 테스트한다."""
        from ai_worker.video.comfy_client import ComfyUIClient

        client = ComfyUIClient("http://localhost:8188")

        with patch.object(client, "_queue_and_wait", new_callable=AsyncMock) as mock_q:
            mock_q.return_value = Path("/tmp/test_upscale.mp4")

            result = asyncio.run(
                client.generate_t2v_with_upscale(
                    prompt="Test prompt",
                    num_frames=97,
                )
            )
            assert result == Path("/tmp/test_upscale.mp4")

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
                        width=1280, height=720,
                        num_frames=97, steps=20,
                        cfg=3.5, timeout=1,
                    )
                )

    def test_all_workflows_have_no_audio_nodes(self):
        """모든 워크플로우에서 오디오 노드가 제거되었다 (Fish Speech TTS 사용)."""
        audio_nodes = {"LTXVAudioVAELoader", "LTXVEmptyLatentAudio",
                       "LTXVConcatAVLatent", "LTXVAudioVAEDecode",
                       "LTXVSeparateAVLatent"}
        for wf_name in ["t2v_ltx2.json", "i2v_ltx2.json",
                        "t2v_ltx2_distilled.json", "t2v_ltx2_upscale.json"]:
            path = Path("comfyui_workflows") / wf_name
            data = json.loads(path.read_text(encoding="utf-8"))
            class_types = {node.get("class_type") for node in data.values()
                          if isinstance(node, dict)}
            found = audio_nodes & class_types
            assert not found, f"{wf_name}에 불필요한 오디오 노드 존재: {found}"

    def test_t2v_workflow_uses_gemma_encoder(self):
        """T2V 워크플로우가 Gemma 3 텍스트 인코더를 사용한다."""
        path = Path("comfyui_workflows/t2v_ltx2.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        gemma_nodes = [
            node for node in data.values()
            if node.get("class_type") == "LTXVGemmaCLIPModelLoader"
        ]
        assert len(gemma_nodes) == 1
        assert "gemma" in gemma_nodes[0]["inputs"]["gemma_path"].lower()

    def test_all_workflows_use_fp8_checkpoints(self):
        """모든 워크플로우의 LTX-2 체크포인트가 FP8이다 (FP4 미사용 확인)."""
        valid_ckpts = {
            "ltx-2-19b-dev-fp8.safetensors",
            "ltx-2-19b-distilled-fp8.safetensors",
        }
        # ckpt_name, ltxv_path: LTX-2 체크포인트를 참조하는 키
        ckpt_keys = {"ckpt_name", "ltxv_path"}
        for wf_name in ["t2v_ltx2.json", "i2v_ltx2.json",
                        "t2v_ltx2_distilled.json", "t2v_ltx2_upscale.json"]:
            path = Path("comfyui_workflows") / wf_name
            data = json.loads(path.read_text(encoding="utf-8"))
            for node_id, node_data in data.items():
                if not isinstance(node_data, dict) or "inputs" not in node_data:
                    continue
                for key, value in node_data["inputs"].items():
                    if key in ckpt_keys and isinstance(value, str):
                        assert value in valid_ckpts, (
                            f"{wf_name} 노드 {node_id}.{key}에 잘못된 체크포인트: {value}"
                        )
                        assert "fp4" not in value, (
                            f"{wf_name}에 FP4 체크포인트 발견: {value} "
                            "(RTX 3090 Ampere에서 FP4 하드웨어 가속 불가)"
                        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
