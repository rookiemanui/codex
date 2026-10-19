"""Persistent owner password; TLS is supplied by a reverse proxy in remote mode."""
import hashlib
import json
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from .store import Problem

ITERATIONS = 600_000


def set_password(directory, password):
    if len(password) < 12 or len(password) > 256 or len(set(password)) < 5:
        raise Problem('访问密码需要12至256个字符，并且不能过于简单')
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    salt = secrets.token_hex(24)
    data = {'scheme': 'pbkdf2_sha256', 'iterations': ITERATIONS, 'salt': salt,
            'hash': hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), ITERATIONS).hex()}
    path = root / 'access.json'
    temporary = path.with_suffix('.tmp')
    # Exclusive creation with owner-only permissions on POSIX.
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(data, stream)
    temporary.replace(path)
    if os.name != 'nt':
        path.chmod(0o600)


def load_password(directory):
    path = Path(directory) / 'access.json'
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('scheme') != 'pbkdf2_sha256' or not ITERATIONS <= value.get('iterations', 0) <= 2_000_000:
        raise Problem('密码配置损坏；请在本机使用 --set-password 重设')
    try:
        if len(bytes.fromhex(value['salt'])) < 16 or len(bytes.fromhex(value['hash'])) != 32:
            raise ValueError()
    except (KeyError, ValueError, TypeError) as exc:
        raise Problem('密码配置损坏；请在本机使用 --set-password 重设') from exc
    return value


def verify_password(value, password):
    if not isinstance(password, str) or len(password) > 256:
        return False
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(value['salt']), value['iterations']).hex()
    return secrets.compare_digest(digest, value['hash'])


def public_origin(url):
    if not url:
        return ''
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.path not in ['', '/'] or parts.query or parts.fragment:
        raise Problem('外网地址必须是 HTTPS 域名根地址，例如 https://ledger.example.com')
    return 'https://' + parts.netloc.lower()
