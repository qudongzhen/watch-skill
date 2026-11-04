<#
.SYNOPSIS
  OCR one image with the Windows built-in engine (WinRT Windows.Media.Ocr) and print text.

.DESCRIPTION
  The sandbox cannot install rapidocr (PyPI handshake times out), but Windows 10/11 ships an
  OCR engine that knows zh-Hans-CN and en-US - enough for burned-in subtitles.
  Called by pipeline/hardsub.py as an external engine:
      powershell -NoProfile -ExecutionPolicy Bypass -File ocr_win.ps1 -Path frame.png [-Lang zh-Hans-CN]
  Prints one line per recognized line of text; non-zero exit on failure.

.NOTES
  Two traps, both hit for real:
  1. StorageFile::GetFileFromPathAsync fails in a non-packaged process ("One or more errors
     occurred"). Reading the bytes ourselves and pushing them into an InMemoryRandomAccessStream
     works - that is what this script does.
  2. powershell.exe reads .ps1 as ANSI unless the file has a UTF-8 BOM, and its output is
     space-separated per CJK character; the caller (hardsub.py) re-joins them.
  Keep this file ASCII-only.
#>
param(
  [Parameter(Mandatory = $true)][string]$Path,
  [string]$Lang = "zh-Hans-CN"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if (-not (Test-Path -LiteralPath $Path)) {
  [Console]::Error.WriteLine("image not found: $Path")
  exit 2
}

try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null

  $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
  })[0]

  function Await($op, [Type]$resultType) {
    $task = $asTaskGeneric.MakeGenericMethod($resultType).Invoke($null, @($op))
    $task.Wait(-1) | Out-Null
    $task.Result
  }

  $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
  $null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
  $null = [Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage, ContentType = WindowsRuntime]
  $null = [Windows.Storage.Streams.DataWriter, Windows.Storage, ContentType = WindowsRuntime]
  $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]

  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new($Lang))
  if ($null -eq $engine) {
    [Console]::Error.WriteLine("no OCR language pack for $Lang")
    exit 3
  }

  $bytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $Path).Path)
  $stream = [Windows.Storage.Streams.InMemoryRandomAccessStream]::new()
  $writer = [Windows.Storage.Streams.DataWriter]::new($stream)
  $writer.WriteBytes($bytes)
  $null = Await ($writer.StoreAsync()) ([uint32])
  $null = Await ($writer.FlushAsync()) ([bool])
  $stream.Seek(0)

  $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
  $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
  $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

  foreach ($line in $result.Lines) { $line.Text }
  exit 0
}
catch {
  [Console]::Error.WriteLine("ocr failed: " + $_.Exception.Message)
  exit 1
}
