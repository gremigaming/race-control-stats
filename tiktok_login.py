"""One-time TikTok login for the stats updater (run by the "TikTok login" workflow).

Without a code it prints the TikTok link to open. After you approve, TikTok
sends you to the redirect page with ?code=... in the address. Run it again
with that whole address (or just the code) and it writes the refresh token
to the file named by TIKTOK_REFRESH_TOKEN_OUT, which the workflow saves as
the TIKTOK_REFRESH_TOKEN secret. Never prints tokens.
"""
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

CLIENT_KEY = os.environ.get("TIKTOK_CLIENT_KEY", "")
CLIENT_SECRET = os.environ.get("TIKTOK_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("TIKTOK_REDIRECT_URI", "")
OUT = os.environ.get("TIKTOK_REFRESH_TOKEN_OUT", "")
SCOPES = "user.info.basic,user.info.stats"


def authorize_url():
    return "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode({
        "client_key": CLIENT_KEY,
        "scope": SCOPES,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "state": secrets.token_urlsafe(16),
    })


def extract_code(text):
    """Accepts the whole redirect address or just the code."""
    text = text.strip()
    if "code=" in text:
        query = urllib.parse.urlparse(text).query or text.split("?", 1)[-1]
        codes = urllib.parse.parse_qs(query).get("code")
        if codes:
            return codes[0]
    return urllib.parse.unquote(text)


def exchange(code):
    body = urllib.parse.urlencode({
        "client_key": CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    }).encode()
    req = urllib.request.Request("https://open.tiktokapis.com/v2/oauth/token/", data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except ValueError:
            return {"error": f"HTTP {e.code}"}


def main(argv):
    missing = [n for n, v in (("TIKTOK_CLIENT_KEY variable", CLIENT_KEY),
                              ("TIKTOK_CLIENT_SECRET secret", CLIENT_SECRET),
                              ("redirect address", REDIRECT_URI)) if not v]
    if missing:
        print("Missing: " + ", ".join(missing))
        return 1
    given = argv[1] if len(argv) > 1 else ""
    if not given.strip():
        print("Step 1: open this link, log in to TikTok and click Authorize:\n")
        print(authorize_url())
        print("\nStep 2: copy the whole address of the page you land on, then run this"
              " workflow again and paste it into the box.")
        return 0
    result = exchange(extract_code(given))
    if "refresh_token" not in result or "user.info.stats" not in result.get("scope", ""):
        reason = result.get("error_description") or result.get("error") or "no follower permission granted"
        print(f"TikTok login failed: {reason}. Start again from step 1 (codes only work once"
              " and expire quickly).")
        return 1
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(result["refresh_token"])
    print("TikTok login worked. Saving it for the stats updater.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
