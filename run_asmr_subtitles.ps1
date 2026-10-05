# ASMR 批量出中日双语字幕
#
# 用法:
#   pwsh -File run_asmr_subtitles.ps1 -InputDir 'F:\资源\ASMR\RJ01263979...\02_SE無し\MP３'
#   pwsh -File run_asmr_subtitles.ps1 -InputDir '...\MP３' -Translate        # 需要 DEEPSEEK_API_KEY
#   pwsh -File run_asmr_subtitles.ps1 -InputDir '...\MP３' -Model 'large-v3' -VadThreshold 0.4
#
# 每一步都能续跑:已存在的 wav / ja.srt / 译文缓存会被跳过。
#
# 关键设计(踩过的坑):
#   1. 开 VAD(--vad-threshold 0.5)在这批素材上明显优于 30 秒硬切块:
#      切口落在自然停顿处,不会拦腰切断句子;Silero 也没有把耳语当静音丢掉。
#   2. condition_on_previous_text=False + hallucination_silence_threshold 抑制幻觉循环。
#   3. 关闭 VAD 时必须自己提供 <=30s 的 clip_timestamps,否则库会直接报错。
#   4. 翻译是独立环节:Whisper 的 task=translate 只能出英文。
#   5. Python 在沙箱里必须带 sitecustomize 补丁(pylibs\sitecustomize.py),否则 pip/tempfile 全废。
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$InputDir,
  [string]$OutDir = "$PSScriptRoot\out",
  [string]$WorkDir = "$PSScriptRoot\audio",
  [string]$Model = "large-v3-turbo",
  [string]$ComputeType = "int8",
  [double]$VadThreshold = 0.5,
  [int]$BatchSize = 8,
  [int]$MaxLine = 40,
  [switch]$NoVad,
  [switch]$Translate,
  [switch]$Overwrite
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

# ---- 环境和依赖 ----
# 用自己的 Python:设 SUBTITLE_PY 环境变量,或改下面这行默认值
$Py = if ($env:SUBTITLE_PY) { $env:SUBTITLE_PY }
      else { "C:\Users\chr\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" }
if (-not (Test-Path $Py)) { throw "找不到 Python: $Py(设 SUBTITLE_PY 或改本脚本默认值)" }
$env:PYTHONPATH = (Join-Path $PSScriptRoot 'pylibs') + ';' + (Join-Path $PSScriptRoot 'patches')
$env:PYTHONIOENCODING = 'utf-8'
$env:TEMP = Join-Path $PSScriptRoot 'tmp'
$env:TMP = $env:TEMP
$env:HF_HOME = Join-Path $PSScriptRoot 'hf2'
$env:HF_ENDPOINT = if ($env:HF_ENDPOINT) { $env:HF_ENDPOINT } else { 'https://hf-mirror.com' }
$env:HF_HUB_DISABLE_XET = '1'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
New-Item -ItemType Directory -Force $OutDir, $WorkDir, $env:TEMP | Out-Null

$Transcribe = Join-Path $PSScriptRoot 'scripts\transcribe_asmr.py'
$TranslatePy = Join-Path $PSScriptRoot 'scripts\translate_zh.py'

$files = Get-ChildItem -LiteralPath $InputDir -File -Include *.mp3, *.wav, *.m4a, *.flac, *.opus
if (-not $files) { throw "目录里没有音频: $InputDir" }
"找到 $($files.Count) 个音频文件"

$summary = @()
foreach ($f in $files) {
  $name = [IO.Path]::GetFileNameWithoutExtension($f.Name)
  $wav = Join-Path $WorkDir "$name.wav"
  $srt = Join-Path $OutDir "$name.ja.srt"
  "`n================ $($f.Name)"

  # 1) 归一化:16k 单声道 + 高通 + loudnorm(ASMR 音量小,归一化对识别率提升明显)
  if ((-not (Test-Path $wav)) -or $Overwrite) {
    "  [1/3] ffmpeg 归一化 ..."
    & ffmpeg -hide_banner -loglevel error -y -i $f.FullName -vn -ac 1 -ar 16000 `
      -af "highpass=f=60,loudnorm=I=-18:TP=-1.5:LRA=11" -c:a pcm_s16le $wav
    if ($LASTEXITCODE -ne 0) { "  [x] ffmpeg 失败,跳过"; continue }
  } else { "  [1/3] 已有 wav,跳过" }

  # 2) 转录
  if ((-not (Test-Path $srt)) -or $Overwrite) {
    "  [2/3] 转录($Model / $ComputeType / VAD=$(-not $NoVad)) ..."
    $a = @($Transcribe, $wav, '--outdir', $OutDir, '--name', $name,
      '--model', $Model, '--compute-type', $ComputeType,
      '--batch-size', $BatchSize, '--max-line', $MaxLine)
    if ($NoVad) { $a += '--no-batched' } else { $a += @('--vad', '--vad-threshold', $VadThreshold) }
    & $Py @a
    if ($LASTEXITCODE -ne 0) { "  [x] 转录失败,跳过"; continue }
  } else { "  [2/3] 已有日文字幕,跳过" }

  # 3) 翻译(可选)
  $bilingual = Join-Path $OutDir "$name.bilingual.ass"
  if ($Translate) {
    if (-not (Test-Path $bilingual) -or $Overwrite) {
      "  [3/3] 翻译成中文 ..."
      & $Py $TranslatePy (Join-Path $OutDir "$name.ja.json") --outdir $OutDir --name $name
      if ($LASTEXITCODE -ne 0) { "  [!] 翻译未完成(可重跑续译)" }
    } else { "  [3/3] 已有双语字幕,跳过" }
  } else { "  [3/3] 未指定 -Translate,只出日文字幕" }

  $summary += [pscustomobject]@{
    文件 = $f.Name
    日文 = Test-Path $srt
    双语 = Test-Path $bilingual
  }
}

"`n================ 汇总"
$summary | Format-Table -AutoSize | Out-String | Write-Host
"产物目录: $OutDir"
