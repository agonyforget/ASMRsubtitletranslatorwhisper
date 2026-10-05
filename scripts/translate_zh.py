#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""把日文字幕翻译成中文,并合成中日双语字幕.

设计要点:
  * faster-whisper 的 task="translate" 只能翻成英文,所以中文字幕必须走独立翻译环节
  * 按行号一一对应(绝不合并/增删行),保证时间轴不变
  * 断点续传:翻译结果落盘缓存,中断后重跑只补没翻的行
  * 兼容任何 OpenAI 格式接口:DeepSeek / OpenAI / 本地 vLLM / Ollama

输出:
  <outdir>/<name>.zh.srt          纯中文字幕
  <outdir>/<name>.bilingual.srt   中文行 + 日文行
  <outdir>/<name>.bilingual.ass   中文主字幕 + 日文小字(libass 样式)
  <outdir>/<name>.zh.cache.json   翻译缓存(可删,用于续跑)

用法:
  set DEEPSEEK_API_KEY=sk-xxx
  python translate_zh.py ..\out\01_seoff.ja.json --outdir ..\out --name 01_seoff

  # 不调接口,先验证整条流水线
  python translate_zh.py ..\out\01_seoff.ja.json --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SYSTEM_PROMPT = """你是资深的日译中字幕翻译。输入是 ASMR 音声的日语识别结果(可能含识别错误)。
输入是 JSON 数组,每项形如 {"i": 行号, "src": "该行原文"}。

要求:
1. 逐行翻译,行数必须与输入完全一致,不合并、不拆分、不增删。即使某行很短或只有语气词,也要给出对应中文。
2. 语气口语化、自然,保持耳语/亲昵/挑逗的语域,不要翻译成书面语或新闻腔。
3. 拟声词用中文惯用写法:「ぺろぺろ」→「舔舔」、「ちゅっ」→「啾」、「ふぅ」→「呼～」、「んっ」→「嗯…」。
4. 明显的识别错误按上下文合理修正(例如把「～」识别成「ー」、假名错位),但不要凭空添加原文没有的内容。
5. 保留「♪」「…」「～」这类语气符号的语感,不要直译成标点堆砌。
6. 只输出 JSON 数组,不要解释、不要 markdown 代码块。格式:
[{"i": 1, "zh": "译文"}, {"i": 2, "zh": "译文"}]
"""


def fmt_ts(t: float) -> str:
    if t < 0:
        t = 0.0
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def load_segments(path: Path):
    """支持 .ja.json(faster-whisper 输出)和 .srt."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        segs = data["segments"] if isinstance(data, dict) else data
        return [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in segs]
    segs = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l for l in block.splitlines() if l.strip()]
        if len(lines) < 2:
            continue
        m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", lines[1])
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        segs.append({"start": start, "end": end, "text": " ".join(lines[2:])})
    return segs


def extract_json_array(raw: str):
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end < 0:
        raise ValueError("响应里没有 JSON 数组")
    return json.loads(raw[start:end + 1])


def call_llm(base_url: str, api_key: str, model: str, items, extra_hint: str = "",
             timeout: int = 180, system_prompt: str = SYSTEM_PROMPT):
    payload = {
        "model": model,
        "temperature": 0.3,
        "messages": [
            {"role": "system", "content": system_prompt + (("\n额外上下文:" + extra_hint) if extra_hint else "")},
            {"role": "user", "content": json.dumps(items, ensure_ascii=False)},
        ],
    }
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"]


def translate(segments, args, cache_path: Path):
    cache = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    todo = [(i, s) for i, s in enumerate(segments, 1) if str(i) not in cache]
    print(f"[*] 共 {len(segments)} 行,已缓存 {len(cache)} 行,待翻译 {len(todo)} 行")

    if args.dry_run:
        for i, s in todo:
            cache[str(i)] = s["text"]
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        return cache

    if not todo:
        print("[*] 所有行都已在缓存里,直接合成")
        return cache

    api_key = os.environ.get(args.api_key_env) or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print(f"[x] 没找到 API key:请设置环境变量 {args.api_key_env}(或 OPENAI_API_KEY)", file=sys.stderr)
        raise SystemExit(4)

    for b in range(0, len(todo), args.batch):
        chunk = todo[b:b + args.batch]
        items = [{"i": i, "src": s["text"].strip()} for i, s in chunk]
        # 给模型一点前后文,减少断行翻译的语义割裂
        lo = max(0, b - 3)
        hint = "前文:" + " / ".join(segments[j]["text"] for j in range(lo, b)) if lo < b else ""
        last_err = None
        for attempt in range(1, args.retries + 1):
            try:
                raw = call_llm(args.base_url, api_key, args.model, items, hint,
                               system_prompt=args.system_prompt)
                arr = extract_json_array(raw)
                got = {int(x["i"]): str(x.get("zh", "")).strip() for x in arr if "i" in x}
                missing = [i for i, _ in chunk if i not in got]
                if missing:
                    raise ValueError(f"缺行 {missing[:5]}")
                cache.update({str(k): v for k, v in got.items()})
                cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"    [{min(b + args.batch, len(todo))}/{len(todo)}] 已翻译并缓存")
                last_err = None
                break
            except Exception as e:  # 网络/格式问题都重试
                last_err = e
                print(f"    ! 第 {attempt} 次失败: {e}")
                time.sleep(2 * attempt)
        if last_err is not None:
            print(f"[x] 该批仍失败,已缓存的部分保留,重跑可续: {last_err}", file=sys.stderr)
            break
        if args.sleep > 0:
            time.sleep(args.sleep)
    return cache


def write_outputs(segments, zh, outdir: Path, name: str):
    zh_only, bi, ass = [], [], []
    ass.append("[Script Info]")
    ass.append("ScriptType: v4.00+")
    ass.append("PlayResX: 1920")
    ass.append("PlayResY: 1080")
    ass.append("WrapStyle: 0")
    ass.append("ScaledBorderAndShadow: yes")
    ass.append("")
    ass.append("[V4+ Styles]")
    ass.append("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
               "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
               "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding")
    # 中文主行:白字黑边,抬高一点;日文行:小一号灰白,放在中文下面
    ass.append("Style: ZH,Microsoft YaHei,58,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,"
               "0,0,0,0,100,100,0,0,1,3,1,2,60,60,88,1")
    ass.append("Style: JA,Microsoft YaHei,38,&H00DCDCDC,&H000000FF,&H00000000,&H64000000,"
               "0,0,0,0,100,100,0,0,1,2,1,2,60,60,42,1")
    ass.append("")
    ass.append("[Events]")
    ass.append("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")

    def ass_ts(t):
        t = max(t, 0.0)
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t % 60
        return f"{h:d}:{m:02d}:{s:05.2f}"

    for i, seg in enumerate(segments, 1):
        ja = seg["text"].strip()
        cn = (zh.get(str(i)) or "").strip() or ja
        zh_only.append((seg, cn))
        bi.append((seg, cn, ja))
        ass.append(f"Dialogue: 0,{ass_ts(seg['start'])},{ass_ts(seg['end'])},ZH,,0,0,0,,{cn}")
        if ja and ja != cn:
            ass.append(f"Dialogue: 1,{ass_ts(seg['start'])},{ass_ts(seg['end'])},JA,,0,0,0,,{ja}")

    def write_srt(rows, path: Path):
        out = []
        for n, row in enumerate(rows, 1):
            seg = row[0]
            out.append(str(n))
            out.append(f"{fmt_ts(seg['start'])} --> {fmt_ts(seg['end'])}")
            out.extend(row[1:])
            out.append("")
        path.write_text("\n".join(out), encoding="utf-8")

    write_srt(zh_only, outdir / f"{name}.zh.srt")
    write_srt(bi, outdir / f"{name}.bilingual.srt")
    (outdir / f"{name}.bilingual.ass").write_text("\n".join(ass), encoding="utf-8")
    print(f"[+] 写出: {outdir / (name + '.zh.srt')}")
    print(f"[+] 写出: {outdir / (name + '.bilingual.srt')}")
    print(f"[+] 写出: {outdir / (name + '.bilingual.ass')}")


def main() -> int:
    ap = argparse.ArgumentParser(description="日文字幕 -> 中文/中日双语")
    ap.add_argument("input", help=".ja.json 或 .ja.srt")
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--name", default=None)
    ap.add_argument("--model", default="deepseek-chat")
    ap.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com/v1"))
    ap.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    ap.add_argument("--batch", type=int, default=30, help="每次请求翻译多少行")
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--sleep", type=float, default=0.5)
    ap.add_argument("--dry-run", action="store_true", help="不调接口,用原文占位,验证流程")
    ap.add_argument("--prompt-file", default=None,
                    help="自定义系统提示词文件(换语言/换风格时用,见 scripts\\prompt_*.txt)")
    ap.add_argument("--prompt", default=None, help="直接给系统提示词(优先级高于 --prompt-file)")
    ap.add_argument("--require-full", action="store_true",
                    help="译文不完整时返回退出码 6(适合放进批处理,避免误用半成品)")
    args = ap.parse_args()

    # 系统提示词:--prompt > --prompt-file > 内置(日语 ASMR)
    if args.prompt:
        args.system_prompt = args.prompt
    elif args.prompt_file:
        pf = Path(args.prompt_file)
        if not pf.exists():
            print(f"[x] 找不到提示词文件: {pf}", file=sys.stderr)
            return 2
        args.system_prompt = pf.read_text(encoding="utf-8")
    else:
        args.system_prompt = SYSTEM_PROMPT

    src = Path(args.input)
    if not src.exists():
        print(f"[x] 找不到输入: {src}", file=sys.stderr)
        return 2
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    name = args.name or src.name.replace(".ja.json", "").replace(".ja.srt", "").replace(".ja", "")

    segments = load_segments(src)
    print(f"[*] 读入 {len(segments)} 行 <- {src}")
    cache_path = outdir / f"{name}.zh.cache.json"
    zh = translate(segments, args, cache_path)
    write_outputs(segments, zh, outdir, name)
    done = sum(1 for i in range(1, len(segments) + 1) if str(i) in zh)
    if done < len(segments):
        print(f"[!] 只有 {done}/{len(segments)} 行有译文,其余行用**原文**占位了;"
              f"重跑本命令可继续翻译未完成的部分(已有译文不会重复计费)")
    else:
        print(f"[*] 翻译覆盖 {done}/{len(segments)} 行")
    if args.require_full and done < len(segments):
        print("[x] --require-full:译文不完整,退出码 6", file=sys.stderr)
        return 6
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
