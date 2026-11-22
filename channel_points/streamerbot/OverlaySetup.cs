using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;

// Streamer.bot action "Channel Points Overlay Setup" (runs when the stream starts; safe to run any time):
// makes sure the OBS browser source "Channel Points Alerts" (Documents\GreMiChannelPoints\alerts.html)
// is on top in every horizontal "[H] ..." scene. Adds nothing that is already there.
public class CPHInline
{
    const string SOURCE = "Channel Points Alerts";
    const string PAGE = @"C:\Users\milan\Documents\GreMiChannelPoints\alerts.html";

    public bool Execute()
    {
        JObject scenes = JObject.Parse(CPH.ObsSendRaw("GetSceneList", "{}"));
        var names = scenes["scenes"].Select(s => (string)s["sceneName"]).Where(n => n.StartsWith("[H] ")).ToList();
        JObject inputs = JObject.Parse(CPH.ObsSendRaw("GetInputList", "{}"));
        bool exists = inputs["inputs"].Any(i => (string)i["inputName"] == SOURCE);
        int added = 0;
        foreach (string scene in names)
        {
            JObject items = JObject.Parse(CPH.ObsSendRaw("GetSceneItemList", new JObject { ["sceneName"] = scene }.ToString()));
            if (items["sceneItems"].Any(i => (string)i["sourceName"] == SOURCE)) continue;
            if (!exists)
            {
                CPH.ObsSendRaw("CreateInput", new JObject {
                    ["sceneName"] = scene, ["inputName"] = SOURCE, ["inputKind"] = "browser_source",
                    ["sceneItemEnabled"] = true,
                    ["inputSettings"] = new JObject {
                        ["is_local_file"] = true, ["local_file"] = PAGE, ["width"] = 1920, ["height"] = 1080,
                        ["reroute_audio"] = false, ["restart_when_active"] = false, ["shutdown"] = false }
                }.ToString());
                exists = true;
            }
            else
            {
                CPH.ObsSendRaw("CreateSceneItem", new JObject { ["sceneName"] = scene, ["sourceName"] = SOURCE }.ToString());
            }
            added++;
        }
        CPH.LogInfo("[Channel Points] overlay added to " + added + " of " + names.Count + " [H] scenes");
        return true;
    }
}
