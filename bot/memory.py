"""What Race Control said before, kept on the server (never in this public repo)
so it doesn't contradict itself between questions, channels or restarts.
No Discord code here, so it can be tested on its own.
"""
import json
import os
import pathlib
import time

PATH = pathlib.Path(os.environ.get("RACE_CONTROL_MEMORY", "/var/lib/race-control/said.jsonl"))
KEEP = 300   # exchanges kept on disk
RECALL = 8   # exchanges shown with each new question
CLIP = 200   # characters kept per question or reply


def said(text):
    """A reply of ours without the timer line the bot adds under it."""
    return text.split("\n-# ")[0]


class Memory:
    def __init__(self, path=PATH):
        self.path = pathlib.Path(path)
        self.items = []
        try:
            for line in self.path.read_text().splitlines():
                self.items.append(json.loads(line))
        except (OSError, ValueError):
            pass
        self.items = self.items[-KEEP:]

    def add(self, channel, asker, question, answer, at=None):
        self.items.append({"at": int(at or time.time()), "channel": channel, "asker": asker,
                           "q": question[:CLIP], "a": answer[:CLIP]})
        self.items = self.items[-KEEP:]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text("".join(json.dumps(i) + "\n" for i in self.items))
            tmp.replace(self.path)
        except OSError:
            pass  # memory is a nice-to-have; answering still works

    def recall(self, limit=RECALL):
        lines = []
        for i in self.items[-limit:]:
            day = time.strftime("%Y-%m-%d %H:%M", time.gmtime(i["at"]))
            lines.append(f"[{day} UTC #{i['channel']}] {i['asker']}: {i['q']}\n  you: {i['a']}")
        return "\n".join(lines)
