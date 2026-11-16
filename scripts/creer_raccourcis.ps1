# Crée les raccourcis « Jarvis AIOS » sur le Bureau et dans le menu Démarrer.
$Root = Split-Path -Parent $PSScriptRoot
$Icone = Join-Path $Root 'assets\jarvis.ico'
$Shell = New-Object -ComObject WScript.Shell
$Cibles = @([Environment]::GetFolderPath('Desktop'), (Join-Path ([Environment]::GetFolderPath('Programs')) ''))
foreach ($dossier in $Cibles) {
  $lnk = $Shell.CreateShortcut((Join-Path $dossier 'Jarvis AIOS.lnk'))
  $lnk.TargetPath = Join-Path $Root 'Lancer_Jarvis.bat'
  $lnk.WorkingDirectory = $Root
  $lnk.WindowStyle = 7
  $lnk.Description = 'Lancer Jarvis AIOS (serveur, overlay, tableau de bord)'
  if (Test-Path $Icone) { $lnk.IconLocation = $Icone }
  $lnk.Save()
  Write-Host "Raccourci créé : $dossier\Jarvis AIOS.lnk"
}
