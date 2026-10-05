# ASMR 音声 → 中日双语字幕工具链(基于 faster-whisper)

一个**纯本地跑**的音声/视频字幕工具链:语音识别 → 逐行翻译 → 中日双语字幕,再配一层
**点击穿透的桌面悬浮字幕**。参数是为"耳语、气声、低音量、长时长、带音效"的 ASMR 素材调的,
但这套流程对普通音视频同样适用。最初的需求是"用 faster-whisper 翻译 ASMR 音频并做成字幕",
这个仓库记录最终参数、踩过的坑和可直接复用的脚本。

## 功能

| 能力 | 说明 |
|---|---|
| 语音识别 | faster-whisper + CTranslate2,CPU int8 即可跑。ASMR 专用调参:VAD 切块(切口落在停顿处)、幻觉抑制、术语提示词、误识纠错表、句子级重断句、按词级时间戳均衡换行 |
| 翻译字幕 | 任意语言 `.srt` → 中文;兼容任何 OpenAI 格式接口(DeepSeek / OpenAI / 硅基流动 / 本地 Ollama),**只用标准库**;按行号严格对齐、断点续传;没有 key 也能用(让 AI 手写译文缓存即可) |
| 双语成品 | `.zh.srt`(纯中文)/ `.bilingual.srt`(对照)/ `.bilingual.ass`(中文主行 + 原文小字)/ 软字幕 `.mkv` |
| 悬浮字幕层 | 置顶 + 全透明 + **鼠标穿透**,永不挡住下面的操作;可自带 ffplay 播放实现零配置同步 |
| 易用入口 | 双击 `.cmd` 出菜单与文件选择框;没有 API key 时给三条出路;跑完停住不闪退 |
| 环境补丁 | 附 `patches/sitecustomize.py`:修 Windows 受限环境下 `tempfile.mkdtemp` 破坏目录权限继承、导致 pip 与模型下载全部失败的问题 |

## 实测数据(本机:12 逻辑核 CPU,无 CUDA)

| 项目 | 结果 |
|---|---|
| 模型 | `large-v3-turbo`,int8,纯 CPU |
| 速度 | **实时率 x0.17–0.33**(20 分钟音频约 4–7 分钟) |
| 批量 | 14 轨共 **228.6 分钟**音频 → **862 行**字幕,总耗时约 73 分钟 |
| 质量 | 术语提示词能修掉多数成人/专有词汇误识;人名、「監獄島」等专名稳定 |
| 已否决方案 | 30 秒硬切块(关 VAD)会丢整句、断词;`kotoba-whisper-v2.0-faster` 在本机 0xC0000005 崩溃 |

## 环境要求

- **Python 3.10+**(官方安装包即可;翻译与悬浮层只用标准库,tkinter 随官方包提供)
- 识别需要 `pip install faster-whisper`(自动带上 ctranslate2 / onnxruntime / av)
- **ffmpeg**(预处理与 ffplay 播放;不做归一化也能跑)
- 不需要 GPU;有 NVIDIA 卡可以换 `--device cuda --compute-type float16`

---

本机实测可跑的完整流程。原始需求是"用 faster-whisper 翻译 ASMR 音频并做成字幕",这里记录
踩过的坑、最终参数和可复用脚本。

---

## 0. 一句话结论

**faster-whisper 的 `task="translate"` 只能翻成英文**,所以"中文字幕"必须拆成两段:
`ASR(带时间轴) → LLM 翻译`。ASMR 的难点不在模型,而在 **VAD/分块策略**和**幻觉抑制**。

---

## 1. 环境(本机已验证)

| 项 | 值 |
|---|---|
| GPU | AMD Radeon RX 6650 XT → **无 CUDA**,只能 CPU int8(CTranslate2 在 Windows 上不支持 AMD) |
| CPU | 12 逻辑核 |
| ffmpeg | `C:\Users\chr\AppData\Local\Programs\ffmpeg\bin\ffmpeg.exe` 9.0.1 |
| Python | 系统无 Python(只有 Store 别名);用 DSH 自带 `C:\Users\chr\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe` 3.12.14 |
| 依赖 | `whisper\pylibs\`(pip `--target` 安装,不动 harness 运行时) |
| 模型 | `large-v3-turbo`(int8,1543 MB)缓存在 `whisper\hf2` |

### 两个必须知道的坑

1. **`python -m venv` 在本机失败**(精简发行版 ensurepip 报错)→ 用 `pip install --target` 代替。
2. **沙箱下 Python 的 `tempfile.mkdtemp` 会自毁权限**:CPython 在 Windows 上用
   `os.mkdir(path, 0o700)` 建临时目录时会写入一个"不继承上级"的权限描述符,导致**创建它的进程自己
   都写不进去**,pip / huggingface_hub 全部失败。
   修法:`whisper\pylibs\sitecustomize.py`(随 PYTHONPATH 自动加载,用默认权限重建临时目录/文件)。
   有了它,pip 安装、模型下载都能在受限沙箱里正常跑。
   另一个症状是**提权跑过的命令产生的目录,受限会话读不了**——所以依赖和模型都别用提权方式装。

---

## 2. 快速使用

```powershell
# 单个/批量出日文字幕(默认参数就是本机验证过的最佳组合)
pwsh -File E:\DeepSeekHarness\whisper\run_asmr_subtitles.ps1 `
  -InputDir 'F:\资源\ASMR\RJ01263979...\02_SE無し\MP３'

# 带上翻译(需要先设 DEEPSEEK_API_KEY;没 key 时可让 AI 直接翻,见第 5 节)
$env:DEEPSEEK_API_KEY = 'sk-xxx'
pwsh -File E:\DeepSeekHarness\whisper\run_asmr_subtitles.ps1 -InputDir '...\MP３' -Translate
```

单步命令:

```powershell
$py="C:\Users\chr\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
$env:PYTHONPATH="E:\DeepSeekHarness\whisper\pylibs"
$env:HF_HOME="E:\DeepSeekHarness\whisper\hf2"
$env:HF_ENDPOINT="https://hf-mirror.com"      # 国内下模型必须走镜像
$env:HF_HUB_DISABLE_XET="1"                   # 不开会卡在 Xet staging
$env:TEMP="E:\DeepSeekHarness\whisper\tmp"; $env:TMP=$env:TEMP

# 1) 归一化(ASMR 音量小,这一步对识别率提升明显)
ffmpeg -y -i "in.mp3" -vn -ac 1 -ar 16000 `
  -af "highpass=f=60,loudnorm=I=-18:TP=-1.5:LRA=11" -c:a pcm_s16le norm.wav

# 2) 转录
& $py E:\DeepSeekHarness\whisper\scripts\transcribe_asmr.py norm.wav `
  --outdir E:\DeepSeekHarness\whisper\out --name 01 `
  --model large-v3-turbo --vad --vad-threshold 0.5 --batch-size 8 --max-line 40 `
  --initial-prompt "媚薬ガス、催淫ガス、射精、性欲数値、おちんちん、シコシコ、オナニー、監獄島、看護師、調教、寸止め、絶頂" `
  --fix-map E:\DeepSeekHarness\whisper\scripts\fix_asr_errors.json

# 3) 翻译 + 合成双语
& $py E:\DeepSeekHarness\whisper\scripts\translate_zh.py `
  E:\DeepSeekHarness\whisper\out\01.ja.json --outdir E:\DeepSeekHarness\whisper\out --name 01
```

---

## 3. ASMR 关键参数(为什么这么设)

| 参数 | 值 | 原因 |
|---|---|---|
| `--vad --vad-threshold 0.5` | **开** | 实测开 VAD 明显更好:切口落在自然停顿处,不会拦腰切句;Silero 也没把耳语丢掉 |
| `condition_on_previous_text` | False | 切断幻觉循环传播 |
| `hallucination_silence_threshold` | 2.0 | 静音段幻觉(「ご視聴ありがとうございました」等)直接丢 |
| `no_speech_threshold` / `log_prob_threshold` / `compression_ratio_threshold` | 0.5 / -1.0 / 2.4 | 配合上面的清理规则 |
| `temperature` | 0→1.0 回退 | 标准做法 |
| `word_timestamps` | True | 后面按词精确重断句,比按字数等分时间准得多 |
| `--no-vad` | 备用 | 若音频是**纯耳语无停顿**,VAD 反而会漏;此时必须自己提供 ≤30s 的 `clip_timestamps`(库会直接报错) |
| `--initial-prompt` | 术语表 | 实测把 `サインガス/処制/エッチな精神/人気が悪い` 四个误识全部修好 |
| `--fix-map` | 纠错表 | 提示词修不掉的(`修正できなくなる→射精できなくなる`、`酸因ガス→催淫ガス`)确定性替换 |

后处理(`transcribe_asmr.py` 内置):幻觉黑名单 → 段内重复压缩 → 跨段死循环去重 → 按句末标点合并成
句子级 → 按词级时间戳均衡重断句(避免「よ」这种一字尾巴)。

### BatchedInferencePipeline 的两个硬约束(源码级)
- 关掉 VAD 时**必须**提供 `clip_timestamps`,否则直接 `RuntimeError`;
- 提供的每个 clip **超过 30 秒只会转前 30 秒**;而且 `sum(clips)` 略微大于音频总长会触发
  `assert seconds >= 0` 断言(浮点四舍五入就够踩中)。

---

## 4. 本机实测数据(素材 01_ようこそ地獄の監獄島へ,4分33秒)

| 方案 | 耗时 | 实时率 | 结果质量 |
|---|---|---|---|
| large-v3-turbo + VAD0.5 + 术语提示 + 纠错表 | **91 s** | **x0.33** | 23 行,句子级,可用 |
| large-v3-turbo + 30s 硬切块(关 VAD) | 81–94 s | x0.30 | 边界处丢整句、断词,明显更差 |
| kotoba-whisper-v2.0-faster(日语专用) | 崩 | — | **0xC0000005 访问冲突**,int8/float32/int8_float32 全崩,弃用 |

按 x0.33 估算:**全 7 轨(约 2 小时)→ 约 40 分钟**转录(12 核 CPU)。

值得注意的一手观察:**同一段音频换分块方式,识别结果会变**(例如「収監」变「習慣」、整句消失)。
所以 ASMR 别用固定 30 秒硬切,切口要落在停顿上。

---

## 5. 翻译环节

`translate_zh.py` 兼容任何 OpenAI 格式接口(`--base-url` / `--model` / `--api-key-env`),
按行号一一对应、断点续传(译文落 `*.zh.cache.json`),输出:

- `*.zh.srt` 纯中文
- `*.bilingual.srt` 中文行 + 日文行
- `*.bilingual.ass` 中文主字幕(58px 白字黑边)+ 日文小字(38px 灰),1080p 版式

没有 API key 也能做:把 23 行日文丢给 AI 逐行翻译,把结果写成 `{"行号": "译文"}` 的
`*.zh.cache.json`,再跑 `translate_zh.py`(它会跳过已缓存的行)。

**注意行号对齐**:让模型"合并被切断的半句"会导致整体错位一位——所以本流程先在 ASR 后处理阶段
把日文并成句子级,max-line 放到 40,减少半句断行。

---

## 6. 素材选择建议

同一套作品里有 `01_本編`(带音效)和 `02_SE無し`(无音效)。**优先用 SE無し 版做 ASR**:
耳舔/水声等音效会显著增加幻觉和误识。但要注意两版时间轴不同(带音效版更长),
**字幕要和实际观看的那一版同源**,或留出偏移量校正。

---

## 7. 整套批量结果(RJ01263979,01_本編 + 02_SE無し 各 7 轨)

| 名称 | 音频版本 | 时长 | 字幕条数 | 实时率 |
|---|---|---|---|---|
| 01_se / 01_seoff | 本編 / SE無し | 4:40 / 4:33 | 23 / 23 | x0.26 / x0.33 |
| 02_se / 02_seoff | 本編 / SE無し | 18:17 / 18:17 | 67 / 63 | x0.19 / x0.19 |
| 03_se / 03_seoff | 本編 / SE無し | 12:02 / 12:02 | 49 / 50 | x0.21 / x0.21 |
| 04_se / 04_seoff | 本編 / SE無し | 12:48 / 12:48 | 39 / 47 | x0.18 / x0.20 |
| 05_se / 05_seoff | 本編 / SE無し | 24:28 / 24:28 | 115 / 116 | x0.26 / x0.25 |
| 06_se / 06_seoff | 本編 / SE無し | 19:50 / 19:48 | 60 / 80 | x0.17 / x0.22 |
| 07_se / 07_seoff | 本編 / SE無し | 22:19 / 22:19 | 68 / 62 | x0.17 / x0.17 |

**合计 228.6 分钟音频 → 862 行字幕,转录总计约 73 分钟 CPU 时间**(12 核 int8)。

质量抽查方法:算"每秒识别字符数",正常区间约 1.5–2.9 字/秒(ASMR 语速慢、停顿多)。
无异常轨。注意带音效版普遍比 SE無し 版少 10–25% 文本(音效干扰所致),符合预期。

每套产物:`<name>.ja.srt`(日文)、`<name>.zh.srt`(中文)、`<name>.bilingual.srt`(对照)、
`<name>.bilingual.ass`(带样式)、`<name>.bilingual.mkv`(原音轨 + 双语软字幕,双击可播)。

### 批量时新踩的两个环境坑

1. **这环境实际是 Windows PowerShell 5.1**(`pwsh` 不在 PATH,嵌套调用会报"无法识别")。
   PS 5.1 读**无 BOM 的 UTF-8 `.ps1`** 会按 ANSI 解码 → 中文变乱码 → 语法错误。
   → 本目录的 `.ps1` 都已保存为 **UTF-8 with BOM**(`Set-Content -Encoding UTF8` 在 PS 5.1 下会写 BOM)。
2. **执行策略禁止未签名脚本**(`UnauthorizedAccess`),而且每次工具调用都是新进程,
   `Set-ExecutionPolicy -Scope Process` 不会保留 → 必须**在同一条命令里**设置再执行:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
& 'E:\DeepSeekHarness\whisper\batch_rj01263979.ps1'
```

### 翻译环节的做法(无需 API key)

让子代理**自己读 `.ja.srt`、自己写 `*.zh.cache.json`**,只回报条数;主流程只做
"条数校验 + 合成",这样大量日/中正文不经过主上下文。校验脚本会比对
`*.ja.json` 的段数与缓存键数,不一致就不用合成。

## 8. 磁盘占用与清理

| 目录 | 大小 | 说明 |
|---|---|---|
| `hf2` | 2.99 GB | 模型缓存,**留着**(删了要重下) |
| `audio` | 427 MB | 归一化后的 16k wav,删掉可由 ffmpeg 秒级重建 |
| `out` | 323 MB | 成品(14 套 MKV 占绝大部分) |
| `tmp` | ~0 | 临时目录 |

## 9. 文件清单

| 文件 | 作用 |
|---|---|
| `tools.cmd` | **双击入口**:主菜单(翻译 / 出字幕 / 悬浮层 / 校验) |
| `translate.cmd` | 双击入口:翻译字幕(弹文件框;无 key 时给三条出路) |
| `overlay.cmd` | 双击入口:启动置顶透明悬浮字幕层(先自检再启动) |
| `scripts\launcher.py` | 上面三个 .cmd 背后的交互逻辑(菜单、文件框、结果汇总) |
| `scripts\subtitle_overlay.py` | 悬浮层本体:置顶 + 全透明 + **鼠标穿透**,自带 ffplay 播放 |
| `字幕翻译-使用说明.md` | 面向使用的完整手册(含 PotPlayer 置顶/透明/不挡操作) |
| `batch_rj01263979.ps1` | 本系列整套驱动(14 轨,显式命名,自动跳过已完成) |
| `run_asmr_subtitles.ps1` | 通用批量驱动:ffmpeg 归一化 → 转录 → (可选)翻译,支持续跑 |
| `scripts\transcribe_asmr.py` | ASMR 调参转录 + 幻觉清理 + 句子级重断句 |
| `scripts\translate_zh.py` | 逐行翻译 + 双语 SRT/ASS 合成(只用标准库,任何 OpenAI 格式接口) |
| `scripts\prompt_*.txt` | 翻译提示词模板(日译中 / 英译中,可复制改成任意语言) |
| `scripts\fix_asr_errors.json` | 误识纠错表(可按系列累积) |
| `patches\sitecustomize.py` | 沙箱/受限环境兼容补丁(随仓库发布,必需) |
| `pylibs\`(不提交) | pip 装出来的依赖目录,克隆后自行 `pip install` |
| `out\` | 产物:`<name>.ja.srt/.ja.json/.ja.txt/.meta.json/.zh.srt/.bilingual.srt/.bilingual.ass/.bilingual.mkv` |

> - `.ps1` 必须以 UTF-8 with BOM 保存,并在执行前于同一进程设置 `-ExecutionPolicy Bypass`(见第 7 节)。
> - `.cmd` 必须是纯 ASCII、**不能带 BOM**(cmd.exe 会把 BOM 当成命令的一部分而报错)。
> - 中文文档/脚本用 UTF-8;控制台输出前先 `chcp 65001`(三个 .cmd 里已经做了)。

## 10. 部署到你自己的机器

1. 装 **Python 3.10+**(python.org 官方包,安装时勾 *Add python.exe to PATH*),或复用你已有的 Python。
2. 装识别依赖(只做一次):
   ```cmd
   set SUBTITLE_PY=C:\Python312\python.exe
   "%SUBTITLE_PY%" -m pip install faster-whisper
   ```
   国内下载模型建议设镜像:`set HF_ENDPOINT=https://hf-mirror.com`
3. **改路径**:`tools.cmd` / `translate.cmd` / `overlay.cmd` 与两个 `.ps1` 顶部都有一行
   `SUBTITLE_PY` 默认值,改成你的 `python.exe`;也可以只设环境变量,脚本优先用它:
   ```cmd
   setx SUBTITLE_PY "C:\Python312\python.exe"
   ```
4. 首次识别会自动下载模型(`large-v3-turbo` 约 1.5GB),缓存在 `hf2\`,之后可离线跑。
5. 运行:双击 `tools.cmd` → 选 1 翻译现成字幕,或选 2 走完整流程。

> 只翻译字幕(不跑识别)的话第 2 步可以跳过 —— `translate_zh.py` 只用标准库。

## 11. 发布到 GitHub 前请注意

`.gitignore` 已排除 `pylibs/ pkgs/ hf/ hf2/ audio/ out/ tmp/` 以及各类音视频与 `*.zh.cache.json`。
**这些不要提交**,两个原因:

- **体积**:模型缓存约 3GB、归一化 wav 427MB、成品 323MB,而代码本身不到 150KB;
- **内容**:`out/` 里是生成的字幕与封装 MKV,与原作音源同源,涉及**版权**;成人向内容也不适合
  放进 GitHub(不符合其可接受使用政策)。

仓库里应该只有代码、文档、示例配置:`scripts/`、`patches/`、`*.cmd`、`*.ps1`、`*.md`、`LICENSE`、`.gitignore`。

- `scripts/fix_asr_errors.json` 是作者自用的误识纠错表(**含成人向词汇**);不想保留就删掉,
  `fix_asr_errors.example.json` 是中性示例,格式一致。
- 文档里的 `F:\资源\ASMR\RJ01263979…`、`D:\potplayer\…` 只是本机示例路径。
- 上传前自查:
  ```cmd
  git status --short
  git ls-files | findstr /I "out/ audio/ hf2/ pylibs/ .wav .mkv .cache.json"
  ```
  第二条命令应当**没有输出**。

## 12. 许可

代码以 **MIT** 许可发布,见 [LICENSE](LICENSE)。第三方依赖遵循各自许可:faster-whisper(MIT)、
CTranslate2(MIT)、PyAV(BSD-3)、onnxruntime(MIT)、Whisper 模型权重(OpenAI,MIT)。
本仓库**不包含**任何模型权重与音视频素材。
