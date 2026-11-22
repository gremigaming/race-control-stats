using System;
using Newtonsoft.Json;

// First step of Streamer.bot actions "Results In" (cp = race-end: sponsor tag off) and
// "Remove Temporary VIPs" (cp = stream-end: challenge + sponsor tags off) for alerts.html.
public class CPHInline
{
    public bool Execute()
    {
        string action = args.ContainsKey("actionName") ? args["actionName"].ToString() : "";
        string cp = action == "Results In" ? "race-end" : "stream-end";
        CPH.WebsocketBroadcastJson(JsonConvert.SerializeObject(new { cp = cp }));
        return true;
    }
}
