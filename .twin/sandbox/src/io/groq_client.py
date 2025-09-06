from __future__ import annotations
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any
import urllib.request
import urllib.error
from .config import load_config
from ..core.models import Message

class GroqClient:
    def __init__(self) -> None:
        self.cfg = load_config()
        if not self.cfg.api_key:
            # Allow dry operation without key
            pass

    def chat_completion(self, messages: List[Message], max_retries: int = 2) -> str:
        if not self.cfg.api_key:
            return "[dry-run:no-key]"
        url = f"{self.cfg.api_base}/chat/completions"
        payload: Dict[str, Any] = {
            "model": self.cfg.model,
            "messages": [
                {"role": m.role, "content": m.content} for m in messages
            ],
            "temperature": 0.7,
            "max_tokens": 512,
        }
        data = json.dumps(payload).encode("utf-8")
        for attempt in range(max_retries + 1):
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", f"Bearer {self.cfg.api_key}")
            req.add_header("User-Agent", "EvolutionSystem/0.1 (+github example)")
            try:
                with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                    raw = resp.read().decode("utf-8", errors="ignore")
                parsed = json.loads(raw)
                # Standard OpenAI-kompatible Struktur
                return parsed.get("choices", [{}])[0].get("message", {}).get("content", "")
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="ignore")
                except Exception:
                    pass
                # Modell nicht gefunden -> einfacher Fallback-Versuch mit anderem Modellnamen
                if e.code == 404 and "model_not_found" in body:
                    # Versuche zuerst bekannten funktionierenden Default
                    alt_models = [
                        "gemma2-9b-it",
                        "llama3-70b-8192",  # Beispiel großer Name
                        "llama3-8b-8192",   # 8B Variante mit Kontextsuffix
                        "mixtral-8x7b-32768",
                    ]
                    for alt in alt_models:
                        try:
                            self.cfg.model = alt  # temporär
                            alt_payload: Dict[str, Any] = {
                                "model": alt,
                                "messages": payload["messages"],
                                "temperature": payload["temperature"],
                                "max_tokens": payload["max_tokens"],
                            }
                            alt_data = json.dumps(alt_payload).encode("utf-8")
                            alt_req = urllib.request.Request(url, data=alt_data, method="POST")
                            alt_req.add_header("Content-Type", "application/json")
                            alt_req.add_header("Authorization", f"Bearer {self.cfg.api_key}")
                            alt_req.add_header("User-Agent", "EvolutionSystem/0.1 (+github example)")
                            with urllib.request.urlopen(alt_req, timeout=self.cfg.timeout) as r2:
                                alt_raw = r2.read().decode("utf-8", errors="ignore")
                            try:
                                parsed_alt = json.loads(alt_raw)
                                return parsed_alt.get("choices", [{}])[0].get("message", {}).get("content", "")
                            except Exception:
                                return alt_raw[:500]
                        except urllib.error.HTTPError:
                            continue
                        except Exception:
                            continue
                if e.code == 429 and attempt < max_retries:
                    time.sleep(2 ** attempt)
                    continue
                # Log details for diagnostics
                try:  # pragma: no cover
                    logs = Path("logs")
                    logs.mkdir(exist_ok=True)
                    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
                    (logs / f"groq_http_error_{stamp}.json").write_text(json.dumps({
                        "status": e.code,
                        "url": url,
                        "payload": payload,
                        "response": body[:4000]
                    }, indent=2), encoding="utf-8")
                except Exception:
                    pass
                return f"[http-error {e.code}]"
            except Exception:
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
                    continue
                return "[network-error]"
        return "[unreachable]"

    # ------------------------------------------------ Models ------------------------------------------------ #
    def list_models(self) -> str:
        """Fetch available models (OpenAI compatible /models). Returns raw JSON string or error code tag."""
        if not self.cfg.api_key:
            return "[dry-run:no-key]"
        url = f"{self.cfg.api_base}/models"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {self.cfg.api_key}")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
            # Log
            try:  # pragma: no cover
                logs = Path("logs"); logs.mkdir(exist_ok=True)
                (logs / "groq_models_last.json").write_text(raw, encoding="utf-8")
            except Exception:
                pass
            return raw
        except urllib.error.HTTPError as e:
            tag = f"[http-error {e.code}]"
            try:
                body = e.read().decode("utf-8", errors="ignore")
                logs = Path("logs"); logs.mkdir(exist_ok=True)
                (logs / "groq_models_error.json").write_text(body, encoding="utf-8")
            except Exception:
                pass
            return tag
        except Exception:
            return "[network-error]"
