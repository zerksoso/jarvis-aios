@echo off
cd /d "%~dp0"
"%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:winexe /optimize+ /win32icon:..\assets\jarvis.ico /reference:System.Windows.Forms.dll /out:..\Jarvis.exe JarvisLauncher.cs
