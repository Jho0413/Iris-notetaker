import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def step(name):
    print(f"   - {name}", flush=True)


def main(work: Path) -> None:
    work.mkdir(parents=True, exist_ok=True)

    step("imports")
    import av
    import cv2
    import numpy as np
    import resvg_py

    import iris.main  # noqa: F401
    import iris.mcp_server  # noqa: F401

    step(f"PyAV {av.__version__}: encode and decode a short video")
    video = work / "clip.mp4"
    with av.open(str(video), "w") as out:
        stream = out.add_stream("h264", rate=5)
        stream.width, stream.height, stream.pix_fmt = 320, 240, "yuv420p"
        for i in range(10):
            img = np.full((240, 320, 3), 20 * i, np.uint8)
            for packet in stream.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)
    with av.open(str(video)) as inp:
        frames = [f.to_ndarray(format="bgr24") for f in inp.decode(video=0)]
    assert len(frames) == 10, f"decoded {len(frames)} frames, expected 10"

    step(f"OpenCV {cv2.__version__}: diff two frames and write a JPEG")
    diff = cv2.absdiff(frames[0], frames[-1])
    assert float(diff.mean()) > 50, "frames should differ"
    jpeg = work / "frame.jpg"
    assert cv2.imwrite(str(jpeg), frames[-1]) and jpeg.stat().st_size > 0

    step("resvg: rasterize an SVG")
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20"><rect width="40" height="20" fill="#4b44d6"/></svg>'
    png = bytes(resvg_py.svg_to_bytes(svg_string=svg))
    assert png.startswith(b"\x89PNG"), "resvg did not return a PNG"

    step("Silero VAD: score a second of audio and find no speech in silence")
    from iris import speech

    silence = np.zeros(16_000, np.int16)
    assert len(speech.voice_probabilities(silence, 16_000)) == 31, "Silero scored no slices"
    assert speech.find_speech(silence, 16_000) == [], "Silero heard speech in silence"

    step("server: python -I -m iris serve --secrets-from-stdin, with and without the token")
    token = "smoke-token-0123456789"
    port = _free_port()
    env = {
        "HOME": os.environ.get("HOME", str(work)),
        "PATH": "/usr/bin:/bin",
        "IRIS_DATA_DIR": str(work / "data"),
        "IRIS_PARENT_PID": str(os.getpid()),
    }
    server = subprocess.Popen(
        [sys.executable, "-I", "-m", "iris", "serve", "--port", str(port), "--secrets-from-stdin"],
        cwd=work,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    server.stdin.write(json.dumps({"openai_api_key": "sk-smoke", "api_token": token}).encode())
    server.stdin.write(b"\n")
    server.stdin.close()
    try:
        url = f"http://127.0.0.1:{port}/api/health"
        deadline = time.monotonic() + 120
        while True:
            if server.poll() is not None:
                raise SystemExit(f"server exited: {server.stdout.read().decode()[-2000:]}")
            try:
                with urllib.request.urlopen(_req(url, token), timeout=2) as res:
                    assert json.load(res)["ok"] is True
                    break
            except (urllib.error.URLError, ConnectionError):
                if time.monotonic() > deadline:
                    raise SystemExit("server did not answer within 120 s") from None
                time.sleep(0.3)
        settings = f"http://127.0.0.1:{port}/api/settings"
        with urllib.request.urlopen(_req(settings, token), timeout=2) as res:
            assert json.load(res)["api_key_set"] is True, "the key from stdin did not arrive"
        try:
            urllib.request.urlopen(_req(f"http://127.0.0.1:{port}/api/sessions", None), timeout=2)
            raise SystemExit("the server answered without the token")
        except urllib.error.HTTPError as e:
            assert e.code == 401, e.code
    finally:
        server.terminate()
        server.wait(timeout=20)

    step("MCP server: python -I -B -m iris mcp, over stdio")
    asyncio.run(_mcp_lists_sessions(env))
    print("   smoke test passed", flush=True)


async def _mcp_lists_sessions(env: dict[str, str]) -> None:
    from mcp import Client, StdioServerParameters

    command = StdioServerParameters(
        command=sys.executable, args=["-I", "-B", "-m", "iris", "mcp"], env=env
    )
    async with Client(command) as client:
        result = await client.call_tool("list_sessions", {})
    assert not result.is_error, result.content
    assert result.structured_content == {"result": []}, result.structured_content


def _req(url, token):
    return urllib.request.Request(url, headers={"X-Iris-Token": token} if token else {})


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


if __name__ == "__main__":
    main(Path(sys.argv[1]))
