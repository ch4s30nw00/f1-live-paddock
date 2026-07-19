# 배포 가이드 — Oracle Cloud 무료 VM + Docker + GitHub Actions

전체 흐름: **VM 만들기 → 방화벽 열기 → Docker 설치 → 앱 띄우기 → 도메인·HTTPS → 자동 배포 연결**

한 번 세팅하면 이후에는 `git push`만으로 자동 배포된다.

---

## 1. Oracle Cloud VM 만들기 (브라우저)

1. [oracle.com/cloud/free](https://www.oracle.com/cloud/free/) 가입 (본인 확인용 카드 필요, Always Free 범위는 과금 없음)
2. 콘솔 → Compute → Instances → **Create instance**
   - Image: **Ubuntu 24.04**
   - Shape: **Ampere (VM.Standard.A1.Flex)** — OCPU 2 / RAM 8GB면 충분 (Always Free 한도는 4 OCPU / 24GB)
   - SSH keys: **Generate a key pair for me** → 개인키 다운로드해서 보관 (예: `~/.ssh/oracle.key`)
3. 생성 후 인스턴스 상세 화면의 **Public IP** 를 메모
4. ⚠️ 서울 리전에서 A1이 "Out of capacity" 로 실패하면 → 몇 시간 뒤 재시도하거나 춘천 리전 사용

접속 확인 (내 PC PowerShell):

```powershell
ssh -i ~/.ssh/oracle.key ubuntu@<공인IP>
```

## 2. 방화벽 열기 (두 겹 다 열어야 함)

**① 오라클 콘솔**: 인스턴스 상세 → Virtual cloud network 클릭 → Security Lists → Default Security List → **Add Ingress Rules**:

| Source CIDR | Protocol | Dest. Port |
|---|---|---|
| 0.0.0.0/0 | TCP | 80 |
| 0.0.0.0/0 | TCP | 443 |

**② VM 내부** (SSH 접속 후):

```bash
sudo iptables -I INPUT 5 -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 5 -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

## 3. Docker 설치 (VM에서)

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
exit   # 그룹 적용을 위해 재접속
```

재접속 후 `docker ps` 가 에러 없이 나오면 성공.

## 4. 앱 받아서 띄우기 (VM에서)

```bash
git clone https://github.com/<깃허브아이디>/<레포이름>.git ~/f1
cd ~/f1
docker compose up -d --build     # 첫 빌드는 몇 분 걸림
docker compose exec app python build_live_replay.py   # 폴백 리플레이 생성(1회)
```

브라우저에서 `http://<공인IP>` 접속 → 사이트가 보이면 성공.
(이 시점엔 HTTP 전용이라 구글 로그인은 아직 안 됨 — 5번에서 해결)

```bash
docker compose logs -f app       # 서버 로그 실시간 보기 (Ctrl+C로 종료)
```

## 5. 도메인 + HTTPS

1. 도메인 확보: 유료 도메인(추천) 또는 무료 [DuckDNS](https://www.duckdns.org)
2. DNS **A 레코드**를 VM 공인 IP로 연결
3. VM에서 도메인 설정 후 재시작:

```bash
cd ~/f1
echo "SITE_DOMAIN=예시.duckdns.org" > .env
docker compose up -d
```

DNS가 퍼진 상태면 Caddy가 HTTPS 인증서를 자동 발급한다. `https://도메인` 접속 확인.

**구글 로그인 연결** (커뮤니티 탭 쓰려면):

1. [구글 클라우드 콘솔](https://console.cloud.google.com/apis/credentials) → OAuth 클라이언트 → 승인된 리디렉션 URI에 `https://<도메인>/auth/google/callback` 추가
2. 내 PC에서 `oauth_config.json`을 VM으로 복사하고 재시작:

```powershell
scp -i ~/.ssh/oracle.key oauth_config.json ubuntu@<공인IP>:~/f1/
```

```bash
cd ~/f1 && docker compose restart app
```

## 6. GitHub Actions 자동 배포 연결

GitHub 레포 → Settings → Secrets and variables → Actions → **New repository secret** 3개:

| 이름 | 값 |
|---|---|
| `VM_HOST` | VM 공인 IP |
| `VM_USER` | `ubuntu` |
| `VM_SSH_KEY` | SSH **개인키 파일 내용 전체** (`-----BEGIN`부터 `-----END`까지) |

이후 `main`에 push하면 [deploy.yml](../.github/workflows/deploy.yml)이 VM에 접속해 `git pull` + 재빌드를 자동 실행한다. 레포의 Actions 탭에서 진행 상황을 볼 수 있다.

---

## 운영 메모

- **데이터 위치**: DB(`f1_database.db`)·녹화(`recordings/`)·결과 캐시(`results_cache/`)는 전부 `~/f1` 안에 있고 컨테이너에 볼륨으로 마운트되므로, 재배포·재부팅해도 유지된다. `~/f1`만 백업하면 끝.
- **수동 재배포**: VM에서 `cd ~/f1 && git pull && docker compose up -d --build`
- **전체 재시작**: `docker compose restart`
- **VM 재부팅 시**: `restart: unless-stopped` 덕에 컨테이너가 자동으로 다시 뜬다.
