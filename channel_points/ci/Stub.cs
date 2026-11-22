// Compile check only (channel_points.yml): the bits of Streamer.bot's CPH that our inline code uses.
using System.Collections.Generic;

public class Proxy
{
    public bool SendMessage(string m, bool bot = true, bool fallback = true) => true;
    public bool SendWhisper(string u, string m, bool bot = true) => true;
    public void LogInfo(string m) { }
    public void LogWarn(string m) { }
    public void LogError(string m) { }
    public double PlaySound(string f, float v = 1, bool finish = false, string name = null, bool useFileName = true) => 0;
    public void WebsocketBroadcastJson(string json) { }
    public void SetGlobalVar(string n, object v, bool persisted = true) { }
    public T GetGlobalVar<T>(string n, bool persisted = true) => default(T);
    public void Wait(int ms) { }
    public string ObsSendRaw(string type, string data, int connection = 0) => "{}";
    public bool TwitchAddVip(string u) => true;
    public bool TwitchRemoveVip(string u) => true;
    public T GetTwitchUserVarById<T>(string id, string n, bool persisted = true) => default(T);
    public void SetTwitchUserVarById(string id, string n, object v, bool persisted = true) { }
    public string ObsGetCurrentScene(int connection = 0) => "";
    public void ObsSetScene(string s, int connection = 0) { }
}

public class StubBase
{
    public Proxy CPH = new Proxy();
    public Dictionary<string, object> args = new Dictionary<string, object>();
}
