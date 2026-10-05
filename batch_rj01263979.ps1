# RJ01263979 整套批量:01_本編(带音效)+ 02_SE無し(无音效)
#
# 命名规则:01_se / 01_seoff(01_seoff 已完成,自动跳过)
# 参数为本机实测最佳组合:large-v3-turbo + VAD0.5 + 术语提示 + 纠错表 + 句子级断句
param(
  # 你的音声根目录(下面应当有 "01_本編\MP３" 与 "02_SE無し\MP３" 这种结构;
  # 只想跑单个目录时,用 run_asmr_subtitles.ps1 更合适)
  [string]$Root = 'F:\资源\ASMR\RJ01263979【両耳吐息責め】痴女ナースに捉えられ監獄島で快楽実験!～媚薬吐息に脳内が侵される～'
)
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

# 用自己的 Python:设 SUBTITLE_PY 环境变量,或改下面这行默认值
$Base = $PSScriptRoot
$Py = if ($env:SUBTITLE_PY) { $env:SUBTITLE_PY }
      else { "C:\Users\chr\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" }
if (-not (Test-Path $Py)) { throw "找不到 Python: $Py(设 SUBTITLE_PY 或改本脚本默认值)" }
$env:PYTHONPATH = (Join-Path $Base 'pylibs') + ';' + (Join-Path $Base 'patches')
$env:PYTHONIOENCODING = 'utf-8'
$env:TEMP = Join-Path $Base 'tmp'; $env:TMP = $env:TEMP
$env:HF_HOME = Join-Path $Base 'hf2'
$env:HF_ENDPOINT = 'https://hf-mirror.com'
$env:HF_HUB_DISABLE_XET = '1'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'

$Script = Join-Path $Base 'scripts\transcribe_asmr.py'
$FixMap = Join-Path $Base 'scripts\fix_asr_errors.json'
if (-not (Test-Path $FixMap)) { $FixMap = Join-Path $Base 'scripts\fix_asr_errors.example.json' }
$OutDir = Join-Path $Base 'out'
$AudioDir = Join-Path $Base 'audio'
New-Item -ItemType Directory -Force $OutDir, $AudioDir | Out-Null

# 术语提示词:按你的素材改(这里是护理师/监狱岛题材的常用词)
$Prompt = "媚薬ガス、催淫ガス、射精、性欲数値、異常性欲数値者、おちんちん、シコシコ、オナニー、監獄島、看守、看護師、ミニスカナース、調教、寸止め、絶頂、ご褒美、経過観察"

if (-not (Test-Path -LiteralPath $Root)) { throw "找不到素材目录: $Root(用 -Root 参数指定)" }
$sets = @(
  @{ dir = Join-Path $Root '01_本編\MP３'; suffix = 'se' },
  @{ dir = Join-Path $Root '02_SE無し\MP３'; suffix = 'seoff' }
)

$plan = @()
foreach ($set in $sets) {
  $files = Get-ChildItem -LiteralPath $set.dir -File -Filter *.mp3 | Sort-Object Name
  foreach ($f in $files) {
    if ($f.Name -match '^(\d+)') { $idx = [int]$Matches[1] } else { $idx = 0 }
    $plan += [pscustomobject]@{ Src = $f.FullName; Name = ('{0:D2}_{1}' -f $idx, $set.suffix); Suffix = $set.suffix }
  }
}
"共 $($plan.Count) 个任务"

foreach ($p in $plan) {
  $wav = Join-Path $AudioDir "$($p.Name).wav"
  $srt = Join-Path $OutDir "$($p.Name).ja.srt"
  "`n================ $($p.Name)  <- $([IO.Path]::GetFileName($p.Src))"

  if (Test-Path -LiteralPath $srt) { "  已有日文字幕,跳过"; continue }

  if (-not (Test-Path -LiteralPath $wav)) {
    "  [1/2] ffmpeg 归一化 ..."
    & ffmpeg -hide_banner -loglevel error -y -i $p.Src -vn -ac 1 -ar 16000 `
      -af "highpass=f=60,loudnorm=I=-18:TP=-1.5:LRA=11" -c:a pcm_s16le $wav
    if ($LASTEXITCODE -ne 0) { "  [x] ffmpeg 失败,跳过"; continue }
  } else { "  [1/2] 已有 wav" }

  $sw = [Diagnostics.Stopwatch]::StartNew()
  & $Py $Script $wav --outdir $OutDir --name $p.Name `
    --model large-v3-turbo --compute-type int8 `
    --vad --vad-threshold 0.5 --batch-size 8 --max-line 40 `
    --initial-prompt $Prompt --fix-map $FixMap 2>&1 | Select-Object -Last 5
  $sw.Stop()
  "  [2/2] 用时 $([math]::Round($sw.Elapsed.TotalMinutes,1)) 分钟  exit=$LASTEXITCODE"
}

"`n================ 全部完成 ================"
Get-ChildItem $OutDir -Filter '*.ja.srt' | Select-Object Name, @{n='KB';e={[math]::Round($_.Length/1KB,1)}} | Format-Table -AutoSize | Out-String
