"""Settings loaded from the environment and an optional .env file.

The .env file is looked up in the repository root (the directory containing
``pyproject.toml``) so that ``glc`` works from any subdirectory. See
``env.default`` for the documented variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def repo_root() -> Path:
    """Return the directory containing pyproject.toml, or the cwd as a fallback."""
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


@dataclass(frozen=True)
class Settings:
    api_url: str
    api_token: str
    db_path: Path

    @classmethod
    def load(cls) -> "Settings":
        root = repo_root()
        load_dotenv(root / ".env")
        api_url = os.environ.get("GOODLINKS_API_URL", "http://localhost:9428/api/v1").rstrip("/")
        api_token = os.environ.get("GOODLINKS_API_TOKEN", "")
        db_raw = os.environ.get("GLC_DB_PATH", "data/goodlinks.sqlite")
        db_path = Path(db_raw)
        if not db_path.is_absolute():
            db_path = root / db_path
        return cls(api_url=api_url, api_token=api_token, db_path=db_path)
