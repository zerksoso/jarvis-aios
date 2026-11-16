using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("Jarvis AIOS Launcher")]
[assembly: AssemblyDescription("Lanceur Windows sans console pour Jarvis AIOS")]
[assembly: AssemblyProduct("Jarvis AIOS")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]
[assembly: ComVisible(false)]

namespace Jarvis.Launcher
{
    /// <summary>
    /// Point d'entree GUI Windows (sans console) du lanceur local Jarvis AIOS.
    /// Orchestre l'arret (--arreter), le demarrage discret (--silencieux),
    /// l'application des mises a jour legeres (--appliquer-maj), l'ouverture directe
    /// du tableau de bord si le serveur repond deja sur le port 5230, ou le lancement
    /// complet via scripts\lancer.ps1.
    /// </summary>
    public static class JarvisLauncher
    {
        /// <summary>
        /// Nom du mutex local garantissant qu'une seule instance du lanceur s'execute a la fois.
        /// </summary>
        private const string MutexName = @"Local\JarvisAIOSLauncher";

        /// <summary>
        /// URL de verification de l'etat de sante du backend local.
        /// </summary>
        private const string HealthUrl = "http://127.0.0.1:5230/api/sante";

        /// <summary>
        /// URL principale du tableau de bord ouverte dans le navigateur par defaut.
        /// </summary>
        private const string AppUrl = "http://127.0.0.1:5230/";

        /// <summary>
        /// Delai maximal d'attente (en millisecondes) d'une sonde HTTP de sante.
        /// </summary>
        private const int HealthTimeoutMs = 2000;

        /// <summary>
        /// Delai maximal d'attente (en millisecondes) pour le script d'arret.
        /// </summary>
        private const int StopTimeoutMs = 30000;

        /// <summary>
        /// Delai maximal d'attente (en millisecondes) pour le script de demarrage.
        /// </summary>
        private const int StartTimeoutMs = 90000;

        /// <summary>
        /// Delai maximal d'attente (en millisecondes) de l'arret du serveur lors d'une mise a jour.
        /// </summary>
        private const int UpdateStopWaitMs = 60000;

        /// <summary>
        /// Delai maximal d'attente (en millisecondes) de la sonde sante apres mise a jour.
        /// </summary>
        private const int UpdateHealthWaitMs = 90000;

        /// <summary>
        /// Delai d'attente (en millisecondes) du mutex lorsqu'une mise a jour est declenchee.
        /// </summary>
        private const int UpdateMutexWaitMs = 10000;

        /// <summary>
        /// Dossiers et fichiers principaux remplaces lors d'une mise a jour legere.
        /// </summary>
        private static readonly string[] CiblesMiseAJour = new string[]
        {
            "backend",
            "overlay",
            "frontend",
            "scripts",
            "templates",
            "VERSION",
            "RUNTIME_VERSION"
        };

        /// <summary>
        /// Point d'entree principal du lanceur Windows Jarvis AIOS.
        /// </summary>
        /// <param name="args">Arguments de ligne de commande (--arreter, --silencieux, --appliquer-maj).</param>
        /// <returns>Code de sortie du processus (0 en cas de succes, non-zero en cas d'erreur).</returns>
        /// <remarks>
        /// Effets de bord : acquiert un mutex nomme, effectue des requetes HTTP locales,
        /// demarre PowerShell ou le navigateur par defaut, peut echanger les dossiers lors d'une
        /// mise a jour et affiche une MessageBox francaise en cas d'erreur.
        /// </remarks>
        [STAThread]
        public static int Main(string[] args)
        {
            try
            {
                bool createdNew;
                using (Mutex mutex = new Mutex(true, MutexName, out createdNew))
                {
                    bool hasHandle = createdNew;
                    if (!hasHandle)
                    {
                        if (!ContientArgument(args, "--appliquer-maj"))
                        {
                            return 0;
                        }

                        try
                        {
                            hasHandle = mutex.WaitOne(UpdateMutexWaitMs);
                        }
                        catch (AbandonedMutexException)
                        {
                            hasHandle = true;
                        }

                        if (!hasHandle)
                        {
                            return 1;
                        }
                    }

                    try
                    {
                        return Executer(args);
                    }
                    finally
                    {
                        if (hasHandle)
                        {
                            mutex.ReleaseMutex();
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    ex.Message,
                    "Jarvis AIOS",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
                return 1;
            }
        }

        /// <summary>
        /// Execute le flux principal du lanceur apres acquisition du mutex d'instance unique.
        /// </summary>
        /// <param name="args">Arguments de ligne de commande recus par Main.</param>
        /// <returns>Code de retour du lanceur (0 si nominal, code d'erreur sinon).</returns>
        /// <remarks>
        /// Effets de bord : applique une mise a jour (--appliquer-maj), lance scripts\arreter.ps1
        /// (--arreter), ouvre le navigateur sur 127.0.0.1:5230 (sauf --silencieux) ou execute
        /// scripts\lancer.ps1.
        /// </remarks>
        private static int Executer(string[] args)
        {
            string rootDir = ObtenirDossierRacine();
            bool silencieux = ContientArgument(args, "--silencieux");

            if (ContientArgument(args, "--appliquer-maj"))
            {
                return AppliquerMiseAJour(rootDir, silencieux);
            }

            if (ContientArgument(args, "--arreter"))
            {
                string scriptArreter = Path.Combine(rootDir, @"scripts\arreter.ps1");
                LancerPowerShell(scriptArreter, rootDir, StopTimeoutMs, null);
                return 0;
            }

            if (EstServeurActif())
            {
                if (!silencieux)
                {
                    OuvrirNavigateur();
                }
                return 0;
            }

            string scriptLancer = Path.Combine(rootDir, @"scripts\lancer.ps1");
            if (!File.Exists(scriptLancer))
            {
                MessageBox.Show(
                    "Le script scripts\\lancer.ps1 est introuvable.\r\n\r\nL'executable Jarvis.exe doit rester dans le dossier racine de Jarvis.",
                    "Jarvis AIOS",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
                return 1;
            }

            string extraArgs = silencieux ? "-SansNavigateur" : null;
            int exitCode = LancerPowerShell(scriptLancer, rootDir, StartTimeoutMs, extraArgs);
            if (exitCode != 0)
            {
                string logsDir = ObtenirDossierLogs(rootDir);
                MessageBox.Show(
                    "Jarvis n'a pas pu demarrer. Journaux : " + logsDir,
                    "Jarvis AIOS",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
                return exitCode;
            }

            return 0;
        }

        /// <summary>
        /// Applique une mise a jour legere preparee dans _maj_staging avec sauvegarde dans _ancien
        /// et restauration automatique si /api/sante ne repond pas dans les 90 secondes.
        /// </summary>
        /// <param name="rootDir">Dossier d'installation (APP_DIR) contenant Jarvis.exe.</param>
        /// <param name="silencieux">True pour relancer sans ouvrir le navigateur.</param>
        /// <returns>0 si la mise a jour et le redemarrage reussissent, 1 sinon.</returns>
        /// <remarks>
        /// Effets de bord : attend l'arret du serveur, arrete l'overlay, deplace des dossiers
        /// vers _ancien\ puis depuis _maj_staging\, relance Jarvis et journalise dans maj.log.
        /// </remarks>
        private static int AppliquerMiseAJour(string rootDir, bool silencieux)
        {
            string logsDir = ObtenirDossierLogs(rootDir);
            string majLogPath = Path.Combine(logsDir, "maj.log");
            string stagingDir = Path.Combine(rootDir, "_maj_staging");
            string ancienDir = Path.Combine(rootDir, "_ancien");

            JournaliserMaj(majLogPath, "Demarrage de l'application de la mise a jour (--appliquer-maj).");

            if (!Directory.Exists(stagingDir))
            {
                JournaliserMaj(majLogPath, "Erreur : dossier _maj_staging introuvable.");
                MessageBox.Show(
                    "Dossier de mise a jour _maj_staging introuvable.\r\nJournal : " + majLogPath,
                    "Jarvis AIOS",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
                return 1;
            }

            JournaliserMaj(majLogPath, "Attente de l'arret du serveur local (max 60 s)...");
            if (!AttendreArretServeur(UpdateStopWaitMs))
            {
                JournaliserMaj(majLogPath, "Erreur : le serveur ne s'est pas arrete dans le delai de 60 s.");
                MessageBox.Show(
                    "Le serveur Jarvis ne s'est pas arrete a temps pour appliquer la mise a jour.\r\nJournal : " + majLogPath,
                    "Jarvis AIOS",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
                return 1;
            }

            string scriptArreter = Path.Combine(rootDir, @"scripts\arreter.ps1");
            if (File.Exists(scriptArreter))
            {
                LancerPowerShell(scriptArreter, rootDir, StopTimeoutMs, null);
            }
            Thread.Sleep(1500);

            List<string> cibles = CollecterCiblesMiseAJour(stagingDir);
            List<string> sauvegardes = new List<string>();
            List<string> deployes = new List<string>();

            try
            {
                SupprimerCheminRecursif(ancienDir);
                Directory.CreateDirectory(ancienDir);

                JournaliserMaj(majLogPath, "Sauvegarde de la version actuelle vers _ancien\\...");
                for (int i = 0; i < cibles.Count; i++)
                {
                    string nom = cibles[i];
                    string src = Path.Combine(rootDir, nom);
                    if (Directory.Exists(src) || File.Exists(src))
                    {
                        string dst = Path.Combine(ancienDir, nom);
                        DeplacerCheminAvecReessai(src, dst);
                        sauvegardes.Add(nom);
                    }
                }

                JournaliserMaj(majLogPath, "Deploiement des fichiers depuis _maj_staging\\...");
                for (int i = 0; i < cibles.Count; i++)
                {
                    string nom = cibles[i];
                    string srcStage = Path.Combine(stagingDir, nom);
                    if (Directory.Exists(srcStage) || File.Exists(srcStage))
                    {
                        string dstRoot = Path.Combine(rootDir, nom);
                        DeplacerCheminAvecReessai(srcStage, dstRoot);
                        deployes.Add(nom);
                    }
                }
            }
            catch (Exception ex)
            {
                JournaliserMaj(majLogPath, "Erreur lors de l'echange des fichiers : " + ex.Message);
                RestaurerVersionAncienne(rootDir, ancienDir, deployes, sauvegardes, majLogPath);
                MessageBox.Show(
                    "Echec lors du remplacement des fichiers de mise a jour. L'ancienne version a ete restauree.\r\nJournal : " + majLogPath,
                    "Jarvis AIOS",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
                return 1;
            }

            JournaliserMaj(majLogPath, "Relance de Jarvis apres mise a jour...");
            string scriptLancer = Path.Combine(rootDir, @"scripts\lancer.ps1");
            string extraArgs = silencieux ? "-SansNavigateur" : null;
            DateTime debutSonde = DateTime.UtcNow;

            int exitCode = File.Exists(scriptLancer)
                ? LancerPowerShell(scriptLancer, rootDir, StartTimeoutMs, extraArgs)
                : 1;

            bool sain = false;
            if (exitCode == 0)
            {
                int restantMs = UpdateHealthWaitMs - (int)(DateTime.UtcNow - debutSonde).TotalMilliseconds;
                if (restantMs < 5000)
                {
                    restantMs = 5000;
                }
                sain = AttendreServeurActif(restantMs);
            }

            if (sain)
            {
                JournaliserMaj(majLogPath, "Mise a jour validee : /api/sante repond HTTP 200.");
                try
                {
                    SupprimerCheminRecursif(ancienDir);
                    SupprimerCheminRecursif(stagingDir);
                }
                catch (Exception exClean)
                {
                    JournaliserMaj(majLogPath, "Avertissement nettoyage post-maj : " + exClean.Message);
                }
                return 0;
            }

            JournaliserMaj(majLogPath, "Echec de la sonde sante sous 90 s (code=" + exitCode + "). Restauration de _ancien\\...");
            if (File.Exists(scriptArreter))
            {
                LancerPowerShell(scriptArreter, rootDir, StopTimeoutMs, null);
                Thread.Sleep(1000);
            }

            RestaurerVersionAncienne(rootDir, ancienDir, deployes, sauvegardes, majLogPath);

            if (File.Exists(scriptLancer))
            {
                LancerPowerShell(scriptLancer, rootDir, StartTimeoutMs, "-SansNavigateur");
            }

            MessageBox.Show(
                "La mise a jour n'a pas demarre correctement dans les 90 secondes. L'ancienne version a ete restauree.\r\nJournal : " + majLogPath,
                "Jarvis AIOS",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            return 1;
        }

        /// <summary>
        /// Construit la liste ordonnee des dossiers et fichiers a remplacer lors d'une mise a jour.
        /// </summary>
        /// <param name="stagingDir">Chemin du dossier _maj_staging.</param>
        /// <returns>Liste des noms relatifs a sauvegarder puis deployer.</returns>
        /// <remarks>
        /// Effets de bord : lecture seule du contenu de premier niveau de _maj_staging.
        /// </remarks>
        private static List<string> CollecterCiblesMiseAJour(string stagingDir)
        {
            List<string> cibles = new List<string>();
            for (int i = 0; i < CiblesMiseAJour.Length; i++)
            {
                cibles.Add(CiblesMiseAJour[i]);
            }

            string[] entrees = Directory.GetFileSystemEntries(stagingDir);
            for (int i = 0; i < entrees.Length; i++)
            {
                string nom = Path.GetFileName(entrees[i]);
                if (string.IsNullOrEmpty(nom))
                {
                    continue;
                }

                if (string.Equals(nom, "installed.flag", StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(nom, "Jarvis.exe", StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(nom, "python", StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(nom, "_ancien", StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(nom, "_maj_staging", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                bool dejaPresent = false;
                for (int j = 0; j < cibles.Count; j++)
                {
                    if (string.Equals(cibles[j], nom, StringComparison.OrdinalIgnoreCase))
                    {
                        dejaPresent = true;
                        break;
                    }
                }
                if (!dejaPresent)
                {
                    cibles.Add(nom);
                }
            }

            return cibles;
        }

        /// <summary>
        /// Restaure les elements sauvegardes depuis _ancien\ vers le dossier racine.
        /// </summary>
        /// <param name="rootDir">Dossier racine de l'application.</param>
        /// <param name="ancienDir">Dossier de sauvegarde _ancien\.</param>
        /// <param name="deployes">Noms deja deployes depuis _maj_staging a supprimer avant restauration.</param>
        /// <param name="sauvegardes">Noms sauvegardes dans _ancien\ a remettre en place.</param>
        /// <param name="majLogPath">Chemin du fichier journal maj.log.</param>
        /// <remarks>
        /// Effets de bord : supprime les fichiers partiellement deployes, deplace _ancien\* vers rootDir et ecrit dans maj.log.
        /// </remarks>
        private static void RestaurerVersionAncienne(
            string rootDir,
            string ancienDir,
            List<string> deployes,
            List<string> sauvegardes,
            string majLogPath)
        {
            for (int i = 0; i < deployes.Count; i++)
            {
                try
                {
                    SupprimerCheminRecursif(Path.Combine(rootDir, deployes[i]));
                }
                catch (Exception ex)
                {
                    JournaliserMaj(majLogPath, "Avertissement suppression pendant restauration (" + deployes[i] + ") : " + ex.Message);
                }
            }

            for (int i = 0; i < sauvegardes.Count; i++)
            {
                string nom = sauvegardes[i];
                try
                {
                    string src = Path.Combine(ancienDir, nom);
                    string dst = Path.Combine(rootDir, nom);
                    if (Directory.Exists(src) || File.Exists(src))
                    {
                        SupprimerCheminRecursif(dst);
                        DeplacerCheminAvecReessai(src, dst);
                    }
                }
                catch (Exception ex)
                {
                    JournaliserMaj(majLogPath, "Erreur restauration (" + nom + ") : " + ex.Message);
                }
            }
        }

        /// <summary>
        /// Attend que /api/sante cesse de repondre dans le delai maximal imparti.
        /// </summary>
        /// <param name="timeoutMs">Delai maximal d'attente en millisecondes.</param>
        /// <returns>True si le serveur est arrete avant expiration, False sinon.</returns>
        /// <remarks>
        /// Effets de bord : effectue des sondes HTTP locales periodiques toutes les 500 ms.
        /// </remarks>
        private static bool AttendreArretServeur(int timeoutMs)
        {
            DateTime limite = DateTime.UtcNow.AddMilliseconds(timeoutMs);
            while (DateTime.UtcNow < limite)
            {
                if (!EstServeurActif())
                {
                    return true;
                }
                Thread.Sleep(500);
            }
            return !EstServeurActif();
        }

        /// <summary>
        /// Attend que /api/sante reponde HTTP 200 dans le delai maximal imparti.
        /// </summary>
        /// <param name="timeoutMs">Delai maximal d'attente en millisecondes.</param>
        /// <returns>True si le serveur repond HTTP 200 avant expiration, False sinon.</returns>
        /// <remarks>
        /// Effets de bord : effectue des sondes HTTP locales periodiques toutes les 1000 ms.
        /// </remarks>
        private static bool AttendreServeurActif(int timeoutMs)
        {
            DateTime limite = DateTime.UtcNow.AddMilliseconds(timeoutMs);
            while (DateTime.UtcNow < limite)
            {
                if (EstServeurActif())
                {
                    return true;
                }
                Thread.Sleep(1000);
            }
            return EstServeurActif();
        }

        /// <summary>
        /// Deplace un fichier ou un dossier avec reessais en cas de verrouillage transitoire Windows.
        /// </summary>
        /// <param name="source">Chemin source existant.</param>
        /// <param name="destination">Chemin cible inexistant sur le meme volume.</param>
        /// <remarks>
        /// Effets de bord : deplace le dossier/fichier sur disque et peut temporiser entre les essais.
        /// </remarks>
        private static void DeplacerCheminAvecReessai(string source, string destination)
        {
            const int maxEssais = 6;
            for (int essai = 1; essai <= maxEssais; essai++)
            {
                try
                {
                    if (Directory.Exists(source))
                    {
                        Directory.Move(source, destination);
                        return;
                    }
                    if (File.Exists(source))
                    {
                        if (File.Exists(destination))
                        {
                            File.Delete(destination);
                        }
                        File.Move(source, destination);
                        return;
                    }
                    return;
                }
                catch (IOException)
                {
                    if (essai == maxEssais)
                    {
                        throw;
                    }
                    Thread.Sleep(500);
                }
                catch (UnauthorizedAccessException)
                {
                    if (essai == maxEssais)
                    {
                        throw;
                    }
                    Thread.Sleep(500);
                }
            }
        }

        /// <summary>
        /// Supprime un fichier ou un dossier recursivement en retirant les attributs lecture seule.
        /// </summary>
        /// <param name="chemin">Chemin du fichier ou dossier a supprimer.</param>
        /// <remarks>
        /// Effets de bord : supprime definitivement les fichiers/dossiers cibles sur le disque.
        /// </remarks>
        private static void SupprimerCheminRecursif(string chemin)
        {
            if (File.Exists(chemin))
            {
                File.SetAttributes(chemin, FileAttributes.Normal);
                File.Delete(chemin);
                return;
            }

            if (!Directory.Exists(chemin))
            {
                return;
            }

            string[] fichiers = Directory.GetFiles(chemin, "*", SearchOption.AllDirectories);
            for (int i = 0; i < fichiers.Length; i++)
            {
                File.SetAttributes(fichiers[i], FileAttributes.Normal);
            }

            string[] sousDossiers = Directory.GetDirectories(chemin, "*", SearchOption.AllDirectories);
            for (int i = 0; i < sousDossiers.Length; i++)
            {
                File.SetAttributes(sousDossiers[i], FileAttributes.Normal);
            }

            Directory.Delete(chemin, true);
        }

        /// <summary>
        /// Retourne le dossier des journaux selon le mode installe (installed.flag) ou developpement.
        /// </summary>
        /// <param name="rootDir">Dossier programme contenant Jarvis.exe.</param>
        /// <returns>Chemin absolu vers runtime\logs (dans %USERPROFILE%\JarvisAIOS si installe).</returns>
        /// <remarks>
        /// Effets de bord : aucun (lecture seule du systeme de fichiers).
        /// </remarks>
        private static string ObtenirDossierLogs(string rootDir)
        {
            if (File.Exists(Path.Combine(rootDir, "installed.flag")))
            {
                string userProfile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
                return Path.Combine(userProfile, @"JarvisAIOS\runtime\logs");
            }

            return Path.Combine(rootDir, @"runtime\logs");
        }

        /// <summary>
        /// Ajoute une ligne horodatee dans le fichier journal maj.log.
        /// </summary>
        /// <param name="majLogPath">Chemin complet du fichier maj.log.</param>
        /// <param name="message">Message a enregistrer.</param>
        /// <remarks>
        /// Effets de bord : cree le dossier parent si absent et ecrit dans maj.log (erreurs ignorees).
        /// </remarks>
        private static void JournaliserMaj(string majLogPath, string message)
        {
            try
            {
                string dir = Path.GetDirectoryName(majLogPath);
                if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                {
                    Directory.CreateDirectory(dir);
                }

                string ligne = string.Format(
                    "{0:yyyy-MM-dd HH:mm:ss} {1}{2}",
                    DateTime.Now,
                    message,
                    Environment.NewLine);
                File.AppendAllText(majLogPath, ligne, Encoding.UTF8);
            }
            catch
            {
            }
        }

        /// <summary>
        /// Determine le dossier racine de l'application a partir du repertoire de l'executable.
        /// </summary>
        /// <returns>Chemin absolu du dossier racine sans separateur final superflu.</returns>
        /// <remarks>
        /// Effets de bord : aucun (lecture seule de AppDomain.CurrentDomain.BaseDirectory).
        /// </remarks>
        private static string ObtenirDossierRacine()
        {
            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            if (string.IsNullOrEmpty(baseDir))
            {
                baseDir = Environment.CurrentDirectory;
            }

            if (baseDir.Length > 3)
            {
                return baseDir.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            }

            return baseDir;
        }

        /// <summary>
        /// Indique si les arguments de ligne de commande contiennent une option donnee.
        /// </summary>
        /// <param name="args">Tableau des arguments passes a l'executable.</param>
        /// <param name="option">Option recherchee (ex. --arreter, --silencieux, --appliquer-maj).</param>
        /// <returns>True si l'option est presente (insensible a la casse), False sinon.</returns>
        /// <remarks>
        /// Effets de bord : aucun (fonction pure).
        /// </remarks>
        private static bool ContientArgument(string[] args, string option)
        {
            if (args == null)
            {
                return false;
            }

            for (int i = 0; i < args.Length; i++)
            {
                if (string.Equals(args[i], option, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }

            return false;
        }

        /// <summary>
        /// Verifie si le backend Jarvis AIOS repond deja avec un statut HTTP 200 sur /api/sante.
        /// </summary>
        /// <returns>True si le serveur repond HTTP 200 dans le delai de 2000 ms, False sinon.</returns>
        /// <remarks>
        /// Effets de bord : emet une requete HTTP GET locale vers 127.0.0.1:5230 sans proxy.
        /// Toute exception reseau ou delai depasse est intercepte et retourne False.
        /// </remarks>
        private static bool EstServeurActif()
        {
            try
            {
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create(HealthUrl);
                request.Method = "GET";
                request.Timeout = HealthTimeoutMs;
                request.ReadWriteTimeout = HealthTimeoutMs;
                request.Proxy = null;
                request.KeepAlive = false;

                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                {
                    return response.StatusCode == HttpStatusCode.OK;
                }
            }
            catch
            {
                return false;
            }
        }

        /// <summary>
        /// Ouvre l'URL du tableau de bord Jarvis AIOS dans le navigateur par defaut de Windows.
        /// </summary>
        /// <remarks>
        /// Effets de bord : demarre un processus shell Windows (UseShellExecute = true) sur http://127.0.0.1:5230/.
        /// </remarks>
        private static void OuvrirNavigateur()
        {
            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = AppUrl;
            psi.UseShellExecute = true;
            Process.Start(psi);
        }

        /// <summary>
        /// Execute un script PowerShell sans fenetre et attend sa terminaison jusqu'au delai imparti.
        /// </summary>
        /// <param name="scriptPath">Chemin complet du script .ps1 a executer.</param>
        /// <param name="workingDir">Repertoire de travail du processus PowerShell.</param>
        /// <param name="timeoutMs">Temps d'attente maximal en millisecondes avant de rendre la main.</param>
        /// <param name="extraArgs">Arguments supplementaires passes au script PowerShell, ou null.</param>
        /// <returns>
        /// Code de sortie du processus s'il s'est termine dans le delai imparti,
        /// 0 s'il poursuit son execution au-dela du delai, ou 1 si le processus n'a pas pu demarrer.
        /// </returns>
        /// <remarks>
        /// Effets de bord : lance powershell.exe en mode cache (CreateNoWindow=true, UseShellExecute=false).
        /// </remarks>
        private static int LancerPowerShell(string scriptPath, string workingDir, int timeoutMs, string extraArgs)
        {
            string arguments = string.Format(
                "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"{0}\"",
                scriptPath);
            if (!string.IsNullOrEmpty(extraArgs))
            {
                arguments = arguments + " " + extraArgs;
            }

            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = "powershell.exe";
            psi.Arguments = arguments;
            psi.WorkingDirectory = workingDir;
            psi.CreateNoWindow = true;
            psi.UseShellExecute = false;

            using (Process process = Process.Start(psi))
            {
                if (process == null)
                {
                    return 1;
                }

                bool exited = process.WaitForExit(timeoutMs);
                if (exited)
                {
                    return process.ExitCode;
                }

                return 0;
            }
        }
    }
}
