import os
from pathlib import Path
from dotenv import load_dotenv
from cryptography.fernet import Fernet

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme")
SECRET_KEY = os.getenv("SECRET_KEY", "change-this-to-a-random-secret-key")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{BASE_DIR / 'deployer.db'}")
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(BASE_DIR / "uploads")))

_fernet_key = os.getenv("FERNET_KEY")
if not _fernet_key:
    _fernet_key = Fernet.generate_key().decode()
    print(f"WARNING: No FERNET_KEY set. Generated ephemeral key. Set FERNET_KEY={_fernet_key} in .env for persistence.")

fernet = Fernet(_fernet_key.encode() if isinstance(_fernet_key, str) else _fernet_key)

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
