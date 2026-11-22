using System;
using System.Diagnostics;
using System.IO;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

// Streamer.bot action "Banned word" (IRL word ban reward): replaces the old speech-command
// steps and the 15 minute delay. Tells the word ban listener (wordban.exe, offline Vosk
// speech recognition) which word to listen for, keeps it running for the whole ban and
// restarts it if it stops. The listener runs "Banned word spoken" when GreMi says the word.
public class CPHInline
{
    const string HOME = @"C:\Users\milan\Documents\GreMiChannelPoints";
    const int BAN_SECONDS = 15 * 60;

    public bool Execute()
    {
        string word = args.ContainsKey("rawInput") ? args["rawInput"].ToString().Trim() : "";
        if (word == "") return true;
        string dir = Path.Combine(HOME, "wordban");
        Directory.CreateDirectory(dir);
        long until = DateTimeOffset.UtcNow.ToUnixTimeSeconds() + BAN_SECONDS;
        File.WriteAllText(Path.Combine(dir, "ban.json"), JsonConvert.SerializeObject(new { word = word, until = until }));
        CPH.LogInfo("[Word ban] '" + word + "' for 15 minutes");

        bool warned = false;
        DateTime lastStart = DateTime.MinValue;
        while (DateTimeOffset.UtcNow.ToUnixTimeSeconds() < until)
        {
            string state = ListenerState(dir);
            if (state != "listening" && state != "starting" && (DateTime.UtcNow - lastStart).TotalSeconds > 20)
            {
                Start();
                lastStart = DateTime.UtcNow;
            }
            CPH.Wait(5000);
            state = ListenerState(dir);
            if (state != "listening" && state != "starting" && !warned && (DateTime.UtcNow - lastStart).TotalSeconds > 12)
            {
                CPH.SendMessage("⚠️ Word ban: the mic listener isn't hearing GreMi right now (" + state + "), retrying...");
                warned = true;
            }
            if (state == "listening") warned = false;
            if (CPH.GetGlobalVar<string>("bannedWord", true) != word) break;   // a newer ban took over
        }
        if (CPH.GetGlobalVar<string>("bannedWord", true) == word)
            File.WriteAllText(Path.Combine(dir, "ban.json"), JsonConvert.SerializeObject(new { word = "", until = 0 }));
        return true;
    }

    // starting / listening / idle / mic-error / stopped, or "off" when the status file is stale
    string ListenerState(string dir)
    {
        try
        {
            JObject s = JObject.Parse(File.ReadAllText(Path.Combine(dir, "status.json")));
            double age = DateTimeOffset.UtcNow.ToUnixTimeSeconds() - (double)s["time"];
            string state = (string)s["state"];
            return age < 10 ? state : "off";
        }
        catch { return "off"; }
    }

    void Start()
    {
        string exe = Path.Combine(HOME, "wordban.exe");
        if (!File.Exists(exe)) { CPH.LogWarn("[Word ban] " + exe + " is missing"); return; }
        Process.Start(new ProcessStartInfo(exe) { WorkingDirectory = HOME, CreateNoWindow = true, UseShellExecute = false });
        CPH.LogInfo("[Word ban] listener started");
    }
}
