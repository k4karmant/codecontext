"""
codecontext/describer/ollama_client.py

Thin wrapper around the Ollama REST API.
Retries up to 3 times with exponential backoff on transient failures.
"""
from __future__ import annotations

import time
import requests

from codecontext.config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT


class OllamaError(RuntimeError):
    """Raised when Ollama is unreachable or returns an error after all retries."""


class OllamaClient:
    """Synchronous Ollama client.

    Usage::

        client = OllamaClient()
        description = client.generate("Describe this function: ...")
    """

    def __init__(
        self,
        base_url: str = OLLAMA_BASE_URL,
        model: str = OLLAMA_MODEL,
        timeout: int = OLLAMA_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._generate_url = f"{self.base_url}/api/generate"

    def is_available(self) -> bool:
        """Return True if the Ollama server is reachable (any response on /api/tags)."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def is_model_available(self) -> bool:
        """Return True if *self.model* is pulled and listed by Ollama."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if resp.status_code != 200:
                return False
            models = resp.json().get("models", [])
            # model names are like "gemma4:e2b" — match on name field
            pulled = {m.get("name", "").split(":")[0] + ":" + m.get("name", "").split(":")[-1]
                      for m in models}
            # also try exact match and prefix match
            for m in models:
                name = m.get("name", "")
                if name == self.model or name.startswith(self.model.split(":")[0]):
                    return True
            return False
        except Exception:
            return False

    def list_models(self) -> list[str]:
        """Return the list of model names currently pulled in Ollama."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if resp.status_code != 200:
                return []
            return [m.get("name", "") for m in resp.json().get("models", [])]
        except Exception:
            return []

    def generate(self, prompt: str, retries: int = 3) -> str:
        """Send *prompt* to the model and return the generated text.

        Retries up to *retries* times with exponential backoff (1s, 2s, 4s).

        Raises:
            OllamaError: If all attempts fail.
        """
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }

        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                resp = requests.post(
                    self._generate_url,
                    json=payload,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("response", "").strip()
            except Exception as exc:
                last_exc = exc
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)

        raise OllamaError(
            f"Ollama generate failed after {retries} attempts: {last_exc}"
        ) from last_exc
