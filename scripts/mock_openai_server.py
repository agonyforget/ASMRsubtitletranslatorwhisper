# -*- coding: utf-8 -*-
"""本地假 OpenAI 接口,用来验证 translate_zh.py 的接口调用路径(不需要真 key).

用法:
  python mock_openai_server.py 8799
然后在另一个终端:
  set DEEPSEEK_API_KEY=fake
  python translate_zh.py xx.ja.json --outdir . --name xx --base-url http://127.0.0.1:8799/v1
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

CALLS = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n).decode("utf-8"))
        user = body["messages"][-1]["content"]
        items = json.loads(user)
        CALLS.append(len(items))
        # 故意第一行不返回,验证"缺行会重试"的逻辑
        out = [{"i": it["i"], "zh": "【译】" + (it.get("src") or it.get("ja") or "")[:12]} for it in items]
        if len(CALLS) == 1 and len(out) > 1:
            out = out[:-1]
        payload = {"choices": [{"message": {"content": json.dumps(out, ensure_ascii=False)}}]}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        data = json.dumps({"calls": CALLS}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
    print(f"mock openai server on http://127.0.0.1:{port}/v1", flush=True)
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
