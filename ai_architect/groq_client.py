"""
ai_architect/groq_client.py
Isolated Groq API client with timeouts, bounded retries, and comprehensive error mapping.
Never exposes API keys or sensitive headers in exceptions or logs.
"""

import os
import json
import re
import time
from typing import Dict, Any, Optional

try:
    from dotenv import load_dotenv
    # Load .env if present
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
except ImportError:
    pass

try:
    from groq import (
        Groq,
        APIError,
        AuthenticationError,
        RateLimitError,
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        BadRequestError,
    )
    _GROQ_SDK_AVAILABLE = True
except ImportError:
    _GROQ_SDK_AVAILABLE = False


class GroqProviderError(Exception):
    """Base exception for Groq client errors with sanitized messages."""
    def __init__(self, message: str, status_code: Optional[int] = None, is_retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.is_retryable = is_retryable


class GroqAuthError(GroqProviderError):
    """Raised when Groq API key is invalid or unauthorized (401/403). Non-retryable."""
    def __init__(self, message: str = "Invalid or unauthorized Groq API key."):
        super().__init__(message, status_code=401, is_retryable=False)


class GroqRateLimitError(GroqProviderError):
    """Raised when rate limits or quotas are exceeded (429)."""
    def __init__(self, message: str = "Groq rate limit or quota exceeded."):
        super().__init__(message, status_code=429, is_retryable=True)


class GroqTimeoutError(GroqProviderError):
    """Raised when Groq request exceeds the configured timeout."""
    def __init__(self, message: str = "Groq API request timed out."):
        super().__init__(message, status_code=408, is_retryable=True)


class GroqConnectionError(GroqProviderError):
    """Raised when network or DNS connection fails."""
    def __init__(self, message: str = "Unable to connect to Groq API endpoint."):
        super().__init__(message, status_code=503, is_retryable=True)


class GroqResponseError(GroqProviderError):
    """Raised when Groq response is malformed, truncated, or invalid JSON."""
    def __init__(self, message: str = "Groq returned a malformed or non-JSON response."):
        super().__init__(message, status_code=502, is_retryable=False)


class GroqClient:
    """
    Isolated wrapper around Groq API.
    Handles credential isolation, bounded retries, timeout management, and JSON extraction.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        enabled: Optional[bool] = None,
    ):
        if api_key is not None:
            self.api_key = api_key.strip()
        else:
            self.api_key = os.getenv("GROQ_API_KEY", "").strip()

        self.model = model or os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()

        env_timeout = os.getenv("GROQ_TIMEOUT_SECONDS", "30")
        try:
            self.timeout = float(timeout if timeout is not None else env_timeout)
        except ValueError:
            self.timeout = 30.0

        if enabled is not None:
            self.enabled = enabled
        else:
            env_enabled = os.getenv("PROJECTDNA_AI_ENABLED", "true").lower()
            self.enabled = env_enabled in ("true", "1", "yes", "on")

        self.max_retries = 2
        self._client: Optional[Any] = None

    def is_available(self) -> bool:
        """Return True only if AI is enabled, SDK is available, and an API key is configured."""
        return bool(self.enabled and _GROQ_SDK_AVAILABLE and self.api_key)

    def _get_client(self) -> Any:
        if not _GROQ_SDK_AVAILABLE:
            raise GroqProviderError("Groq Python SDK is not installed.")
        if not self.api_key:
            raise GroqAuthError("No Groq API key provided or found in environment.")
        if self._client is None:
            self._client = Groq(api_key=self.api_key, timeout=self.timeout)
        return self._client

    def chat_completion_json(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """
        Send chat completion request expecting a JSON response.
        Applies timeout, bounded retries on transient errors, and sanitizes errors.
        """
        if not self.is_available():
            raise GroqProviderError("Groq client is not available or AI is disabled.")

        client = self._get_client()
        attempts = 0
        last_error: Optional[Exception] = None

        while attempts < self.max_retries:
            attempts += 1
            try:
                response = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    timeout=self.timeout,
                    temperature=0.2,
                )

                if not response.choices or not response.choices[0].message:
                    raise GroqResponseError("Groq returned an empty response choices list.")

                content = response.choices[0].message.content or ""
                return self._parse_json_content(content)

            except AuthenticationError as e:
                # 401/403: Permanent error. Fast fail, no retry.
                raise GroqAuthError(f"Groq authentication failed: {self._sanitize_error(e)}")

            except BadRequestError as e:
                # Invalid model or bad request parameter: Non-retryable
                msg = self._sanitize_error(e)
                raise GroqProviderError(f"Groq bad request: {msg}", status_code=400, is_retryable=False)

            except RateLimitError as e:
                last_error = GroqRateLimitError(f"Groq rate limit: {self._sanitize_error(e)}")
                if attempts < self.max_retries:
                    time.sleep(1.0 * attempts)
                    continue
                raise last_error

            except APITimeoutError as e:
                last_error = GroqTimeoutError(f"Groq request timed out after {self.timeout}s: {self._sanitize_error(e)}")
                if attempts < self.max_retries:
                    time.sleep(1.0)
                    continue
                raise last_error

            except (APIConnectionError, ConnectionError) as e:
                last_error = GroqConnectionError(f"Groq connection failure: {self._sanitize_error(e)}")
                if attempts < self.max_retries:
                    time.sleep(1.0)
                    continue
                raise last_error

            except InternalServerError as e:
                last_error = GroqProviderError(f"Groq internal server error: {self._sanitize_error(e)}", status_code=500, is_retryable=True)
                if attempts < self.max_retries:
                    time.sleep(1.0)
                    continue
                raise last_error

            except APIError as e:
                msg = self._sanitize_error(e)
                raise GroqProviderError(f"Groq API error: {msg}")

            except json.JSONDecodeError as e:
                raise GroqResponseError(f"Failed to parse JSON from Groq response: {e}")

        if last_error:
            raise last_error
        raise GroqProviderError("Groq request failed after retries.")

    def _parse_json_content(self, content: str) -> Dict[str, Any]:
        """Strip markdown fences if present and parse JSON."""
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            # Fallback: search for first { and last }
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(cleaned[start:end + 1])
                except json.JSONDecodeError:
                    pass
            raise GroqResponseError(f"Could not parse valid JSON from content: {e}")

    def _sanitize_error(self, err: Exception) -> str:
        """Ensure no API key or sensitive token appears in error messages."""
        msg = str(err)
        if self.api_key and len(self.api_key) > 6:
            msg = msg.replace(self.api_key, "[REDACTED_API_KEY]")
        # Regex redact common key patterns
        msg = re.sub(r"gsk_[a-zA-Z0-9_-]{20,}", "[REDACTED_API_KEY]", msg)
        msg = re.sub(r"Bearer\s+[a-zA-Z0-9_\-\.]{15,}", "Bearer [REDACTED]", msg)
        return msg
