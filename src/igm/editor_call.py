"""Signed call to the AI editor on Vercel (pipeline-hook event=editor): one post per call.

В лог Actions пишем аннотации (::error:: / ::warning:: / ::notice::), чтобы причину было видно
в сводке запуска без открытия логов.
"""
import json
import os
import socket
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .pipeline import hook_signature

TIMEOUT = 70  # функция Vercel ограничена 60 с


def call(base, secret, transport=urlopen):
    ts = int(time.time())
    body = json.dumps({"id": "P0000", "event": "editor", "ts": ts}).encode()
    req = Request(base.rstrip("/") + "/api/pipeline-hook", data=body, method="POST",
                  headers={"Content-Type": "application/json", "X-Pipeline-Signature": hook_signature("P0000", "editor", ts, secret)})
    with transport(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def _one_line(text, limit=400):
    # аннотация GitHub — одна строка; переводы строк кодируются как %0A
    return str(text).replace("%", "%25").replace("\r", "").replace("\n", "%0A")[:limit]


def _is_timeout(err):
    reason = getattr(err, "reason", None)
    return isinstance(err, (TimeoutError, socket.timeout)) or isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(err)


def _server_error(err):
    """Текст ошибки из ответа Vercel (JSON {error|message}) или кусок тела."""
    try:
        raw = err.read().decode("utf-8", "replace")
    except Exception:
        return ""
    try:
        data = json.loads(raw)
        return str(data.get("error") or data.get("message") or raw)
    except ValueError:
        return raw.strip()


def main(transport=urlopen, env=os.environ):
    base, secret = env.get("PIPELINE_BASE_URL", ""), env.get("PIPELINE_SECRET", "")
    if not base or not secret:
        print("::error::PIPELINE_BASE_URL или PIPELINE_SECRET не заданы в IGM (Settings → Secrets and variables → Actions)")
        return 1
    try:
        result = call(base, secret, transport)
    except HTTPError as err:
        detail = _server_error(err)
        if err.code == 504:  # Vercel оборвал функцию по лимиту 60 с — пост мог успеть записаться
            print(f"::warning::ИИ-редактор не уложился в лимит Vercel (HTTP 504){': ' + _one_line(detail) if detail else ''}")
            return 0
        print(f"::error::ИИ-редактор: HTTP {err.code}{': ' + _one_line(detail) if detail else ''}")
        return 1
    except (URLError, OSError) as err:
        if _is_timeout(err):
            print(f"::warning::ИИ-редактор не ответил за {TIMEOUT} с (лимит функции Vercel 60 с) — следующий запуск продолжит")
            return 0
        print(f"::error::ИИ-редактор недоступен: {type(err).__name__}: {_one_line(getattr(err, 'reason', err))}")
        return 1
    except ValueError as err:
        print(f"::error::ИИ-редактор вернул не JSON: {_one_line(err)}")
        return 1
    print(json.dumps(result, ensure_ascii=False))
    if result.get("ok"):
        print(f"::notice::ИИ-редактор: {_one_line(json.dumps(result, ensure_ascii=False))}")
        return 0
    print(f"::error::ИИ-редактор отказал: {_one_line(result.get('error') or result.get('message') or json.dumps(result, ensure_ascii=False))}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
