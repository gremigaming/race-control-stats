"""Puts the channel point actions into Streamer.bot's data/actions.json (Streamer.bot closed!).

python3 apply.py <actions.json in> <actions.json out> <reward ids file>
The reward ids file is the output of the Twitch chat workflow's 'rewards-sync'
("<id> | <title> | ..." lines). Safe to run again: it replaces what it added before.
"""
import base64
import json
import pathlib
import re
import sys
import uuid

HERE = pathlib.Path(__file__).parent
FW = "C:\\Windows\\Microsoft.NET\\Framework64\\v4.0.30319\\"
REFS = [FW + "mscorlib.dll", FW + "System.dll", FW + "System.Core.dll"]
DEFAULT_Q = "00000000-0000-0000-0000-000000000000"
FUN, SETUP = "Channel Points Fun", "Channel Points Overlay Setup"
REWARDS = ["Hydrate, driver!", "Safety Car", "Team Radio", "Box Box Box!", "Sponsor the Next Race",
           "Final Race Challenge", "Get a Walk-on Sound"]


def code(name, parent=None, index=0, label=None):
    return {"name": label, "description": None, "references": REFS,
            "byteCode": base64.b64encode((HERE / name).read_bytes()).decode(),
            "precompile": False, "delayStart": False, "saveResultToVariable": False, "saveToVariable": None,
            "id": str(uuid.uuid4()), "weight": 0.0, "type": 99999, "parentId": parent, "enabled": True, "index": index}


def action(name, triggers, subs):
    return {"id": str(uuid.uuid4()), "queue": DEFAULT_Q, "enabled": True, "excludeFromHistory": False,
            "excludeFromPending": False, "name": name, "group": "Channel Points", "alwaysRun": False,
            "randomAction": False, "concurrent": False, "triggers": triggers, "subActions": subs}


def reindex(subs):
    for i, s in enumerate(subs):
        s["index"] = i


def find(actions, name):
    hits = [a for a in actions if a["name"] == name]
    if len(hits) != 1:
        raise SystemExit(f"expected one action named {name!r}, found {len(hits)}")
    return hits[0]


def reward_ids(text):
    ids = {}
    for line in text.splitlines():
        m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}) \| ([^|]+?) \|", line)
        if m:
            ids[m.group(2).strip()] = m.group(1)
    missing = [t for t in REWARDS if t not in ids]
    if missing:
        raise SystemExit(f"no reward id for {missing}")
    return ids


def apply(data, ids):
    acts = data["actions"]
    acts[:] = [a for a in acts if a["name"] not in (FUN, SETUP)]
    acts.append(action(FUN, [{"rewardId": ids[t], "id": str(uuid.uuid4()), "type": 112, "enabled": True,
                              "exclusions": []} for t in REWARDS], [code("ChannelPointsFun.cs")]))
    acts.append(action(SETUP, [{"id": str(uuid.uuid4()), "type": 154, "enabled": True, "exclusions": []}],
                       [code("OverlaySetup.cs")]))

    # IRL word ban: the listener replaces the speech command steps and the 15 minute wait
    ban = find(acts, "Banned word")
    subs = [s for s in ban["subActions"] if s["type"] != 1018 and not (s["type"] == 1002 and s.get("value") == "900000")]
    if not any(s["type"] == 99999 and "Word ban" in base64.b64decode(s["byteCode"]).decode() for s in subs):
        at = next(i for i, s in enumerate(subs) if s["type"] == 30 and s.get("state") == 1)
        subs.insert(at, code("BannedWordWatch.cs", label="Word ban listener"))
    else:
        for s in subs:
            if s["type"] == 99999:
                s["byteCode"] = code("BannedWordWatch.cs")["byteCode"]
    reindex(subs)
    ban["subActions"] = subs
    for t in find(acts, "Banned word spoken")["triggers"]:
        if t["type"] == 301:
            t["enabled"] = False   # old Windows speech command, replaced by the listener

    def replace_code(sub_list, new):
        for s in sub_list:
            if s["type"] == 99999:
                s["byteCode"] = new
                return True
            if replace_code(s.get("subActions", []), new):
                return True
        return False
    if not replace_code(find(acts, "First Redeem Script")["subActions"], code("FirstRedeem.cs")["byteCode"]):
        raise SystemExit("First Redeem Script has no code step")

    for name in ("Results In", "Remove Temporary VIPs"):
        a = find(acts, name)
        clear = code("OverlayClear.cs", label="Channel point overlay clear")
        a["subActions"] = [s for s in a["subActions"] if s.get("name") != clear["name"]]
        a["subActions"].insert(0, clear)
        reindex(a["subActions"])
    return data


def main(src, dst, ids_file):
    raw = pathlib.Path(src).read_bytes()
    data = json.loads(raw.decode("utf-8-sig"))
    apply(data, reward_ids(pathlib.Path(ids_file).read_text()))
    out = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    pathlib.Path(dst).write_bytes((b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b"") + out.encode())
    print(f"wrote {dst}: {len(data['actions'])} actions")


if __name__ == "__main__":
    main(*sys.argv[1:4])
