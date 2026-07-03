"""LinkNote 인증 모듈 (표준 라이브러리만 사용).
- 비밀번호: PBKDF2-HMAC-SHA256 + 솔트
- 토큰: HMAC-SHA256 서명 (JWT 유사, 의존성 없음)
- 사용자 저장: data/users.json
각 사용자는 data_user_id 를 가지며, 이 값이 ChromaDB/자료의 user_id 로 쓰인다.
기존 자료 네임스페이스 연결(link_user_id)은 maintainer/admin 마이그레이션 용도로만 허용한다.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid

DATA_DIR = os.getenv("DATA_DIR", "./data")
USERS_PATH = os.path.join(DATA_DIR, "users.json")
_SECRET_PATH = os.path.join(DATA_DIR, ".auth_secret")
TOKEN_TTL = 60 * 60 * 24 * 30  # 30일
ALLOWED_STUDENT_TRACKS = {"general", "nursing"}

MAINTAINER_EMAILS = {
    email.strip().lower()
    for email in os.getenv("MAINTAINER_EMAILS", "kory124@snu.ac.kr").split(",")
    if email.strip()
}

# 스터디 기능(논문 질문·주장 근거화·CareFlow 자료 가져오기) 사용 가능 계정.
# 기본값은 관리자 계정 — 본인 전용. 다른 계정을 추가하려면 STUDY_EMAILS 환경변수 사용.
STUDY_EMAILS = {
    email.strip().lower()
    for email in os.getenv("STUDY_EMAILS", "").split(",")
    if email.strip()
} or set(MAINTAINER_EMAILS)


def is_study_enabled(user: dict) -> bool:
    return (user.get("email") or "").strip().lower() in STUDY_EMAILS


def _normalize_student_track(value: str = "") -> str:
    track = (value or "").strip().lower()
    return track if track in ALLOWED_STUDENT_TRACKS else "general"


def _new_data_user_id() -> str:
    return uuid.uuid4().hex


def _resolve_data_user_id(email: str, link_user_id: str = "") -> tuple[str, str | None]:
    legacy_id = (link_user_id or "").strip()
    if legacy_id:
        if email not in MAINTAINER_EMAILS:
            return "", "link_user_id는 관리자 마이그레이션 용도로만 사용할 수 있습니다."
        return legacy_id, None
    return _new_data_user_id(), None


def _get_secret() -> bytes:
    env = os.getenv("AUTH_SECRET")
    if env:
        return env.encode("utf-8")
    if os.path.exists(_SECRET_PATH):
        with open(_SECRET_PATH, "r", encoding="utf-8") as f:
            return f.read().strip().encode("utf-8")
    os.makedirs(os.path.dirname(_SECRET_PATH), exist_ok=True)
    s = secrets.token_hex(32)
    with open(_SECRET_PATH, "w", encoding="utf-8") as f:
        f.write(s)
    return s.encode("utf-8")


# ---------- 저장소 ----------
def _load_users() -> dict:
    if not os.path.exists(USERS_PATH):
        return {}
    try:
        with open(USERS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_users(users: dict) -> None:
    os.makedirs(os.path.dirname(USERS_PATH), exist_ok=True)
    with open(USERS_PATH, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


# ---------- 비밀번호 ----------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    iters = 200_000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
    return f"pbkdf2_sha256${iters}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# ---------- 토큰 ----------
def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("utf-8").rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def create_token(uid: str) -> str:
    payload = {"uid": uid, "exp": int(time.time()) + TOKEN_TTL}
    body = _b64e(json.dumps(payload).encode("utf-8"))
    sig = _b64e(hmac.new(_get_secret(), body.encode("utf-8"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_token(token: str):
    try:
        body, sig = token.split(".")
        expected = _b64e(hmac.new(_get_secret(), body.encode("utf-8"), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64d(body))
        if payload.get("exp", 0) < int(time.time()):
            return None
        return payload.get("uid")
    except Exception:
        return None


# ---------- 사용자 ----------
def _public(user: dict) -> dict:
    provider = "google" if user.get("google_sub") else "password"
    created = user.get("created")
    return {
        "id": user["id"],
        "account_id": user["id"],
        "email": user["email"],
        "display_name": user.get("display_name", ""),
        "auth_provider": provider,
        "login_method": provider,
        "data_user_id": user.get("data_user_id", user["email"]),
        "student_track": _normalize_student_track(user.get("student_track", "general")),
        "study_enabled": is_study_enabled(user),
        "created": created,
        "created_at": created,
        "joined_at": created,
    }


def get_user_by_id(uid: str):
    for u in _load_users().values():
        if u["id"] == uid:
            return u
    return None


def register_user(email: str, password: str, display_name: str = "", link_user_id: str = "", student_track: str = ""):
    email = (email or "").strip().lower()
    if not email or not password:
        return None, "이메일과 비밀번호가 필요합니다."
    users = _load_users()
    if email in users:
        return None, "이미 가입된 이메일입니다."
    data_user_id, err = _resolve_data_user_id(email, link_user_id)
    if err:
        return None, err
    user = {
        "id": uuid.uuid4().hex,
        "email": email,
        "password_hash": hash_password(password),
        "display_name": display_name or email.split("@")[0],
        "google_sub": None,
        "data_user_id": data_user_id,
        "student_track": _normalize_student_track(student_track),
        "created": int(time.time()),
    }
    users[email] = user
    _save_users(users)
    return user, None


def authenticate(email: str, password: str):
    email = (email or "").strip().lower()
    users = _load_users()
    user = users.get(email)
    if not user or not verify_password(password, user.get("password_hash", "")):
        return None, "이메일 또는 비밀번호가 올바르지 않습니다."
    return user, None


def upsert_google_user(email: str, google_sub: str, display_name: str = "", link_user_id: str = "", student_track: str = ""):
    """Google 로그인용 — 이메일로 찾거나 새로 만든다."""
    email = (email or "").strip().lower()
    users = _load_users()
    user = users.get(email)
    if user:
        if not user.get("google_sub"):
            user["google_sub"] = google_sub
            _save_users(users)
        return user
    data_user_id, err = _resolve_data_user_id(email, link_user_id)
    if err:
        raise ValueError(err)
    user = {
        "id": uuid.uuid4().hex,
        "email": email,
        "password_hash": "",
        "display_name": display_name or email.split("@")[0],
        "google_sub": google_sub,
        "data_user_id": data_user_id,
        "student_track": _normalize_student_track(student_track),
        "created": int(time.time()),
    }
    users[email] = user
    _save_users(users)
    return user


def public_user(user: dict) -> dict:
    return _public(user)
