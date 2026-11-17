param(
    [string]$RefreshDir,
    [string]$RefreshBranch,
    [string]$RefreshFile
)

# Claude Code reads stdout from a pipe; the default 'Host' rendering would strip the OSC 8 link escapes there.
$PSStyle.OutputRendering = 'Ansi'

# Background mode: ask glab for the branch's MR and store it; the status line itself never waits on the network.
if ($RefreshFile) {
    Set-Location $RefreshDir
    $mr = glab mr list --source-branch $RefreshBranch -F json 2>$null | ConvertFrom-Json | Select-Object -First 1
    $cached = if ($mr) { @{ iid = $mr.iid; url = $mr.web_url } } else { @{} }
    $cached | ConvertTo-Json -Compress | Set-Content -LiteralPath $RefreshFile
    return
}

$ESC = [char]27
function Get-Link([string]$Url, [string]$Text, [int]$Color) { "$ESC]8;;$Url$ESC\$ESC[${Color}m$Text$ESC[0m$ESC]8;;$ESC\" }
function Get-Usage([string]$Label, $Percentage) {
    $value = [math]::Round($Percentage)
    $color = if ($value -ge 80) { 31 } elseif ($value -ge 50) { 33 } else { 32 }
    "${Label}:$ESC[${color}m$value%$ESC[0m"
}

$data = $input | Out-String | ConvertFrom-Json
$cwd = $data.workspace.current_dir
$parts = [System.Collections.Generic.List[string]]::new()

# Transcripts don't record rate-limit state, so leave it for the worklog Stop hook (repeat-o-scope-worklog).
# A window at 100% means the turns went to extra usage.
$worklogState = Join-Path $HOME '.claude\hooks\worklog-state'
if ($data.rate_limits -and $data.session_id -and (Test-Path -LiteralPath $worklogState)) {
    $data.rate_limits | ConvertTo-Json -Compress -Depth 5 |
        Set-Content -LiteralPath (Join-Path $worklogState "$($data.session_id).rate-limits.json")
}

$root = git -C $cwd --no-optional-locks rev-parse --show-toplevel 2>$null
$branch = git -C $cwd --no-optional-locks branch --show-current 2>$null

# Main checkout's folder name, plus the worktree folder when they differ. The common dir is the main checkout's .git.
if ($root) {
    $name = Split-Path -Leaf $root
    $common = git -C $cwd --no-optional-locks rev-parse --path-format=absolute --git-common-dir 2>$null
    if ($common) {
        $repo = Split-Path -Leaf (Split-Path -Parent $common)
        $name = if ($repo -eq $name) { $repo } else { "$repo/$name" }
    }
}

# Issue from a leading number in the branch name. The origin remote holds the full group path; workspace.repo may hold only the top-level group.
$origin = git -C $cwd --no-optional-locks remote get-url origin 2>$null
$repoUrl = if ($origin -match '^git@([^:]+):(.+?)(\.git)?/?$' -or $origin -match '^https?://(?:[^@/]+@)?([^/]+)/(.+?)(\.git)?/?$') {
    "https://$($Matches[1])/$($Matches[2])"
} elseif ($data.workspace.repo) {
    "https://$($data.workspace.repo.host)/$($data.workspace.repo.owner)/$($data.workspace.repo.name)"
}
if ($branch -match '^(\d+)-' -and $repoUrl) {
    $parts.Add((Get-Link "$repoUrl/-/issues/$($Matches[1])" "#$($Matches[1])" 33))
}

# MR: the JSON's own pr field first, else a per-branch cache refreshed in the background every 10 minutes
if ($data.pr.number) {
    $parts.Add((Get-Link $data.pr.url "!$($data.pr.number)" 35))
} elseif ($branch) {
    $cacheFile = Join-Path $HOME ".claude\statusline-mr-$($branch -replace '[^\w.-]', '_').json"
    $stale = -not (Test-Path -LiteralPath $cacheFile) -or ((Get-Date) - (Get-Item -LiteralPath $cacheFile).LastWriteTime).TotalMinutes -gt 10
    if ($stale) {
        # Touch first so concurrent refreshes do not pile up
        if (-not (Test-Path -LiteralPath $cacheFile)) { Set-Content -LiteralPath $cacheFile '{}' } else { (Get-Item -LiteralPath $cacheFile).LastWriteTime = Get-Date }
        Start-Process pwsh -WindowStyle Hidden -ArgumentList '-NoProfile', '-File', $PSCommandPath, '-RefreshDir', $root, '-RefreshBranch', $branch, '-RefreshFile', $cacheFile
    }
    $mr = Get-Content -LiteralPath $cacheFile -Raw | ConvertFrom-Json
    if ($mr.iid) { $parts.Add((Get-Link $mr.url "!$($mr.iid)" 35)) }
}

# Context and 5-hour limit
if ($null -ne $data.context_window.used_percentage) { $parts.Add((Get-Usage 'C' $data.context_window.used_percentage)) }
$fiveHour = $data.rate_limits.five_hour
if ($null -ne $fiveHour.used_percentage) {
    $usage = Get-Usage 'H' $fiveHour.used_percentage
    # Time to reset only once the limit is red; resets_at is Unix epoch seconds
    if ([math]::Round($fiveHour.used_percentage) -ge 80 -and $fiveHour.resets_at) {
        $left = [DateTimeOffset]::FromUnixTimeSeconds([long]$fiveHour.resets_at) - [DateTimeOffset]::UtcNow
        if ($left -gt [TimeSpan]::Zero) {
            $minutes = [math]::Ceiling($left.TotalMinutes)
            $text = if ($minutes -lt 100) { "${minutes}m" } else { "$([math]::Ceiling($minutes / 60))h" }
            $usage += " $ESC[31m$text$ESC[0m"
        }
    }
    $parts.Add($usage)
}

# Worktree goes last and is the only part cut, and only when a known terminal width demands it. The documented JSON has no width, so $env:COLUMNS is the only source.
if ($name) {
    $width = [int]$env:COLUMNS
    if ($width -gt 0) {
        $restLength = ($parts -join ' ') -replace "$ESC\]8;;[^$ESC]*$ESC\\" -replace "$ESC\[[0-9;]*m" | ForEach-Object Length
        $room = $width - $restLength - 1
        if ($name.Length -gt $room) { $name = $name.Substring(0, [math]::Max($room - 1, 0)) + '…' }
    }
    $parts.Add("$ESC[36m$name$ESC[0m")
}

$parts -join ' '
