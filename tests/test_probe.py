"""Host-side protocol smoke test; does not substitute for iPhone background testing."""
import http.client
import json
import socket
import subprocess
import sys
import time


def request(path, code=None):
    client = http.client.HTTPConnection("127.0.0.1", 8789, timeout=12)
    client.request("GET", path, headers={"X-Probe-Code": code} if code else {})
    response = client.getresponse()
    status, data = response.status, response.read()
    client.close()
    return status, json.loads(data)


process = subprocess.Popen([sys.argv[1]], stdout=subprocess.PIPE, text=True)
try:
    token = process.stdout.readline().strip()
    assert len(token) == 36
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", 8789), timeout=0.1):
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError("listener did not start")
    assert request("/probe")[0] == 401
    assert request("/unknown", token)[0] == 404
    status, result = request("/probe", token)
    assert status == 200 and result["capturing"] and result["frames"] == 17
    assert result["frame_age_ms"] == 200 and result["requests"] == 1
    assert "https_status" in result or "https_error" in result
    print("Probe protocol checks passed; HTTPS outcome:", result.get("https_status", "connection error"))
finally:
    process.terminate()
    process.wait(timeout=5)
