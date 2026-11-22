using System;
using System.Diagnostics;
using System.IO;
using System.Text.RegularExpressions;
using Newtonsoft.Json;

// Streamer.bot action "Channel Points Fun" (group Channel Points), one trigger per reward
// that Race Control owns (bot/channel_rewards.json in race-control-stats). Sounds live in
// Documents\GreMiChannelPoints\sounds, the overlay (alerts.html) shows the banner.
public class CPHInline
{
    const string HOME = @"C:\Users\milan\Documents\GreMiChannelPoints";
    const string WALKONS = @"C:\Users\milan\Music\Create Regular";

    public bool Execute()
    {
        string reward = Arg("rewardName");
        string user = Arg("user");
        if (user == "") user = Arg("userName");
        string text = Arg("rawInput").Trim();
        switch (reward)
        {
            case "Hydrate, driver!":
                Alert("hydrate", user, "");
                Sound("ding");
                CPH.SendMessage("💧 " + user + " says: hydrate, driver! GreMi, take a sip.");
                break;
            case "Safety Car":
                Alert("safety", user, "");
                Sound("siren");
                CPH.SendMessage("🚨 SAFETY CAR! " + user + " has neutralised the race. Bunch up!");
                break;
            case "Team Radio":
                string clean = Clean(text);
                if (clean == "")
                {
                    CPH.SendMessage(user + ", your team radio got lost in the static (no links or bad words, max 120 characters).");
                    break;
                }
                Alert("radio", user, clean);
                Sound("radio");
                Speak(user + " says: " + clean);
                Sound("radio");
                break;
            case "Box Box Box!":
                Alert("box", user, "");
                Sound("box");
                Speak("Box box. Box box.");
                CPH.SendMessage("📻 BOX BOX BOX! " + user + " calls GreMi into the pits. (On the final challenge race that's an order!)");
                break;
            case "Sponsor the Next Race":
                Alert("sponsor", user, "");
                Sound("fanfare");
                CPH.SendMessage("💜 The next race is proudly sponsored by " + user + "!");
                break;
            case "Final Race Challenge":
                string rule = Challenge(text);
                Alert("challenge", user, rule);
                Sound("fanfare");
                CPH.SetGlobalVar("finalRaceChallenge", rule + " (picked by " + user + ")", false);
                CPH.SendMessage("🏁 FINAL RACE CHALLENGE: " + rule + ", picked by " + user + "!");
                break;
            case "Get a Walk-on Sound":
                string folder = Path.Combine(WALKONS, Arg("userName"));
                Directory.CreateDirectory(folder);
                Alert("walkon", user, "");
                Sound("fanfare");
                CPH.SendMessage("🔊 " + user + " just bought a walk-on sound! DM GreMi on Discord with a 5-10 second sound and it plays with your first message every stream.");
                break;
            default:
                CPH.LogWarn("[Channel Points Fun] unknown reward " + reward);
                break;
        }
        return true;
    }

    string Arg(string key)
    {
        return args.ContainsKey(key) && args[key] != null ? args[key].ToString() : "";
    }

    void Alert(string kind, string user, string text)
    {
        CPH.WebsocketBroadcastJson(JsonConvert.SerializeObject(new { cp = kind, user = user, text = text }));
    }

    void Sound(string name)
    {
        string file = Path.Combine(HOME, "sounds", name + ".wav");
        if (File.Exists(file)) CPH.PlaySound(file, 0.6f, true);
    }

    // Windows text-to-speech through PowerShell (no extra Streamer.bot setup), played like the other sounds.
    void Speak(string line)
    {
        try
        {
            string wav = Path.Combine(Path.GetTempPath(), "gremi_cp_tts.wav");
            string safe = line.Replace("'", "''");
            string ps = "Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; " +
                        "$s.Rate = 1; $s.SetOutputToWaveFile('" + wav + "'); $s.Speak('" + safe + "'); $s.Dispose()";
            var p = Process.Start(new ProcessStartInfo("powershell.exe",
                "-NoProfile -ExecutionPolicy Bypass -Command \"" + ps.Replace("\"", "") + "\"")
                { CreateNoWindow = true, UseShellExecute = false });
            if (p != null && p.WaitForExit(15000) && File.Exists(wav)) CPH.PlaySound(wav, 0.8f, true);
        }
        catch (Exception e)
        {
            CPH.LogWarn("[Channel Points Fun] speech failed: " + e.Message);
        }
    }

    // Team radio text: max 120 characters, no links, nothing from the blocked list.
    static readonly string[] BLOCKED = { "nigg", "fag", "retard", "kys", "kill yourself", "cunt", "rape", "nazi", "hitler", "kanker", "tering" };
    string Clean(string text)
    {
        text = Regex.Replace(text, @"\s+", " ").Trim();
        if (text.Length == 0 || text.Length > 120) return "";
        string low = text.ToLowerInvariant();
        if (Regex.IsMatch(low, @"(https?://|www\.|\.(com|net|org|gg|tv|ly|io|nl)\b)")) return "";
        foreach (string b in BLOCKED) if (low.Contains(b)) return "";
        return text;
    }

    // Map the viewer's words onto one of the challenge rules; anything else is shown as typed.
    string Challenge(string text)
    {
        string low = text.ToLowerInvariant();
        if (Regex.IsMatch(low, @"cockpit|cam|camera|helmet")) return "Cockpit cam only";
        if (Regex.IsMatch(low, @"assist|abs|traction|\btc\b")) return "No assists";
        if (Regex.IsMatch(low, @"back|last|grid|p20|p22|rear")) return "Start from the back";
        if (Regex.IsMatch(low, @"one.?stop|1.?stop|single stop")) return "One-stop only";
        if (Regex.IsMatch(low, @"line|racing line")) return "No racing line";
        text = Regex.Replace(text, @"\s+", " ").Trim();
        if (text.Length > 60) text = text.Substring(0, 60);
        return text == "" ? "GreMi's choice" : text;
    }
}
