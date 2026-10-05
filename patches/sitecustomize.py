# -*- coding: utf-8 -*-
"""DSH 沙箱兼容补丁(sitecustomize 自动加载).

问题:CPython 在 Windows 上执行 `os.mkdir(path, 0o700)` 时会写入一个"不继承上级"
的权限描述符,导致该目录的访问权不再继承工作区授权 —— 于是**创建它的那个进程自己
都写不进去**(pip 下载/解包时报 PermissionError: [Errno 13],DSH 沙箱则报
file access denied)。

`tempfile.mkdtemp()` / `tempfile.mkstemp()` 恰好就是这么建目录/文件,所以 pip、
huggingface_hub 等一切依赖 tempfile 的代码在沙箱里都会失败。

修法:用默认权限(继承上级 ACL)建同名临时目录/文件,行为与标准库一致,
只是不再写入那个破坏继承的描述符。仅在 Windows 上生效。
"""
import os
import sys
import tempfile

if sys.platform == "win32":
    _orig_mkdtemp = tempfile.mkdtemp

    def _mkdtemp(suffix=None, prefix=None, dir=None):
        suffix = suffix or ""
        prefix = prefix or tempfile.gettempprefix()
        if dir is None:
            dir = tempfile.gettempdir()
        for _ in range(100):
            name = os.path.join(dir, prefix + next(tempfile._get_candidate_names()) + suffix)
            try:
                os.mkdir(name)  # 不传 mode -> 继承上级权限,沙箱可用
            except FileExistsError:
                continue
            try:
                os.chmod(name, 0o700)
            except OSError:
                pass
            return name
        raise FileExistsError("no usable temporary directory name found")

    def _mkstemp(suffix=None, prefix=None, dir=None, text=False):
        suffix = suffix or ""
        prefix = prefix or tempfile.gettempprefix()
        if dir is None:
            dir = tempfile.gettempdir()
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
        if text:
            flags |= getattr(os, "O_TEXT", 0)
        for _ in range(100):
            name = os.path.join(dir, prefix + next(tempfile._get_candidate_names()) + suffix)
            try:
                fd = os.open(name, flags, 0o600)
            except FileExistsError:
                continue
            return fd, name
        raise FileExistsError("no usable temporary filename found")

    tempfile.mkdtemp = _mkdtemp
    tempfile.mkstemp = _mkstemp
    tempfile._mkdtemp = _mkdtemp
