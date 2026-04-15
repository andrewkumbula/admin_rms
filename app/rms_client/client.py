from dataclasses import dataclass
import json
from typing import Any, List, Optional, Tuple, Union

import requests
from flask import current_app, session


@dataclass
class RMSResponse:
    ok: bool
    status_code: int
    data: Any
    error: Optional[str] = None


class RMSClient:
    def __init__(self) -> None:
        runtime_base_url = session.get("rms_api_base_url")
        default_base_url = current_app.config["RMS_API_BASE_URL"]
        self.base_url = (runtime_base_url or default_base_url).rstrip("/")
        self.timeout = current_app.config["RMS_API_TIMEOUT_SECONDS"]

    def _headers(self) -> dict[str, str]:
        token = session.get("access_token", "")
        return {"Authorization": f"Bearer {token}"} if token else {}

    def get(
        self,
        path: str,
        params: Optional[Union[dict[str, Any], List[Tuple[str, Any]]]] = None,
    ) -> RMSResponse:
        return self._request("GET", path, params=params)

    def post(self, path: str, json: Optional[Union[dict[str, Any], List[Any]]] = None) -> RMSResponse:
        return self._request("POST", path, json=json)

    def post_multipart(
        self,
        path: str,
        data: Optional[dict[str, str]] = None,
        files: Optional[dict[str, Any]] = None,
    ) -> RMSResponse:
        kwargs: dict[str, Any] = {}
        if data:
            kwargs["data"] = data
        if files:
            kwargs["files"] = files
        return self._request("POST", path, **kwargs)

    def patch(self, path: str, json: Optional[dict[str, Any]] = None) -> RMSResponse:
        return self._request("PATCH", path, json=json)

    def delete(self, path: str, json: Optional[dict[str, Any]] = None) -> RMSResponse:
        return self._request("DELETE", path, json=json)

    def _request(self, method: str, path: str, **kwargs) -> RMSResponse:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            resp = requests.request(
                method,
                url,
                headers=self._headers(),
                timeout=self.timeout,
                **kwargs,
            )
            payload = resp.json() if resp.content else {}
            if resp.ok:
                return RMSResponse(ok=True, status_code=resp.status_code, data=payload)
            return RMSResponse(
                ok=False,
                status_code=resp.status_code,
                data=payload,
                error=self._normalize_error(payload),
            )
        except requests.RequestException as exc:
            return RMSResponse(ok=False, status_code=0, data={}, error=str(exc))

    @staticmethod
    def _normalize_error(payload: Any) -> str:
        if isinstance(payload, dict):
            err = payload.get("error")
            if isinstance(err, str):
                return err
            if isinstance(err, dict):
                nested = err.get("message")
                if isinstance(nested, str):
                    details = err.get("details")
                    if isinstance(details, list) and details:
                        detail_parts = []
                        for d in details:
                            if isinstance(d, dict):
                                field = d.get("field")
                                msg = d.get("message")
                                if field and msg:
                                    detail_parts.append(f"{field}: {msg}")
                                elif msg:
                                    detail_parts.append(str(msg))
                        if detail_parts:
                            return f"{nested} ({'; '.join(detail_parts)})"
                    return nested
            message = payload.get("message")
            details = payload.get("details")
            if isinstance(message, str):
                if isinstance(details, list) and details:
                    detail_parts = []
                    for d in details:
                        if isinstance(d, dict):
                            field = d.get("field")
                            msg = d.get("message")
                            if field and msg:
                                detail_parts.append(f"{field}: {msg}")
                            elif msg:
                                detail_parts.append(str(msg))
                    if detail_parts:
                        return f"{message} ({'; '.join(detail_parts)})"
                return message
            return json.dumps(payload, ensure_ascii=False)
        if isinstance(payload, list):
            return json.dumps(payload, ensure_ascii=False)
        return "RMS API error"
