"""Client for the WeFlow HTTP API."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class WeFlowAPIError(RuntimeError):
    status_code: int
    body: str

    def __str__(self) -> str:
        return f"WeFlow API request failed: HTTP {self.status_code}: {self.body}"


class WeFlowClient:
    def __init__(self, base_url: str = "http://localhost:5031", access_token: str | None = None, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token or None
        self.timeout = timeout

    def health_check(self) -> bool:
        try:
            data = self._request_json("/health", auth=False, timeout=10)
        except Exception:
            return False
        return data.get("status") == "ok"

    def get_sessions(self, keyword: str = "", limit: int = 5000) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if keyword:
            params["keyword"] = keyword
        data = self._request_json("/api/v1/sessions", params=params)
        return data.get("sessions", [])

    def get_contacts(self, keyword: str = "", limit: int = 5000) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if keyword:
            params["keyword"] = keyword
        data = self._request_json("/api/v1/contacts", params=params)
        return data.get("contacts", [])

    def get_messages(
        self,
        talker: str,
        limit: int = 10000,
        offset: int = 0,
        media: bool = True,
        start: str = "",
        end: str = "",
    ) -> tuple[list[dict[str, Any]], bool]:
        params: dict[str, Any] = {
            "talker": talker,
            "limit": limit,
            "offset": offset,
            "media": "1" if media else "0",
        }
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        data = self._request_json("/api/v1/messages", params=params)
        return data.get("messages", []), bool(data.get("hasMore", False))

    def get_all_messages(self, talker: str, media: bool = True, page_size: int = 20) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        offset = 0
        while True:
            page, has_more = self.get_messages(talker, limit=page_size, offset=offset, media=media)
            messages.extend(page)
            if not has_more or not page:
                break
            offset += len(page)
        return messages

    def download_media(self, relative_path: str, dest_path: str) -> bool:
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        request = self._build_request(f"/api/v1/media/{relative_path}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                with dest.open("wb") as fh:
                    while True:
                        chunk = response.read(8192)
                        if not chunk:
                            break
                        fh.write(chunk)
        except Exception:
            return False
        return True

    def download_media_url(self, media_url: str, dest_path: str) -> bool:
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        request = self._build_request_from_url(media_url)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                with dest.open("wb") as fh:
                    while True:
                        chunk = response.read(8192)
                        if not chunk:
                            break
                        fh.write(chunk)
        except Exception:
            return False
        return True

    def _request_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        auth: bool = True,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        request = self._build_request(path, params=params, auth=auth)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise WeFlowAPIError(exc.code, body) from exc
        if not raw:
            return {}
        return json.loads(raw)

    def _build_request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> urllib.request.Request:
        merged_params = dict(params or {})
        if auth and self.access_token:
            merged_params.setdefault("access_token", self.access_token)
        query = urllib.parse.urlencode(merged_params)
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{query}"
        headers = {"Accept": "application/json"}
        if auth and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return urllib.request.Request(url, headers=headers)

    def _build_request_from_url(self, media_url: str) -> urllib.request.Request:
        parsed = urllib.parse.urlparse(media_url)
        if parsed.scheme and parsed.netloc:
            params = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
            return self._build_request(parsed.path, params=params)
        return self._build_request(media_url)
