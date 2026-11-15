# Removes the page Send to shortcut.

$linkPath = Join-Path $env:APPDATA "Microsoft\Windows\SendTo\page.lnk"
if (Test-Path -LiteralPath $linkPath) {
    Remove-Item -LiteralPath $linkPath -Force
    Write-Output "page removed from Send to."
} else {
    Write-Output "page was not in Send to."
}
