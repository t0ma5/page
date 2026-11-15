# Puts page (Python GUI for age) in this user's Send to menu.
# The shortcut target is page.exe in this script's folder.
# Explorer appends the selected file paths itself, so the shortcut has no
# Arguments. One shortcut covers encrypt and decrypt: .age files open in
# decrypt mode, anything else in encrypt mode.
# Run again after the folder moves. No admin.

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$gui = Join-Path $here "page.exe"
$icon = Join-Path $here "icon.ico"
if (-not (Test-Path -LiteralPath $gui)) {
    Write-Error "page.exe not found next to this script."
    exit 1
}
if (-not (Test-Path -LiteralPath $icon)) {
    Write-Error "icon.ico not found next to this script."
    exit 1
}

$sendTo = Join-Path $env:APPDATA "Microsoft\Windows\SendTo"
$linkPath = Join-Path $sendTo "page.lnk"
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($linkPath)
$link.TargetPath = $gui
$link.WorkingDirectory = $here
$link.Arguments = ""
$link.IconLocation = "$icon,0"
$link.Description = "Encrypt or decrypt with age (page)"
$link.Save()

Write-Output "Send to: $linkPath"
Write-Output "Target: $gui"
Write-Output "Icon: $icon"
Write-Output "Windows 11: right-click a file, Show more options, Send to, page."
Write-Output "After moving this folder, run this again."
