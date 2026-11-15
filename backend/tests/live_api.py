from fastapi.testclient import TestClient


def start_recording(client: TestClient) -> str:
    return client.post("/api/live", json={"title": "standup"}).json()["id"]


def post_audio(client: TestClient, sid: str, seq: int, data: bytes) -> int:
    return client.post(f"/api/live/{sid}/audio?seq={seq}", content=data).status_code


def post_frame(client: TestClient, sid: str, t: float, data: bytes, share: int = 1) -> int:
    return client.post(f"/api/live/{sid}/frame?t={t}&share={share}", content=data).status_code


def stop_recording(client: TestClient, sid: str) -> int:
    return client.post(f"/api/live/{sid}/stop").status_code
