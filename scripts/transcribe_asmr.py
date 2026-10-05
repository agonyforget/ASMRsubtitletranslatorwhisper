#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""ASMR 专用 faster-whisper 转录脚本.

针对耳语/气声/低音量/长时长的 ASMR 音频调参:
  * vad_filter 默认关闭(Silero VAD 会把耳语当静音切掉)
  * condition_on_previous_text=False + hallucination_silence_threshold 抑制幻觉循环
  * 后处理清理已知幻觉句、段内重复、跨段死循环,并做中文字幕友好的重新断句

输出:
  <outdir>/<name>.ja.srt   日文字幕
  <outdir>/<name>.ja.json  带词级时间戳的完整结果(给翻译/对齐用)
  <outdir>/<name>.ja.txt   纯文本
  <outdir>/<name>.meta.json 运行元信息(模型/耗时/实时率/参数)

用法示例:
  python transcribe_asmr.py norm.wav --model large-v3-turbo --outdir ..\out
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------- 幻觉黑名单
# Whisper 在日语音频的静音段会稳定输出这些句子,整段命中且无有效词时丢弃
HALLUCINATION_RE = re.compile(
    r"^(?:"
    r"ご視聴(?:ありがとうございました|ありがとうございます|ありがとうございました。)|"
    r"ご清聴ありがとうございました|"
    r"おやすみなさい|おやすみ|お疲れ様でした|おつかれさまでした|"
    r"チャンネル登録(?:お願いします)?|高評価(?:お願いします)?|"
    r"字幕(?:by|BY|：|:).*|"
    r"(?:thanks?|thank you) for watching.*|(?:please )?subscribe.*|"
    r"おめでとうございます|おめでとう|"
    r"[。、,.!?！？♪\s]+"
    r")[。.!！?？\s♪]*$",
    re.IGNORECASE,
)

# 只由语气词/填充词构成、且置信度很低的段,通常是幻觉
FILLER_ONLY_RE = re.compile(r"^(?:[あぁんんーっふはひへほぅうぃえお～〜…、。,.\s♪]+)$")


def fmt_ts(t: float) -> str:
    if t < 0:
        t = 0.0
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def norm_text(s: str) -> str:
    return re.sub(r"[\s♪。、,.!?！？…]+", "", s or "")


def collapse_intra_repeat(text: str) -> str:
    """把 'あぁあぁあぁあぁ' 这类段内死循环压成一次."""
    t = text
    for _ in range(6):
        new = re.sub(r"(?P<p>.{1,14}?)(?:\s*(?P=p)){2,}", r"\g<p>", t)
        if new == t:
            break
        t = new
    return t


def split_long_text(text: str, max_len: int = 26):
    """CJK 字幕单行不宜过长,按标点优先切分."""
    text = text.strip()
    if len(text) <= max_len:
        return [text] if text else []
    parts, buf = [], ""
    for ch in text:
        buf += ch
        if ch in "。、！？!?…♪" and len(buf) >= max_len // 2:
            parts.append(buf)
            buf = ""
        elif len(buf) >= max_len:
            parts.append(buf)
            buf = ""
    if buf:
        parts.append(buf)
    return [p for p in (p.strip() for p in parts) if p]


def clean_segments(segments, drop_filler: bool = True):
    """段级清洗:丢幻觉 -> 压重复 -> 去跨段死循环 -> 合并过碎片段."""
    kept = []
    for s in segments:
        text = (s["text"] or "").strip()
        if not text:
            continue
        # 1) 高 no_speech + 低 logprob 的段直接丢
        if s["no_speech_prob"] > 0.75 and s["avg_logprob"] < -0.75:
            continue
        # 2) 整段命中幻觉黑名单
        if HALLUCINATION_RE.match(text):
            continue
        if drop_filler and FILLER_ONLY_RE.match(text) and s["avg_logprob"] < -0.9:
            continue
        # 3) 段内重复
        text = collapse_intra_repeat(text).strip()
        if not text:
            continue
        kept.append({**s, "text": text})

    # 4) 跨段死循环:连续 3 段以上文本完全相同,只保留第一段
    out, i = [], 0
    while i < len(kept):
        j = i + 1
        base = norm_text(kept[i]["text"])
        if base:
            while j < len(kept) and norm_text(kept[j]["text"]) == base:
                j += 1
        if j - i >= 3:
            out.append(kept[i])
            i = j
        else:
            out.extend(kept[i:j])
            i = j

    # 5) 合并成句子级:一直合到出现句末标点、或停顿变长、或太长为止
    #    (30s 分块会把句子拦腰切断,不合并会导致日/中两行都在半句处断)
    merged = []
    for s in out:
        if merged:
            p = merged[-1]
            gap = s["start"] - p["end"]
            ptxt = p["text"].rstrip()
            combined = len(norm_text(p["text"])) + len(norm_text(s["text"]))
            ends_sentence = bool(ptxt) and ptxt[-1] in SENT_END
            continued = (not ends_sentence) and gap < 1.2 and combined <= 80
            too_close = gap < 0.30 and combined <= 40
            if continued or too_close:
                p["text"] = p["text"] + s["text"]
                p["end"] = s["end"]
                p["words"] = (p.get("words") or []) + (s.get("words") or [])
                continue
        merged.append(dict(s))
    return merged


def write_srt(segments, path: Path):
    lines = []
    for i, s in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{fmt_ts(s['start'])} --> {fmt_ts(s['end'])}")
        lines.append(s["text"])
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def read_wav_16k_mono(path: Path):
    """用标准库 wave + numpy 读 16k 单声道 wav,绕开 PyAV 版本兼容问题.

    faster-whisper 1.2.1 调 av.open(metadata_errors=...) 而 PyAV 19 已移除该参数;
    直接喂 float32 numpy 数组既避开这个坑,也省掉一次解码。
    """
    import wave

    import numpy as np

    with wave.open(str(path), "rb") as w:
        channels, sampwidth, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(frames)
    if sampwidth == 2:
        arr = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
    elif sampwidth == 4:
        arr = np.frombuffer(raw, dtype="<i4").astype("float32") / 2147483648.0
    else:
        raise ValueError(f"只支持 16bit/32bit PCM wav,当前 sampwidth={sampwidth}")
    if channels > 1:
        arr = arr.reshape(-1, channels).mean(axis=1)
    if rate != 16000:
        raise ValueError(f"需要 16kHz 输入(用 ffmpeg -ar 16000 预处理),当前 {rate}Hz")
    return np.ascontiguousarray(arr)


def make_clips(audio, sample_rate: int = 16000, chunk_s: float = 30.0, search_s: float = 3.0):
    """把长音频切成 <=chunk_s 的块,切点尽量落在局部能量最低处.

    BatchedInferencePipeline 要求:关闭 VAD 时必须自己提供 clip_timestamps,
    而且每个块超过 30s 只会转前 30s。所以在 30s 边界附近 ±search_s 找能量最低的
    位置下刀,避免把词切断。返回 [{"start": 秒, "end": 秒}]。
    """
    import numpy as np

    n = len(audio)
    step = int(chunk_s * sample_rate)
    guard = int(5.0 * sample_rate)      # 至少 5 秒一块,避免疯狂切
    win = max(int(0.20 * sample_rate), 1)
    clips, pos = [], 0
    while pos < n:
        end = min(pos + step, n)
        if end < n and end - pos > guard:
            # 只在边界"之前"找切点,保证每块严格 <= chunk_s(超过 30s 会被截断并触发断言)
            lo = max(pos + guard, end - int(search_s * sample_rate))
            hi = end
            if hi - lo > win:
                energy = np.convolve(np.abs(audio[lo:hi]), np.ones(win) / win, mode="valid")
                cut = lo + int(np.argmin(energy)) + win // 2
                if cut > pos + guard:
                    end = cut
        clips.append({"start": pos / sample_rate, "end": end / sample_rate})
        pos = end
    # 浮点误差保护:faster-whisper 会断言 sum(clips) <= 音频总长
    total = sum(c["end"] - c["start"] for c in clips)
    dur = n / sample_rate
    if total > dur and clips:
        clips[-1]["end"] -= (total - dur)
    return clips


SENT_END = "。！？!?…♪"


def split_by_words(seg, max_len: int):
    """用词级时间戳精确重断句(比按字数等分时间准得多).返回 None 表示没有词信息."""
    words = [w for w in (seg.get("words") or []) if (w.get("word") or "").strip()]
    if len(words) < 2:
        return None
    soft = max_len          # 达到上限即断,靠下面的均衡步骤避免碎行
    groups, cur, cur_len = [], [], 0
    for w in words:
        t = w["word"].strip()
        cur.append(w)
        cur_len += len(t)
        ends_sentence = bool(t) and t[-1] in SENT_END
        if ends_sentence and cur_len >= max_len * 0.5:
            groups.append(cur)
            cur, cur_len = [], 0
        elif cur_len >= soft:
            groups.append(cur)
            cur, cur_len = [], 0
    if cur:
        groups.append(cur)
    # 收尾:尾组太短时从上一组挪词过来做均衡(而不是并回去,否则会长出一大截)
    while len(groups) > 1:
        tail = sum(len(x["word"].strip()) for x in groups[-1])
        if tail >= max_len * 0.4:
            break
        prev = groups[-2]
        while prev and tail < max_len * 0.4:
            w = prev.pop()
            groups[-1].insert(0, w)
            tail += len(w["word"].strip())
        if not prev:
            groups.pop(-2)
    out = []
    for grp in groups:
        text = "".join(w["word"] for w in grp).strip()
        if not text:
            continue
        out.append({**seg, "start": float(grp[0]["start"]), "end": float(grp[-1]["end"]),
                    "text": text, "words": grp})
    return out or None


def main() -> int:
    ap = argparse.ArgumentParser(description="ASMR-tuned faster-whisper transcriber")
    ap.add_argument("audio", help="输入音频/视频(建议先做 loudnorm 归一化")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--name", default=None, help="输出文件名前缀,默认取输入文件名")
    ap.add_argument("--model", default="large-v3-turbo")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--compute-type", default="int8")
    ap.add_argument("--cpu-threads", type=int, default=0, help="0=自动")
    ap.add_argument("--language", default="ja")
    ap.add_argument("--task", default="transcribe", choices=["transcribe", "translate"],
                    help="translate 只能翻成英文,中文字幕请用 transcribe + 翻译环节")
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--beam-size", type=int, default=5)
    ap.add_argument("--no-batched", action="store_true", help="关闭批处理流水线")
    ap.add_argument("--vad", action="store_true", help="开启 VAD(ASMR 默认关闭,开了容易吃掉耳语)")
    ap.add_argument("--vad-threshold", type=float, default=0.25)
    ap.add_argument("--initial-prompt", default=None, help="术语提示,可提高专有名词准确率")
    ap.add_argument("--clip", default=None,
                    help='只转指定片段,如 "0,600" 或 "0,600,1200,1800"(秒,成对 start,end)')
    ap.add_argument("--max-line", type=int, default=40,
                    help="单行最大字符数(日语 40 字左右仍可读;设小会把句子切碎,影响翻译对齐)")
    ap.add_argument("--keep-filler", action="store_true", help="保留纯语气词段")
    ap.add_argument("--force-av", action="store_true",
                    help="强制用 PyAV 解码(默认对 .wav 走 wave+numpy,规避 PyAV 19 不兼容)")
    ap.add_argument("--chunk-seconds", type=float, default=30.0,
                    help="批处理模式下的分块长度,上限 30s(Whisper 窗口)")
    ap.add_argument("--fix-map", default=None,
                    help='纠错表 JSON:{"误识":"正确"},对最终文本做确定性替换(同一系列可复用)')

    args = ap.parse_args()

    audio = Path(args.audio)
    if not audio.exists():
        print(f"[x] 找不到音频: {audio}", file=sys.stderr)
        return 2
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    name = args.name or audio.stem

    # 延迟导入,便于在没装依赖时报错更清楚
    try:
        from faster_whisper import BatchedInferencePipeline, WhisperModel
    except ImportError as e:
        print(f"[x] 没装 faster-whisper: {e}", file=sys.stderr)
        return 3

    kwargs = {}
    if args.cpu_threads > 0:
        kwargs["cpu_threads"] = args.cpu_threads
    print(f"[*] 加载模型 {args.model} ({args.device}/{args.compute_type}) ...", flush=True)
    t0 = time.time()
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type, **kwargs)
    load_s = time.time() - t0
    print(f"[*] 模型加载完成 {load_s:.1f}s", flush=True)

    transcribe_kwargs = dict(
        language=args.language,
        task=args.task,
        beam_size=args.beam_size,
        # ---- ASMR 关键参数 ----
        vad_filter=args.vad,
        condition_on_previous_text=False,
        hallucination_silence_threshold=2.0,
        no_speech_threshold=0.5,
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        word_timestamps=True,
        # -----------------------
    )
    if args.vad:
        transcribe_kwargs["vad_parameters"] = dict(threshold=args.vad_threshold, min_silence_duration_ms=700)
    if args.initial_prompt:
        transcribe_kwargs["initial_prompt"] = args.initial_prompt

    t1 = time.time()
    # 16k 单声道 wav 直接读成 numpy 数组,绕过 PyAV 的版本兼容问题
    audio_array = None
    if not args.force_av and audio.suffix.lower() == ".wav":
        audio_array = read_wav_16k_mono(audio)
        print(f"[*] 已用 wave+numpy 读入 {len(audio_array) / 16000:.1f}s 音频(跳过 PyAV 解码)", flush=True)
    audio_input = audio_array if audio_array is not None else str(audio)
    duration = (len(audio_array) / 16000) if audio_array is not None else None

    # clip_timestamps:批处理模式必须显式提供(关掉 VAD 时不会自动生成)
    if args.clip:
        vals = [float(x) for x in args.clip.split(",") if x.strip() != ""]
        pairs = [(vals[i], vals[i + 1]) for i in range(0, len(vals) - 1, 2)]
        if args.no_batched:
            transcribe_kwargs["clip_timestamps"] = args.clip  # 非批处理接受字符串
        else:
            transcribe_kwargs["clip_timestamps"] = [{"start": s, "end": e} for s, e in pairs]
        if pairs:
            duration = sum(e - s for s, e in pairs)
    elif not args.no_batched and not args.vad:
        if audio_array is None:
            print("[x] 批处理+关闭VAD 需要先转成 16k 单声道 wav(用 ffmpeg 预处理),"
                  "或加 --vad / --no-batched", file=sys.stderr)
            return 5
        clips = make_clips(audio_array, sample_rate=16000, chunk_s=min(args.chunk_seconds, 30.0))
        transcribe_kwargs["clip_timestamps"] = clips
        print(f"[*] 自建 {len(clips)} 个分块(每块 <= {min(args.chunk_seconds, 30.0):.0f}s,切点取最低能量处)", flush=True)

    if args.no_batched:
        seg_iter, info = model.transcribe(audio_input, **transcribe_kwargs)
    else:
        pipe = BatchedInferencePipeline(model=model)
        seg_iter, info = pipe.transcribe(audio_input, batch_size=args.batch_size, **transcribe_kwargs)

    print(f"[*] 检测语言={info.language} p={info.language_probability:.3f} 时长={duration or info.duration:.1f}s", flush=True)
    raw = []
    last_report = time.time()
    for seg in seg_iter:  # 生成器,这里才真正开始推理
        raw.append(dict(
            start=float(seg.start), end=float(seg.end), text=seg.text,
            avg_logprob=float(getattr(seg, "avg_logprob", 0.0) or 0.0),
            no_speech_prob=float(getattr(seg, "no_speech_prob", 0.0) or 0.0),
            words=[dict(start=float(w.start), end=float(w.end), word=w.word)
                   for w in (getattr(seg, "words", None) or [])],
        ))
        if time.time() - last_report > 15:
            print(f"    ... 已到 {raw[-1]['end']:.0f}s / {duration or info.duration:.0f}s", flush=True)
            last_report = time.time()
    asr_s = time.time() - t1
    total_duration = duration or info.duration

    kept = clean_segments(raw, drop_filler=not args.keep_filler)
    print(f"[*] 原始段 {len(raw)} -> 清洗后 {len(kept)}  (丢 {len(raw) - len(kept)})")

    # 重新断句:优先用词级时间戳精确切,没有词信息再按字数等分
    final = []
    for s in kept:
        parts = split_by_words(s, args.max_line)
        if parts:
            final.extend(parts)
            continue
        parts = split_long_text(s["text"], args.max_line)
        if len(parts) <= 1:
            final.append(s)
            continue
        span = max(s["end"] - s["start"], 0.01)
        total = sum(len(p) for p in parts) or 1
        cur = s["start"]
        for p in parts:
            dur = span * (len(p) / total)
            final.append({**s, "start": cur, "end": min(cur + dur, s["end"]), "text": p})
            cur += dur

    # 纠错表:确定性替换已知误识(模型提示词修不掉的那些)
    if args.fix_map:
        fixes = json.loads(Path(args.fix_map).read_text(encoding="utf-8"))
        if fixes:
            pat = re.compile("|".join(re.escape(k) for k in sorted(fixes, key=len, reverse=True)))
            hit = 0
            for s in final:
                new = pat.sub(lambda m: fixes[m.group(0)], s["text"])
                if new != s["text"]:
                    hit += 1
                    s["text"] = new
            print(f"[*] 纠错表命中 {hit} 行 <- {args.fix_map}")

    srt_path = outdir / f"{name}.ja.srt"
    write_srt(final, srt_path)
    (outdir / f"{name}.ja.txt").write_text(
        "\n".join(s["text"] for s in final), encoding="utf-8")
    (outdir / f"{name}.ja.json").write_text(
        json.dumps({"segments": final, "info": {
            "language": info.language, "duration": total_duration, "model": args.model}},
            ensure_ascii=False, indent=1), encoding="utf-8")
    (outdir / f"{name}.meta.json").write_text(json.dumps({
        "model": args.model, "device": args.device, "compute_type": args.compute_type,
        "batch_size": args.batch_size, "vad": args.vad, "clip": args.clip,
        "audio_duration_s": round(total_duration, 1),
        "model_load_s": round(load_s, 1), "asr_s": round(asr_s, 1),
        "realtime_factor": round(asr_s / max(total_duration, 0.01), 3),
        "segments_raw": len(raw), "segments_kept": len(final),
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[+] 音频 {total_duration:.0f}s,推理 {asr_s:.0f}s,实时率 x{asr_s / max(total_duration, .01):.2f}")
    print(f"[+] 写出: {srt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
