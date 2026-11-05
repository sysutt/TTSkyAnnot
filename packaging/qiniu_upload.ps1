# 上传到七牛（华南 z2）。凭据从 packaging\deploy.local.ps1 或环境变量读取，不写进仓库。
param(
    [Parameter(Mandatory=$true)][string]$LocalFile,
    [Parameter(Mandatory=$true)][string]$Key,
    [string]$Bucket = $env:QiniuBucket,
    [string]$AccessKey = $env:QiniuAK,
    [string]$SecretKey = $env:QiniuSK,
    [string]$UploadHost = $env:QiniuUploadHost,
    [string]$Domain = $env:QiniuDomain
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http     # Windows PowerShell 5.1 不会自动加载它
if (-not $AccessKey -or -not $SecretKey) { throw "no Qiniu credentials (set QiniuAK / QiniuSK in deploy.local.ps1)" }
if (-not $Bucket) { $Bucket = 'jiaowax' }
if (-not $UploadHost) { $UploadHost = 'https://up-z2.qbox.me' }
if (-not (Test-Path -LiteralPath $LocalFile)) { throw "file not found: $LocalFile" }

function ConvertTo-Base64Url([byte[]]$bytes) {
    [Convert]::ToBase64String($bytes).Replace('+','-').Replace('/','_')
}

# putPolicy: 只允许写这一个 key，2 小时后过期
$deadline = [int][double]::Parse((Get-Date -UFormat %s)) + 7200
$policy = '{"scope":"' + $Bucket + ':' + $Key + '","deadline":' + $deadline + ',"insertOnly":0}'
$policyB64 = ConvertTo-Base64Url ([Text.Encoding]::UTF8.GetBytes($policy))
$hmac = [System.Security.Cryptography.HMACSHA1]::new([Text.Encoding]::UTF8.GetBytes($SecretKey))
$sign = ConvertTo-Base64Url ($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($policyB64)))
$token = $AccessKey + ':' + $sign + ':' + $policyB64

$size = (Get-Item -LiteralPath $LocalFile).Length
Write-Output ("uploading {0} ({1:N1} MB) -> {2}/{3}" -f (Split-Path $LocalFile -Leaf), ($size/1MB), $Bucket, $Key)

$sw = [Diagnostics.Stopwatch]::StartNew()
$client = [System.Net.Http.HttpClient]::new()
$client.Timeout = [TimeSpan]::FromHours(1)
$form = [System.Net.Http.MultipartFormDataContent]::new()
$tokContent = [System.Net.Http.StringContent]::new($token)
$form.Add($tokContent, 'token')
$keyContent = [System.Net.Http.StringContent]::new($Key)
$form.Add($keyContent, 'key')
$stream = [IO.File]::OpenRead($LocalFile)
$fileContent = [System.Net.Http.StreamContent]::new($stream)
$fileContent.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('application/octet-stream')
$form.Add($fileContent, 'file', (Split-Path $LocalFile -Leaf))
$resp = $client.PostAsync($UploadHost, $form).GetAwaiter().GetResult()
$body = $resp.Content.ReadAsStringAsync().GetAwaiter().GetResult()
$stream.Dispose(); $form.Dispose(); $client.Dispose()
$sw.Stop()
if (-not $resp.IsSuccessStatusCode) { throw ("upload failed: HTTP {0} {1}" -f [int]$resp.StatusCode, $body) }
Write-Output ("uploaded in {0:N0}s: {1}" -f $sw.Elapsed.TotalSeconds, $body.Substring(0, [Math]::Min(120, $body.Length)))

if ($Domain) {
    $url = ($Domain.TrimEnd('/') + '/' + $Key)
    try {
        $head = Invoke-WebRequest -Uri $url -Method Head -TimeoutSec 30 -UseBasicParsing
        Write-Output ("live: {0} {1} ({2:N1} MB)" -f [int]$head.StatusCode, $url, ([double]$head.Headers['Content-Length']/1MB))
    } catch {
        Write-Output ("WARNING: uploaded but the CDN URL did not answer: {0} -> {1}" -f $url, $_.Exception.Message)
    }
}
