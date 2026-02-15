# Podman에서 Docker Compose로 마이그레이션 가이드

## 📋 개요

이 가이드는 WaggleBot을 Podman Compose에서 Docker Compose로 전환하는 방법을 설명합니다.

### 왜 Docker로 전환하나요?

**Podman의 문제점 (개발 환경):**
- ❌ **데몬리스 아키텍처**: 매번 새로운 프로세스 생성 및 시스템 상태 점검
- ❌ **느린 빌드 속도**: 소스 코드 변경 시 빌드 시간 증가
- ❌ **캐싱 비효율**: Docker의 레이어 캐싱에 비해 불리

**Docker의 장점 (개발 환경):**
- ✅ **데몬 기반**: 빠른 빌드 및 실행
- ✅ **효율적인 캐싱**: 레이어 캐싱으로 빌드 시간 단축
- ✅ **볼륨 마운트**: 소스 코드 변경 시 재빌드 불필요

**주의:** Podman은 프로덕션 환경에서는 여전히 유용합니다. 하지만 개발 빈도가 높은 환경에서는 Docker가 더 적합합니다.

---

## 🔧 마이그레이션 단계

### 1단계: 기존 Podman 컨테이너 중지 및 제거

```bash
# 모든 Podman 컨테이너 중지
sudo podman-compose down

# 또는 수동으로 중지
sudo podman stop $(sudo podman ps -aq)
sudo podman rm $(sudo podman ps -aq)

# 볼륨 확인 (선택사항 - 데이터 백업 필요 시)
sudo podman volume ls
```

**주의:** 데이터베이스 데이터를 보존하려면 백업하세요!

```bash
# MariaDB 백업
sudo podman exec wagglebot_db_1 mysqldump -u root -p${DB_ROOT_PASSWORD} wagglebot > backup.sql
```

### 2단계: Docker 설치 확인

```bash
# Docker 설치 확인
docker --version

# 출력 예시: Docker version 24.0.x, build xxxxx
```

**Docker가 설치되지 않았다면:**

```bash
# Docker 공식 GPG 키 추가
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Docker 저장소 추가
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Docker 설치
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Docker 서비스 시작
sudo systemctl start docker
sudo systemctl enable docker

# 현재 사용자를 docker 그룹에 추가 (sudo 없이 docker 명령 실행)
sudo usermod -aG docker $USER

# 재로그인 또는 다음 명령 실행
newgrp docker
```

### 3단계: NVIDIA Container Toolkit 설정

```bash
# 자동 설정 스크립트 실행
cd /home/justant/Data/WaggleBot
bash scripts/setup_docker_gpu.sh
```

**스크립트가 수행하는 작업:**
1. ✅ NVIDIA GPU 확인
2. ✅ Docker 설치 확인
3. ✅ NVIDIA Container Toolkit Repository 추가
4. ✅ NVIDIA Container Toolkit 설치
5. ✅ Docker 데몬 설정 및 재시작
6. ✅ GPU 접근 테스트

**수동 설정 (스크립트 실패 시):**

```bash
# 1. NVIDIA Container Toolkit Repository 추가
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

# 2. 설치
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit

# 3. Docker 데몬 설정
sudo nvidia-ctk runtime configure --runtime=docker

# 4. Docker 재시작
sudo systemctl restart docker

# 5. 테스트
docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi
```

### 4단계: docker-compose.yml 검증

수정된 `docker-compose.yml`이 올바른지 확인하세요:

```bash
# docker-compose.yml 문법 검증
docker compose config

# 출력이 에러 없이 표시되면 성공
```

**주요 변경사항:**

1. **GPU 설정 (ai_worker 서비스)**
   ```yaml
   # Podman CDI 방식 (제거됨)
   devices:
     - nvidia.com/gpu=all

   # Docker 표준 방식 (새로 추가)
   deploy:
     resources:
       reservations:
         devices:
           - driver: nvidia
             count: all
             capabilities: [gpu]
   ```

2. **네트워크 설정 (ai_worker 서비스)**
   ```yaml
   # Podman host 모드 (제거됨)
   network_mode: host
   environment:
     OLLAMA_HOST: "http://localhost:11434"
     DATABASE_URL: "...@127.0.0.1/..."

   # Docker 브리지 모드 + extra_hosts (새로 추가)
   extra_hosts:
     - "host.docker.internal:host-gateway"
   environment:
     OLLAMA_HOST: "http://host.docker.internal:11434"
     DATABASE_URL: "...@db/..."
   ```

3. **개발용 볼륨 마운트 (모든 서비스)**
   ```yaml
   volumes:
     - ./:/app              # 소스 코드 실시간 반영
     - /app/venv            # 로컬 venv 보호
     - /app/__pycache__     # 캐시 충돌 방지
   ```

### 5단계: Docker Compose 실행

```bash
cd /home/justant/Data/WaggleBot

# 컨테이너 빌드 및 시작 (최초 1회는 시간 소요)
docker compose up -d

# 서비스 상태 확인
docker compose ps

# 예상 출력:
# NAME                      STATUS         PORTS
# wagglebot-db-1            running        0.0.0.0:3306->3306/tcp
# wagglebot-crawler-1       running
# wagglebot-ai_worker-1     running
# wagglebot-dashboard-1     running        0.0.0.0:8501->8501/tcp
# wagglebot-monitoring-1    running
```

### 6단계: GPU 인식 확인

```bash
# ai_worker 컨테이너에서 GPU 확인
docker exec wagglebot-ai_worker-1 python3 -c \
  "import torch; print('CUDA:', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A')"

# 예상 출력:
# CUDA: True NVIDIA GeForce RTX 3080 Ti
```

### 7단계: Ollama 연결 확인

```bash
# ai_worker 로그 확인
docker logs wagglebot-ai_worker-1 2>&1 | grep -i ollama

# Ollama 연결 테스트
docker exec wagglebot-ai_worker-1 curl -s http://host.docker.internal:11434/api/tags
```

### 8단계: 데이터베이스 복원 (백업한 경우)

```bash
# 백업한 데이터베이스 복원
cat backup.sql | docker exec -i wagglebot-db-1 mariadb -u root -p${DB_ROOT_PASSWORD} wagglebot
```

---

## 📊 명령어 비교표

| 작업 | Podman Compose | Docker Compose |
|------|----------------|----------------|
| **시작** | `sudo podman-compose up -d` | `docker compose up -d` |
| **중지** | `sudo podman-compose down` | `docker compose down` |
| **로그** | `sudo podman logs -f <container>` | `docker logs -f <container>` |
| **상태** | `sudo podman ps` | `docker compose ps` |
| **재시작** | `sudo podman restart <container>` | `docker restart <container>` |
| **쉘 접속** | `sudo podman exec -it <container> bash` | `docker exec -it <container> bash` |
| **이미지 빌드** | `sudo podman-compose build` | `docker compose build` |
| **볼륨 확인** | `sudo podman volume ls` | `docker volume ls` |

**주의:** Docker는 `docker compose` (V2)를 사용합니다. `docker-compose` (V1, 하이픈 포함)는 deprecated입니다.

---

## 🚀 개발 워크플로우 개선

### 소스 코드 Hot Reload

이제 소스 코드를 수정하면 **재빌드 없이** 즉시 반영됩니다:

```bash
# 1. 로컬에서 코드 수정 (예: ai_worker/llm.py)
nano ai_worker/llm.py

# 2. 컨테이너 재시작 (빌드 없이)
docker restart wagglebot-ai_worker-1

# 3. 로그 확인
docker logs -f wagglebot-ai_worker-1
```

**왜 빠른가요?**
- `./:/app` 볼륨 마운트로 로컬 파일이 컨테이너에 직접 연결됨
- 재빌드 없이 재시작만으로 변경사항 반영

### 빌드가 필요한 경우

의존성(`requirements.txt`)을 변경한 경우에만 재빌드가 필요합니다:

```bash
# requirements.txt 수정 후
docker compose build ai_worker
docker compose up -d ai_worker
```

---

## 🔍 문제 해결

### 문제 1: "Cannot connect to the Docker daemon"

**증상:**
```
Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?
```

**해결:**
```bash
# Docker 서비스 시작
sudo systemctl start docker

# 자동 시작 활성화
sudo systemctl enable docker

# 현재 사용자를 docker 그룹에 추가
sudo usermod -aG docker $USER
newgrp docker
```

### 문제 2: GPU를 인식하지 못함

**증상:**
```
docker.errors.APIError: ... could not select device driver "" with capabilities: [[gpu]]
```

**해결:**
```bash
# NVIDIA Container Toolkit 재설정
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# 테스트
docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi
```

### 문제 3: Ollama 연결 실패

**증상:**
```
ConnectionError: HTTPConnectionPool(host='host.docker.internal', port=11434): Connection refused
```

**해결:**

**Option 1: WSL2 Docker Engine 사용 시 (권장)**
```bash
# docker-compose.yml에서 extra_hosts 확인
extra_hosts:
  - "host.docker.internal:host-gateway"

# 또는 WSL2 IP 직접 사용
export WSL_HOST_IP=$(ip route show | grep -i default | awk '{ print $3}')
# docker-compose.yml에서:
# OLLAMA_HOST: "http://${WSL_HOST_IP}:11434"
```

**Option 2: network_mode: host 사용 (대안)**
```yaml
# docker-compose.yml의 ai_worker 서비스
network_mode: host
environment:
  OLLAMA_HOST: "http://127.0.0.1:11434"
  DATABASE_URL: "mysql+pymysql://wagglebot:password@127.0.0.1/wagglebot"
```

**주의:** `network_mode: host`를 사용하면 다른 서비스와 네트워크 격리가 없어집니다.

### 문제 4: 볼륨 마운트로 인한 권한 문제

**증상:**
```
PermissionError: [Errno 13] Permission denied: '/app/media/...'
```

**해결:**
```bash
# 로컬 디렉토리 권한 설정
sudo chown -R $USER:$USER ./media
chmod -R 755 ./media

# 컨테이너 재시작
docker restart wagglebot-ai_worker-1
```

### 문제 5: 이전 Podman 이미지와 충돌

**증상:**
- 빌드 또는 실행 중 예상치 못한 에러

**해결:**
```bash
# Docker 이미지 및 컨테이너 정리
docker compose down -v
docker system prune -a --volumes

# 재빌드
docker compose build --no-cache
docker compose up -d
```

---

## ✅ 마이그레이션 체크리스트

- [ ] Podman 컨테이너 중지 및 제거
- [ ] 데이터베이스 백업 (필요 시)
- [ ] Docker 설치 확인
- [ ] NVIDIA Container Toolkit 설정
- [ ] `docker-compose.yml` 변경사항 검증
- [ ] Docker Compose 실행
- [ ] GPU 인식 확인
- [ ] Ollama 연결 확인
- [ ] 데이터베이스 복원 (백업한 경우)
- [ ] 대시보드 접속 확인 (http://localhost:8501)
- [ ] AI 워커 로그 확인
- [ ] 크롤러 테스트 실행

---

## 📚 추가 참고 자료

- **Docker Compose 공식 문서**: https://docs.docker.com/compose/
- **NVIDIA Container Toolkit**: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/
- **Docker GPU 지원**: https://docs.docker.com/config/containers/resource_constraints/#gpu

---

## 🎯 다음 단계

마이그레이션이 완료되었다면:

1. **개발 시작**: 소스 코드 수정 후 컨테이너만 재시작
2. **테스트**: `pytest` 실행
3. **커밋**: 변경사항 커밋 및 푸시

```bash
# 개발 워크플로우 예시
# 1. 코드 수정
nano ai_worker/llm.py

# 2. 재시작 (빌드 없음!)
docker restart wagglebot-ai_worker-1

# 3. 로그 확인
docker logs -f wagglebot-ai_worker-1

# 4. 테스트
docker exec wagglebot-ai_worker-1 pytest

# 5. 커밋
git add .
git commit -m "feat: improve LLM summarization"
```

---

**축하합니다! 🎉**
WaggleBot이 이제 Docker Compose 환경에서 더 빠르게 개발할 수 있습니다!
