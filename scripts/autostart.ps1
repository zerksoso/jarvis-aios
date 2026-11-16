# Active ou désactive le démarrage automatique de Jarvis à l'ouverture de session (tâche planifiée utilisateur, sans admin).
# Usage : autostart.ps1 -Activer   |   autostart.ps1 -Desactiver
param([switch]$Activer, [switch]$Desactiver)
$Root = Split-Path -Parent $PSScriptRoot
$Nom = 'Jarvis AIOS - demarrage'
if ($Desactiver) {
  Unregister-ScheduledTask -TaskName $Nom -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host 'Démarrage automatique désactivé.'; exit 0
}
if ($Activer) {
  $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Root\scripts\lancer.ps1`" -SansNavigateur" -WorkingDirectory $Root
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 0)
  Register-ScheduledTask -TaskName $Nom -Action $action -Trigger $trigger -Settings $settings -Description 'Lance le serveur et l overlay Jarvis AIOS a la connexion.' -Force | Out-Null
  Write-Host 'Démarrage automatique activé (à chaque ouverture de session).'; exit 0
}
Write-Host 'Préciser -Activer ou -Desactiver.'
