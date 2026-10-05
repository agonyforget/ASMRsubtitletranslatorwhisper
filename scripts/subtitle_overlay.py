#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""字幕悬浮层:置顶 + 全透明背景 + 鼠标穿透,永不挡住下面的操作。

为什么需要它:PotPlayer 置顶后那个窗口(视频/黑底)会一直挡住别的程序,而 PotPlayer
本身**没有鼠标穿透功能**(语言文件里查不到相关项),所以原生无解。这个悬浮层用
Windows 的 layered window + WS_EX_TRANSPARENT 实现点击穿透:鼠标点上去会直接落到
下面的窗口,字幕看得见但摸不着。

两种用法:
  1) 自带播放(推荐,零配置、天然同步):
     python subtitle_overlay.py --srt xx.bilingual.srt --audio xx.mkv
  2) 只做字幕层,配合 PotPlayer 等播放器(手动对时/微调):
     python subtitle_overlay.py --srt xx.bilingual.srt --start 0

控制窗(独立小窗,不影响穿透层):空格=播放/暂停,←/→=±0.5s,Shift+←/→=±5s,
+/-=字号,q=退出。穿透层本身收不到鼠标和键盘,这是设计如此。

只依赖 Python 标准库(tkinter + ctypes)和 ffplay(仅自带播放时需要)。
"""
from __future__ import annotations

import argparse
import ctypes
import re
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------- Win32 常量
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020      # 关键:鼠标穿透
WS_EX_NOACTIVATE = 0x08000000       # 不抢焦点
WS_EX_TOOLWINDOW = 0x00000080       # 不出现在 Alt+Tab

TRANSPARENT_KEY = "#0a0b0c"         # 这个颜色的像素会变成完全透明(选个文字不会用到的色)

SRT_TIME = re.compile(
    r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def _t(ms: str) -> float:
    return int(ms) / 1000.0


def parse_srt(path: Path):
    """解析 SRT -> [(start, end, [行,行])],容忍 BOM、CRLF、乱序、空块."""
    raw = path.read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", raw):
        lines = [l for l in block.split("\n") if l.strip()]
        if len(lines) < 2:
            continue
        m = None
        body_at = 1
        for i, l in enumerate(lines[:3]):
            m = SRT_TIME.search(l)
            if m:
                body_at = i + 1
                break
        if not m:
            continue
        g = m.groups()
        start = int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) + _t(g[3])
        end = int(g[4]) * 3600 + int(g[5]) * 60 + int(g[6]) + _t(g[7])
        body = [l.strip() for l in lines[body_at:] if l.strip()]
        if body:
            cues.append((start, end, body))
    cues.sort(key=lambda c: c[0])
    return cues


def cue_at(cues, t: float):
    """返回 t 时刻该显示的内容;没有则返回 None。带二分查找."""
    lo, hi = 0, len(cues) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        s, e, body = cues[mid]
        if t < s:
            hi = mid - 1
        elif t >= e:
            lo = mid + 1
        else:
            return body
    return None


class Overlay:
    """透明 + 穿透 + 置顶 的字幕窗."""

    def __init__(self, root, font_size: int, ja_size: int, width: int, bottom: int,
                 clickthrough: bool, opacity: float):
        import tkinter as tk

        self.tk = tk
        self.root = root
        self.bottom = bottom
        self.clickthrough = clickthrough
        self._signature = None

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        if opacity < 1.0:
            root.attributes("-alpha", opacity)
        root.configure(bg=TRANSPARENT_KEY)
        try:
            root.attributes("-transparentcolor", TRANSPARENT_KEY)
        except Exception as e:      # 非 Windows 或 Tk 版本不支持
            print(f"[!] 不支持透明色,背景会不透明: {e}", file=sys.stderr)

        self.frame = tk.Frame(root, bg=TRANSPARENT_KEY)
        self.frame.pack()

        self.zh = self._outlined_label(font_size, "Microsoft YaHei", "#ffffff", width)
        self.ja = self._outlined_label(ja_size, "Microsoft YaHei", "#d8d8d8", width)

        self.apply_clickthrough()

    def _outlined_label(self, size, family, color, width):
        """用 4 个黑色偏移副本 + 1 个亮色副本模拟描边,保证在任何桌面背景上都读得清."""
        tk = self.tk
        holder = tk.Frame(self.frame, bg=TRANSPARENT_KEY)
        holder.pack(pady=1)
        shadows = []
        for dx, dy in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
            lb = tk.Label(holder, text="", font=(family, size, "bold"),
                          fg="#000000", bg=TRANSPARENT_KEY, wraplength=width, justify="center")
            lb.place(x=dx, y=dy)
            shadows.append(lb)
        main = tk.Label(holder, text="", font=(family, size, "bold"),
                        fg=color, bg=TRANSPARENT_KEY, wraplength=width, justify="center")
        main.place(x=0, y=0)
        holder._shadows = shadows
        holder._main = main
        return holder

    def _set(self, holder, text):
        if holder._main.cget("text") == text:
            return False
        for lb in holder._shadows:
            lb.configure(text=text)
        holder._main.configure(text=text)
        # 让 holder 尺寸跟随文字
        holder.update_idletasks()
        w = max(holder._main.winfo_reqwidth(), 1)
        h = max(holder._main.winfo_reqheight(), 1)
        holder.configure(width=w, height=h)
        holder.pack_propagate(False)
        for lb in holder._shadows + [holder._main]:
            lb.configure(wraplength=w if w > 10 else lb.cget("wraplength"))
        return True

    def show(self, lines):
        """lines: 字幕块里的若干行(第一行中文,第二行原文...).传 None 表示清空."""
        zh = lines[0] if lines else ""
        ja = " ".join(lines[1:]) if lines and len(lines) > 1 else ""
        sig = (zh, ja)
        if sig == self._signature:
            return
        self._signature = sig
        self._set(self.zh, zh)
        self._set(self.ja, ja)
        if not zh and not ja:
            self.root.withdraw()
            return
        if self.root.state() == "withdrawn":
            self.root.deiconify()
        self._reposition()

    def _reposition(self):
        self.root.update_idletasks()
        w = max(self.root.winfo_reqwidth(), 10)
        h = max(self.root.winfo_reqheight(), 10)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, sh - h - self.bottom)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _hwnd_chain(self):
        """Tk 的 overrideredirect 窗口可能有包装父窗口,真正的顶层不一定是谁;
        所以返回从 winfo_id 到顶层(不含桌面)的整条链,样式全加上才稳."""
        u32 = ctypes.windll.user32
        desktop = u32.GetDesktopWindow()
        chain, seen = [], set()
        h = self.root.winfo_id()
        for _ in range(8):
            if not h or h in seen or h == desktop:
                break
            seen.add(h)
            chain.append(h)
            h = u32.GetParent(h)
        return chain

    def apply_clickthrough(self):
        """给窗口加 穿透/不激活/工具窗 扩展样式.可重复调用(Tk 重映射后样式可能丢)."""
        if not self.clickthrough:
            return
        u32 = ctypes.windll.user32
        for hwnd in self._hwnd_chain():
            style = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            u32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                               style | WS_EX_LAYERED | WS_EX_TRANSPARENT
                               | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)

    def verify_clickthrough(self):
        u32 = ctypes.windll.user32
        out = {}
        for i, hwnd in enumerate(self._hwnd_chain()):
            st = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            out[f"level{i}"] = {
                "hwnd": hex(hwnd),
                "style": hex(st & 0xFFFFFFFF),
                "WS_EX_TRANSPARENT": bool(st & WS_EX_TRANSPARENT),
                "WS_EX_LAYERED": bool(st & WS_EX_LAYERED),
                "WS_EX_NOACTIVATE": bool(st & WS_EX_NOACTIVATE),
                "WS_EX_TOOLWINDOW": bool(st & WS_EX_TOOLWINDOW),
            }
        return out


class Player:
    """时钟 + (可选)ffplay 音频播放.暂停/跳转用重启 ffplay 实现,简单可靠."""

    def __init__(self, audio: str | None, start: float, ffplay: str = "ffplay"):
        self.audio = audio
        self.ffplay = ffplay
        self.base = start
        self.t0 = time.monotonic()
        self.running = False
        self.proc = None

    @property
    def position(self) -> float:
        if not self.running:
            return self.base
        return self.base + (time.monotonic() - self.t0)

    def _spawn(self):
        if not self.audio:
            return
        self._kill()
        cmd = [self.ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet",
               "-ss", f"{max(self.base, 0):.3f}", "-i", self.audio]
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            print(f"[!] 找不到 {self.ffplay},改为纯字幕模式(不影响字幕)", file=sys.stderr)
            self.audio = None
            self.proc = None

    def _kill(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None

    def play(self):
        if self.running:
            return
        self.t0 = time.monotonic()
        self.running = True
        self._spawn()

    def pause(self):
        if not self.running:
            return
        self.base = self.position
        self.running = False
        self._kill()

    def toggle(self):
        self.pause() if self.running else self.play()

    def seek(self, delta: float):
        self.base = max(0.0, self.position + delta)
        self.t0 = time.monotonic()
        if self.running:
            self._spawn()

    def stop(self):
        self.running = False
        self._kill()


def fmt(t: float) -> str:
    t = max(t, 0)
    return f"{int(t // 60):02d}:{t % 60:05.2f}"


def build_control(root, player: Player, overlay: Overlay, cues, args, state):
    """独立控制小窗.穿透层收不到输入,所以操作都放这里."""
    import tkinter as tk

    win = tk.Toplevel(root)
    win.title("字幕悬浮层 · 控制")
    win.attributes("-topmost", True)
    win.configure(bg="#f0f0f0")

    status = tk.Label(win, text="", font=("Consolas", 10), bg="#f0f0f0", justify="left")
    status.pack(fill="x", padx=8, pady=(6, 2))

    def refresh():
        cur = cue_at(cues, player.position)
        head = cur[0][:26] if cur else "(无字幕)"
        status.configure(
            text=(f"状态 {'播放中' if player.running else '已暂停'}\n"
                  f"位置 {fmt(player.position)}   偏移 {args.offset:+.1f}s\n"
                  f"字号 {state['size']}   当前: {head}"))
        win.after(150, refresh)

    row1 = tk.Frame(win, bg="#f0f0f0"); row1.pack(pady=2)
    row2 = tk.Frame(win, bg="#f0f0f0"); row2.pack(pady=2)
    row3 = tk.Frame(win, bg="#f0f0f0"); row3.pack(pady=(2, 8))

    tk.Button(row1, text="▶/⏸ 空格", width=10, command=player.toggle).pack(side="left", padx=2)
    tk.Button(row1, text="-5s", width=5, command=lambda: player.seek(-5)).pack(side="left", padx=2)
    tk.Button(row1, text="-0.5s", width=6, command=lambda: player.seek(-0.5)).pack(side="left", padx=2)
    tk.Button(row1, text="+0.5s", width=6, command=lambda: player.seek(0.5)).pack(side="left", padx=2)
    tk.Button(row1, text="+5s", width=5, command=lambda: player.seek(5)).pack(side="left", padx=2)

    def font_delta(d):
        state["size"] = max(14, state["size"] + d)
        overlay.zh._main.configure(font=(overlay.zh._main.cget("font")[0], state["size"], "bold"))
        for lb in overlay.zh._shadows:
            lb.configure(font=(lb.cget("font")[0], state["size"], "bold"))
        for lb in overlay.ja._shadows + [overlay.ja._main]:
            lb.configure(font=(lb.cget("font")[0], max(12, int(state["size"] * 0.62)), "bold"))
        overlay._signature = None
        overlay.show(cue_at(cues, player.position))

    tk.Button(row2, text="字号 -", width=8, command=lambda: font_delta(-2)).pack(side="left", padx=2)
    tk.Button(row2, text="字号 +", width=8, command=lambda: font_delta(2)).pack(side="left", padx=2)
    tk.Button(row2, text="重新穿透", width=10,
              command=overlay.apply_clickthrough).pack(side="left", padx=2)
    tk.Button(row3, text="退出", width=10,
              command=lambda: (player.stop(), root.destroy())).pack()

    def on_key(e):
        if e.keysym == "space":
            player.toggle()
        elif e.keysym == "Left":
            player.seek(-5 if (e.state & 0x0001) else -0.5)
        elif e.keysym == "Right":
            player.seek(5 if (e.state & 0x0001) else 0.5)
        elif e.char in ("+", "="):
            font_delta(2)
        elif e.char in ("-", "_"):
            font_delta(-2)
        elif e.char in ("q", "Q"):
            player.stop(); root.destroy()

    win.bind("<Key>", on_key)
    win.focus_force()
    refresh()
    return win


def selftest(args) -> int:
    """不依赖肉眼的自检:解析、时间点选行、窗口样式是否真的生效."""
    cues = parse_srt(Path(args.srt))
    print(f"[1] 解析 SRT: {len(cues)} 条")
    if not cues:
        print("[x] 没解析出字幕条目"); return 1
    print(f"    首条 {cues[0][0]:.2f}-{cues[0][1]:.2f}s  {cues[0][2][0][:30]}")
    print(f"    末条 {cues[-1][0]:.2f}-{cues[-1][1]:.2f}s")

    # 时间点选行:取每条的中点必须命中它自己
    bad = 0
    for i, (s, e, body) in enumerate(cues):
        got = cue_at(cues, (s + e) / 2)
        if got != body:
            bad += 1
    print(f"[2] 逐条中点命中: {'OK' if bad == 0 else f'{bad} 条不匹配'}")

    # 间隙应无字幕
    gaps = 0
    for (s1, e1, _), (s2, e2, _) in zip(cues, cues[1:]):
        if s2 - e1 > 1.0 and cue_at(cues, (e1 + s2) / 2) is not None:
            gaps += 1
    print(f"[3] 间隙不误显示: {'OK' if gaps == 0 else f'{gaps} 处异常'}")

    # 窗口 + 穿透样式
    import tkinter as tk
    root = tk.Tk()
    ov = Overlay(root, args.font_size, int(args.font_size * 0.62), args.width,
                 args.bottom, not args.no_clickthrough, args.opacity)
    ov.show(cues[0][2])
    # 和主循环一样:映射后再补加几次样式,否则测到的是 Tk 刚重写完的中间状态
    for _ in range(6):
        ov.apply_clickthrough()
        root.update()
        time.sleep(0.05)
    info = ov.verify_clickthrough()
    print("[4] 窗口样式(父链):")
    all_ct = True
    for tag, st in info.items():
        print(f"    {tag}: {st}")
        if not (st["WS_EX_TRANSPARENT"] and st["WS_EX_LAYERED"]):
            all_ct = False
    print(f"[5] 鼠标穿透生效(所有层级): {'OK' if all_ct or args.no_clickthrough else 'FAIL'}")
    root.destroy()

    good = (bad == 0 and gaps == 0 and (all_ct or args.no_clickthrough))
    print("SELFTEST", "PASS" if good else "FAIL")
    return 0 if good else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="置顶+透明+鼠标穿透的字幕悬浮层")
    ap.add_argument("--srt", required=True, help="字幕文件(推荐用 *.bilingual.srt,一行中文一行原文)")
    ap.add_argument("--audio", default=None,
                    help="音频/视频文件;给了就用 ffplay 播放,进度天然同步;不给则只显示字幕")
    ap.add_argument("--ffplay", default="ffplay")
    ap.add_argument("--start", type=float, default=0.0, help="从第几秒开始")
    ap.add_argument("--offset", type=float, default=0.0, help="字幕整体提前(+)/延后(-)多少秒")
    ap.add_argument("--font-size", type=int, default=44)
    ap.add_argument("--width", type=int, default=1500, help="换行宽度")
    ap.add_argument("--bottom", type=int, default=90, help="距屏幕底部像素")
    ap.add_argument("--opacity", type=float, default=1.0, help="0.3-1.0,整层透明度")
    ap.add_argument("--no-clickthrough", action="store_true", help="关闭鼠标穿透(调试用)")
    ap.add_argument("--no-control", action="store_true", help="不显示控制窗(只剩字幕层,靠 --start 播)")
    ap.add_argument("--autoplay", action="store_true", help="启动即开始计时/播放")
    ap.add_argument("--selftest", action="store_true", help="自检后退出(不显示界面)")
    ap.add_argument("--exit-after", type=float, default=0.0, help="N 秒后自动退出(测试用)")
    args = ap.parse_args()

    p = Path(args.srt)
    if not p.exists():
        print(f"[x] 找不到字幕: {p}", file=sys.stderr)
        return 2

    if args.selftest:
        return selftest(args)

    cues = parse_srt(p)
    if not cues:
        print(f"[x] {p} 里没解析出字幕条目", file=sys.stderr)
        return 2
    print(f"[*] 载入 {len(cues)} 条字幕,时长约 {fmt(cues[-1][1])}")

    import tkinter as tk
    root = tk.Tk()
    root.title("subtitle overlay")
    ov = Overlay(root, args.font_size, int(args.font_size * 0.62), args.width,
                 args.bottom, not args.no_clickthrough, args.opacity)
    root.withdraw()

    player = Player(args.audio, args.start, args.ffplay)
    state = {"size": args.font_size}

    if not args.no_control:
        build_control(root, player, ov, cues, args, state)
    if args.autoplay:
        player.play()

    def tick():
        body = cue_at(cues, player.position + args.offset)
        ov.show(body)
        if ov.clickthrough:
            ov.apply_clickthrough()      # 重映射后兜底
        root.after(80, tick)

    tick()
    if args.exit_after > 0:
        root.after(int(args.exit_after * 1000), lambda: (player.stop(), root.destroy()))
    try:
        root.mainloop()
    finally:
        player.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
