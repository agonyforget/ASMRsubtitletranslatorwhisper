#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""字幕工具 · 双击即用入口.

为什么需要它:双击 .cmd 等于"不带参数运行",而命令行工具必须给参数,
于是窗口一闪就没了。这个入口负责:

  * 不带参数时给出菜单 + 文件选择框(tkinter),选完就能跑;
  * 有参数时原样转交给对应脚本(命令行用法不变);
  * 把每一步的结果、产物路径、下一步建议都打印清楚,并等回车再关窗;
  * API key 缺失时给三条出路(输入 key / 只验证流程 / 用已有译文缓存直接合成)。

子命令:
  translate <字幕文件> [额外参数...]   翻译字幕
  asr       <音视频>   [额外参数...]   音视频 -> 字幕
  overlay   <字幕文件> [额外参数...]   启动悬浮层
  check     <字幕文件或.ja.json>       校验译文条数是否对齐
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent          # ...\whisper
SCRIPTS = BASE / "scripts"
PY = Path(sys.executable)
PYW = PY.with_name("pythonw.exe")
if not PYW.exists():
    PYW = PY
OUT_DEFAULT = BASE / "out"

SRT_FILTER = [("字幕文件", "*.srt"), ("faster-whisper 结果", "*.ja.json"), ("所有文件", "*.*")]
MEDIA_FILTER = [("音视频", "*.mp3 *.wav *.m4a *.flac *.mp4 *.mkv *.opus"),
                ("所有文件", "*.*")]
LANG_TAGS = (".ja", ".jp", ".en", ".zh", ".ko", ".chs", ".cht")


def base_name(filename: str) -> str:
    """movie.ja.srt -> movie;  05_se.bilingual.srt -> 05_se"""
    n = filename
    for suf in (".bilingual", ".zh", ".ja", ".en"):
        if n.endswith(suf):
            n = n[: -len(suf)]
    for suf in (".srt", ".ass", ".json"):
        if n.endswith(suf):
            n = n[: -len(suf)]
    for tag in LANG_TAGS:
        if n.endswith(tag):
            n = n[: -len(tag)]
    return n or filename


# ----------------------------------------------------------------- 小工具
def hr(title=""):
    print("\n" + "=" * 62)
    if title:
        print(title)
        print("=" * 62)


def ask_text(prompt: str) -> str:
    try:
        s = input(prompt).strip()
    except EOFError:
        return ""
    return s.strip().strip('"').strip("'")


def ask_file(title: str, filetypes, use_gui: bool) -> str | None:
    if use_gui:
        try:
            import tkinter as tk
            from tkinter import filedialog
            r = tk.Tk()
            r.withdraw()
            r.attributes("-topmost", True)
            p = filedialog.askopenfilename(title=title, filetypes=filetypes)
            r.destroy()
            if p:
                print(f"[*] 已选择: {p}")
                return p
            print("[!] 没选文件")
            return None
        except Exception as e:
            print(f"[!] 文件选择框打不开({e}),改为手动输入路径")
    while True:
        s = ask_text(f"{title}\n  把文件拖到本窗口后按回车(或直接输入路径,q 取消): ")
        if s.lower() in ("q", "quit", ""):
            return None
        p = Path(s)
        if p.exists():
            return str(p)
        print(f"[x] 找不到: {p}")


def ask_dir(title: str, default: Path, use_gui: bool) -> Path:
    if use_gui:
        try:
            import tkinter as tk
            from tkinter import filedialog
            r = tk.Tk()
            r.withdraw()
            r.attributes("-topmost", True)
            p = filedialog.askdirectory(title=title, initialdir=str(default))
            r.destroy()
            if p:
                return Path(p)
        except Exception:
            pass
    s = ask_text(f"{title}\n  回车用默认 {default}: ")
    return Path(s) if s else default


def run(cmd, **kw) -> int:
    """跑子进程并把输出直接透到当前控制台."""
    print("  $ " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd))
    sys.stdout.flush()          # 不 flush 的话子进程输出会插到本行前面
    env = dict(os.environ)
    env["PYTHONPATH"] = lib_path()
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("TEMP", str(BASE / "tmp"))
    env.setdefault("TMP", env["TEMP"])
    env.setdefault("HF_HOME", str(BASE / "hf2"))
    env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    env.setdefault("HF_HUB_DISABLE_XET", "1")
    try:
        return subprocess.call([str(c) for c in cmd], env=env, **kw)
    except KeyboardInterrupt:
        print("\n[!] 已被用户中断")
        return 130


def lib_path() -> str:
    """依赖目录 + 沙箱补丁目录(pylibs 是 pip 装出来的,patches 是仓库里的)."""
    return f"{BASE / 'pylibs'};{BASE / 'patches'}"


def api_key() -> str:
    return os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""


def seg_count(src: Path) -> int | None:
    if src.suffix.lower() == ".json":
        try:
            d = json.loads(src.read_text(encoding="utf-8"))
            segs = d["segments"] if isinstance(d, dict) else d
            return len(segs)
        except Exception:
            return None
    try:
        txt = src.read_text(encoding="utf-8-sig", errors="replace")
        return len(re.findall(r"-->\s*\d", txt))
    except Exception:
        return None


def open_folder(p: Path):
    try:
        subprocess.Popen(["explorer", str(p)])
    except Exception:
        pass


# ----------------------------------------------------------------- 各子命令
def cmd_translate(rest, use_gui: bool) -> int:
    hr("翻译字幕")
    if rest:
        src = Path(rest[0])
        extra = rest[1:]
    else:
        p = ask_file("选择要翻译的字幕文件(任意语言 .srt,或 .ja.json)", SRT_FILTER, use_gui)
        if not p:
            return 0
        src, extra = Path(p), []
    if not src.exists():
        print(f"[x] 找不到文件: {src}")
        return 2

    name = base_name(src.name)
    outdir = Path(extra[extra.index("--outdir") + 1]) if "--outdir" in extra else (
        src.parent if not use_gui else ask_dir("输出到哪个目录?(建议和字幕同目录)", src.parent, use_gui))
    name = extra[extra.index("--name") + 1] if "--name" in extra else name

    n = seg_count(src)
    print(f"[*] 输入: {src}")
    print(f"[*] 字幕条数: {n if n is not None else '解析失败'}")
    print(f"[*] 输出目录: {outdir}    输出名: {name}")

    # 提示词:默认日译中,问一下是不是英文
    if "--prompt-file" not in extra and "--prompt" not in extra and not rest:
        ans = ask_text("原文是什么语言?  1=日语(默认)  2=英语  其他=用默认: ")
        if ans.strip() == "2":
            extra += ["--prompt-file", str(SCRIPTS / "prompt_en2zh.txt")]

    cache = Path(outdir) / f"{name}.zh.cache.json"
    cmd = [PY, SCRIPTS / "translate_zh.py", src, "--outdir", outdir, "--name", name, *extra]
    rc = 0

    if api_key():
        print("[*] 检测到 API key,开始调用接口翻译")
        rc = run(cmd)
    elif cache.exists():
        try:
            cached = len(json.loads(cache.read_text(encoding="utf-8")))
        except Exception:
            cached = -1
        print(f"[*] 没检测到 API key,但已有译文缓存({cached} 条),将直接用缓存合成")
        rc = run(cmd)
    else:
        print("[!] 没有检测到 API key(环境变量 DEEPSEEK_API_KEY / OPENAI_API_KEY)")
        print("    你可以:")
        print("      a) 现在粘贴一个 API key(DeepSeek 等,输入时不显示)")
        print("      b) 不调接口,只跑流程验证(--dry-run,输出里中文位置放原文)")
        print("      c) 我自己有译文缓存,直接合成")
        ch = ask_text("  选 a/b/c [b]: ").lower() or "b"
        if ch == "a":
            import getpass
            k = getpass.getpass("  API key: ").strip()
            if k:
                os.environ["DEEPSEEK_API_KEY"] = k
                rc = run(cmd)
            else:
                print("[x] 没输入 key"); return 1
        elif ch == "c":
            if cache.exists():
                rc = run(cmd)
            else:
                print(f"[x] 没找到缓存文件: {cache}")
                print("    格式是 {\"1\":\"译文\",\"2\":\"译文\",...},键要和字幕序号一一对应")
                return 1
        else:
            rc = run(cmd + ["--dry-run"])

    hr("结果")
    made = [outdir / f"{name}.zh.srt", outdir / f"{name}.bilingual.srt",
            outdir / f"{name}.bilingual.ass"]
    for f in made:
        print(f"  {'[√]' if f.exists() else '[ ]'} {f}")
    print(f"  译文缓存: {cache}  (重跑会跳过已翻的行,别删)")
    if rc != 0:
        print(f"[!] 脚本返回码 {rc};若只是网络/接口问题,重跑本命令即可续翻")
    if any(f.exists() for f in made):
        print("\n下一步:用 PotPlayer 直接打开视频/音频,它会自动加载同目录同名的字幕;")
        print("        想要置顶透明悬浮字幕,回到菜单选 3。")
    return rc


def cmd_asr(rest, use_gui: bool) -> int:
    hr("音视频 → 字幕")
    if rest:
        media = Path(rest[0]); extra = rest[1:]
    else:
        p = ask_file("选择音视频文件", MEDIA_FILTER, use_gui)
        if not p:
            return 0
        media, extra = Path(p), []
    print(f"[*] 输入: {media}  ({media.stat().st_size/1024/1024:.1f} MB)")
    print("[*] 这一步是本地识别,速度约音频时长的 1/3(纯 CPU),长文件请耐心等")

    outdir = Path(extra[extra.index("--outdir") + 1]) if "--outdir" in extra else OUT_DEFAULT
    name = extra[extra.index("--name") + 1] if "--name" in extra else media.stem
    outdir.mkdir(parents=True, exist_ok=True)
    work = BASE / "audio"
    work.mkdir(exist_ok=True)
    wav = work / f"{name}.wav"

    if not wav.exists():
        print(f"[1/2] ffmpeg 归一化 -> {wav}")
        rc = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", media,
                  "-vn", "-ac", "1", "-ar", "16000",
                  "-af", "highpass=f=60,loudnorm=I=-18:TP=-1.5:LRA=11",
                  "-c:a", "pcm_s16le", wav])
        if rc != 0:
            print("[x] ffmpeg 失败"); return rc
    else:
        print(f"[1/2] 已有归一化文件,跳过: {wav}")

    print("[2/2] 识别中 ...")
    rc = run([PY, SCRIPTS / "transcribe_asmr.py", wav, "--outdir", outdir, "--name", name,
              "--model", "large-v3-turbo", "--vad", "--vad-threshold", "0.5",
              "--batch-size", "8", "--max-line", "40", *extra])
    if rc != 0:
        print(f"[x] 识别失败,返回码 {rc}"); return rc

    srt = outdir / f"{name}.ja.srt"
    print(f"[√] 日文字幕: {srt}")
    if ask_text("现在就翻译成中文吗? [Y/n]: ").lower() in ("", "y", "yes"):
        return cmd_translate([str(srt), "--outdir", str(outdir), "--name", name], use_gui=False)
    return 0


def cmd_overlay(rest, use_gui: bool) -> int:
    hr("字幕悬浮层(置顶/透明/鼠标穿透)")
    rest = list(rest)
    no_launch = "--no-launch" in rest          # 只自检不启动,排错用
    if no_launch:
        rest.remove("--no-launch")
    if rest:
        srt = Path(rest[0]); extra = rest[1:]
    else:
        p = ask_file("选择字幕文件(推荐 .bilingual.srt)", SRT_FILTER, use_gui)
        if not p:
            return 0
        srt, extra = Path(p), []
    if not srt.exists():
        print(f"[x] 找不到字幕: {srt}"); return 2
    n = seg_count(srt)
    print(f"[*] 字幕: {srt}  ({n} 条)")

    audio = None
    if "--audio" in extra:
        audio = Path(extra[extra.index("--audio") + 1])
    elif not rest:
        a = ask_file("可选:要不要让它自己播放音频?选音频文件(不想就取消)", MEDIA_FILTER, use_gui)
        audio = Path(a) if a else None
    if audio:
        extra += ["--audio", str(audio)]
        print(f"[*] 自带播放: {audio}")

    print("[*] 先自检一遍(解析字幕 + 验证鼠标穿透样式) ...")
    rc = run([PY, SCRIPTS / "subtitle_overlay.py", "--srt", srt, "--selftest"])
    if rc != 0:
        print("[x] 自检没通过,先别启动;把上面的报错发我看看")
        return rc
    if no_launch:
        print("[*] --no-launch:自检通过,按要求不启动窗口")
        return 0

    print("[*] 启动悬浮层 ...(控制窗里有:空格=播放/暂停,←/→=±0.5s,+/-=字号,q=退出)")
    env = dict(os.environ)
    env["PYTHONPATH"] = lib_path()
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("TEMP", str(BASE / "tmp"))
    env.setdefault("TMP", env["TEMP"])
    subprocess.Popen([str(PYW), str(SCRIPTS / "subtitle_overlay.py"), "--srt", str(srt), *extra], env=env)
    print("[√] 已启动。如果屏幕上没看到字幕,检查:字幕条数是否为 0、是否被别的置顶窗口盖住。")
    return 0


def cmd_check(rest, use_gui: bool) -> int:
    hr("校验译文条数是否对齐")
    if rest:
        src = Path(rest[0])
    else:
        p = ask_file("选择字幕文件(.ja.json 或 .srt)", SRT_FILTER, use_gui)
        if not p:
            return 0
        src = Path(p)
    name = base_name(src.name)
    cache = src.parent / f"{name}.zh.cache.json"
    n = seg_count(src)
    if n is None:
        print(f"[x] 解析不出条数: {src}"); return 2
    if not cache.exists():
        print(f"[x] 没找到译文缓存: {cache}"); return 2
    try:
        zh = json.loads(cache.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[x] 缓存读不了: {e}"); return 2
    keys = [k for k in zh if str(k).isdigit()]
    missing = [i for i in range(1, n + 1) if str(i) not in zh]
    print(f"  字幕条数 : {n}")
    print(f"  译文条数 : {len(keys)}")
    print(f"  缺号     : {missing[:20]}{' ...' if len(missing) > 20 else ''}")
    ok = len(missing) == 0 and len(keys) == n
    print(f"  结论     : {'OK,可以用' if ok else '不一致,别用!先补齐缺号再合成'}")
    return 0 if ok else 1


# ----------------------------------------------------------------- 菜单
MENU = """
 1  翻译字幕(已有 .srt / .ja.json → 中文、双语)
 2  音视频 → 字幕(本地识别,再翻译)
 3  启动字幕悬浮层(置顶 / 全透明 / 鼠标穿透)
 4  校验译文与字幕是否对齐
 q  退出
"""


def main() -> int:
    argv = sys.argv[1:]
    use_gui = True
    if "--no-gui" in argv:
        use_gui = False
        argv.remove("--no-gui")

    if argv and argv[0] in ("translate", "asr", "overlay", "check"):
        sub, rest = argv[0], argv[1:]
        if sub == "translate":
            return cmd_translate(rest, use_gui=False)
        if sub == "asr":
            return cmd_asr(rest, use_gui=False)
        if sub == "overlay":
            return cmd_overlay(rest, use_gui=False)
        return cmd_check(rest, use_gui=False)

    hr("字幕工具")
    print(f"  Python : {PY}")
    print(f"  工作目录: {BASE}")
    print(f"  API key : {'已设置' if api_key() else '未设置(菜单 1 里会给你三种选择)'}")
    print(MENU)
    choice = ask_text("请输入序号后回车: ").strip().lower()

    if choice == "1":
        return cmd_translate([], use_gui)
    if choice == "2":
        return cmd_asr([], use_gui)
    if choice == "3":
        return cmd_overlay([], use_gui)
    if choice == "4":
        return cmd_check([], use_gui)
    print("已退出")
    return 0


if __name__ == "__main__":
    code = 0
    try:
        code = main()
    except KeyboardInterrupt:
        print("\n[!] 已中断")
        code = 130
    except Exception:
        import traceback
        print("\n[x] 出错了:")
        traceback.print_exc()
        code = 1
    print("\n" + "-" * 62)
    try:
        input("按回车关闭窗口 ...")
    except EOFError:
        pass
    raise SystemExit(code)
