from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Optional

_dotenv_loaded_flag = False

def _ensure_dotenv_loaded() -> None:
    global _dotenv_loaded_flag
    if _dotenv_loaded_flag:
        return
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv()  # lädt .env falls vorhanden
    except Exception:
        pass
    _dotenv_loaded_flag = True

@dataclass
class AppConfig:
    api_key: Optional[str]
    model: str = "gemma2-9b-it"
    api_base: str = "https://api.groq.com/openai/v1"
    timeout: int = 30


def load_config() -> AppConfig:
    _ensure_dotenv_loaded()
    return AppConfig(
        api_key=os.getenv("API_KEY"),
    model=os.getenv("GROQ_MODEL", "gemma2-9b-it"),
        api_base=os.getenv("GROQ_API_BASE", "https://api.groq.com/openai/v1"),
        timeout=int(os.getenv("GROQ_TIMEOUT", "30")),
    )
