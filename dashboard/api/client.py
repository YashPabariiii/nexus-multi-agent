import requests

from config import API_BASE_URL


class ApiError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{status_code}: {detail}")


def _headers(token: str | None) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _handle(resp: requests.Response):
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise ApiError(resp.status_code, str(detail))
    if resp.status_code == 204:
        return None
    if resp.headers.get("content-type", "").startswith("application/json"):
        return resp.json()
    return resp.content


def register(name: str, email: str) -> dict:
    return _handle(requests.post(f"{API_BASE_URL}/v1/auth/register", json={"name": name, "email": email}))


def login(api_key: str) -> dict:
    return _handle(requests.post(f"{API_BASE_URL}/v1/auth/token", json={"api_key": api_key}))


def preview_brief(token: str, topic: str, scope: str, domain: str) -> dict:
    return _handle(
        requests.post(
            f"{API_BASE_URL}/v1/briefs/preview",
            headers=_headers(token),
            json={"topic": topic, "scope": scope, "domain": domain},
        )
    )


def create_brief(token: str, **payload) -> dict:
    return _handle(requests.post(f"{API_BASE_URL}/v1/briefs", headers=_headers(token), json=payload))


def list_briefs(token: str, page: int = 1, page_size: int = 20) -> dict:
    return _handle(
        requests.get(f"{API_BASE_URL}/v1/briefs", headers=_headers(token), params={"page": page, "page_size": page_size})
    )


def get_brief(token: str, brief_id: str) -> dict:
    return _handle(requests.get(f"{API_BASE_URL}/v1/briefs/{brief_id}", headers=_headers(token)))


def get_traces(token: str, brief_id: str) -> list:
    return _handle(requests.get(f"{API_BASE_URL}/v1/briefs/{brief_id}/traces", headers=_headers(token)))


def get_claims(token: str, brief_id: str, verified_only: bool = False, disputed_only: bool = False) -> list:
    return _handle(
        requests.get(
            f"{API_BASE_URL}/v1/briefs/{brief_id}/claims",
            headers=_headers(token),
            params={"verified_only": verified_only, "disputed_only": disputed_only},
        )
    )


def resume_brief(token: str, brief_id: str, action: str, feedback: str | None = None, sections_to_revise: list | None = None) -> dict:
    return _handle(
        requests.post(
            f"{API_BASE_URL}/v1/briefs/{brief_id}/resume",
            headers=_headers(token),
            json={"action": action, "feedback": feedback, "sections_to_revise": sections_to_revise},
        )
    )


def download_report_pdf(token: str, brief_id: str) -> bytes:
    return _handle(requests.get(f"{API_BASE_URL}/v1/briefs/{brief_id}/report/download/pdf", headers=_headers(token)))


def download_trace_pdf(token: str, brief_id: str) -> bytes:
    return _handle(requests.get(f"{API_BASE_URL}/v1/briefs/{brief_id}/report/download/trace", headers=_headers(token)))


def get_report_json(token: str, brief_id: str) -> dict:
    return _handle(requests.get(f"{API_BASE_URL}/v1/briefs/{brief_id}/report/download/json", headers=_headers(token)))


def get_report_preview(token: str, brief_id: str) -> dict:
    return _handle(requests.get(f"{API_BASE_URL}/v1/briefs/{brief_id}/report/preview", headers=_headers(token)))


def email_report(token: str, brief_id: str, email: str) -> dict:
    return _handle(
        requests.post(f"{API_BASE_URL}/v1/briefs/{brief_id}/report/email", headers=_headers(token), json={"email": email})
    )


def compare_briefs(token: str, brief_id_a: str, brief_id_b: str) -> dict:
    return _handle(
        requests.post(
            f"{API_BASE_URL}/v1/briefs/compare",
            headers=_headers(token),
            json={"brief_id_a": brief_id_a, "brief_id_b": brief_id_b},
        )
    )


def knowledge_summary(token: str) -> dict:
    return _handle(requests.get(f"{API_BASE_URL}/v1/knowledge", headers=_headers(token)))


def knowledge_related(token: str, topic: str) -> list:
    return _handle(requests.get(f"{API_BASE_URL}/v1/knowledge/related", headers=_headers(token), params={"topic": topic}))
