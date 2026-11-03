"""Compare a research-only Swift fit with a specified local Python pointer.py snapshot."""
import importlib.util
import json
import math
import subprocess
import sys

spec = importlib.util.spec_from_file_location("pointer", sys.argv[2])
pointer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pointer)
good = [(x, y, 0.9 * x - 12, 1.3 * y - 20) for x, y in pointer.PROBES]
cases = [good, good[:2], [(x, y, 100, 100) for x, y in pointer.PROBES],
         good[:-1] + [(140, 264, 200, 700)]]
payload = [[dict(zip(("x", "y", "px", "py"), row)) for row in case] for case in cases]
result = subprocess.run([sys.argv[1]], input=json.dumps(payload), text=True, capture_output=True, check=True)
swift = json.loads(result.stdout)
for rows, actual in zip(cases, swift):
    try:
        expected = pointer.fit(rows)
    except ValueError:
        expected = None
    assert (actual is None) == (expected is None)
    if expected:
        for key in ("gain", "offset", "rms"):
            assert all(math.isclose(a, b, abs_tol=1e-8) for a, b in zip(actual[key], expected[key]))
print(f"Calibration parity: {len(cases)} synthetic cases passed; no real pointer movement tested.")
