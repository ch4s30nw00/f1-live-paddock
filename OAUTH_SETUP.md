# 구글 로그인 설정 방법

커뮤니티 로그인은 **구글 개발자 콘솔에서 발급받은 키**가 있어야 동작합니다.
(이 키는 본인 계정으로만 발급 가능하고, 코드로 자동 생성할 수 없어요.)

발급받은 값을 프로젝트 폴더의 **`oauth_config.json`** 에 채운 뒤 서버를 재시작하면 됩니다.

```json
{
  "base_url": "http://localhost:8000",
  "google": { "client_id": "...", "client_secret": "..." }
}
```

> 서버를 다른 주소/포트로 열면 `base_url` 을 그에 맞게 바꾸세요. (redirect URI도 그 주소로 등록해야 함)

---

## 1) 구글 로그인

1. https://console.cloud.google.com/ 접속 → 상단에서 **프로젝트 생성**
2. 좌측 **API 및 서비스 → OAuth 동의 화면**
   - User Type: **외부(External)** 선택 → 앱 이름/이메일 입력
   - **테스트 사용자**에 본인 구글 계정 추가 (게시 안 해도 테스트 가능)
3. **API 및 서비스 → 사용자 인증 정보 → 사용자 인증 정보 만들기 → OAuth 클라이언트 ID**
   - 애플리케이션 유형: **웹 애플리케이션**
   - **승인된 리디렉션 URI**에 정확히 추가:
     ```
     http://localhost:8000/auth/google/callback
     ```
4. 생성되면 뜨는 **클라이언트 ID**와 **클라이언트 보안 비밀**을
   `oauth_config.json` 의 `google.client_id`, `google.client_secret` 에 붙여넣기

---

## 2) 적용

`oauth_config.json` 을 저장한 뒤 서버 재시작:

```
py -m uvicorn server:app
```

커뮤니티 탭의 로그인 버튼이 활성화됩니다.

> 참고: 로그인 세션은 서버 메모리에 저장돼서, **서버를 재시작하면 로그아웃**됩니다. (게시글은 DB에 영구 저장)
>
> 주의: `oauth_config.json` 에는 비밀키가 들어가므로 **git에 절대 커밋하지 마세요.** (`.gitignore`에 추가)
