"""Signed call to the AI editor on Vercel (pipeline-hook event=editor): one post per call."""
import json
import os
import sys
import time
from urllib.request import Request, urlopen

from .pipeline import hook_signature


def call(base, secret, transport=urlopen):
    ts = int(time.time())
    body = json.dumps({"id": "P0000", "event": "editor", "ts": ts}).encode()
    req = Request(base.rstrip("/") + "/api/pipeline-hook", data=body, method="POST",
                  headers={"Content-Type": "application/json", "X-Pipeline-Signature": hook_signature("P0000", "editor", ts, secret)})
    with transport(req, timeout=70) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def main():
    base, secret = os.environ.get("PIPELINE_BASE_URL", ""), os.environ.get("PIPELINE_SECRET", "")
    if not base or not secret:
        print("PIPELINE_BASE_URL или PIPELINE_SECRET не заданы")
        return 1
    result = call(base, secret)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
