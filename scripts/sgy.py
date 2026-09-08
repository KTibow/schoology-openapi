"""Minimal Schoology API client: OAuth 1.0a PLAINTEXT, no deps (urllib)."""
import json, os, time, urllib.request, urllib.parse, uuid, sys

# load .env from repo root
_envpath = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(_envpath):
    for line in open(_envpath):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

KEY = os.environ["SC_KEY_75"]
SECRET = os.environ["SC_SECRET_75"]
TOKEN_KEY = os.environ.get("TOKEN_KEY", "")
TOKEN_SECRET = os.environ.get("TOKEN_SECRET", "")

BASE = "https://api.schoology.com"

def auth_header(url, method="GET", body=b""):
    p = urllib.parse.urlparse(url)
    params = {
        "realm": "Schoology API",
        "oauth_consumer_key": KEY,
        "oauth_token": TOKEN_KEY,
        "oauth_nonce": uuid.uuid4().hex,
        "oauth_timestamp": str(int(time.time())),
        "oauth_signature_method": "PLAINTEXT",
        "oauth_version": "1.0",
    }
    sig = urllib.parse.quote(SECRET, safe="") + "&" + urllib.parse.quote(TOKEN_SECRET, safe="")
    params["oauth_signature"] = sig
    items = [f'{k}="{urllib.parse.quote(v, safe="")}"' for k, v in params.items()]
    return "OAuth " + ", ".join(items)

def request(path, method="GET", body=None, ctype="application/json", follow=True):
    url = path if path.startswith("http") else BASE + path
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", auth_header(url, method, data or b""))
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", ctype)
    req.add_header("Host", "api.schoology.com")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k): return None
    opener = urllib.request.build_opener(NoRedirect)  # ALWAYS no auto-redirect
    try:
        with opener.open(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, dict(resp.headers), raw
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()

def request_follow(path, method="GET", body=None, ctype="application/json", max_hops=5):
    """Request, manually following redirects with freshly generated auth each hop."""
    s, h, b = request(path, method, body, ctype)
    hops = 0
    while s in (301, 302, 303, 307, 308) and hops < max_hops:
        loc = h.get("Location") or h.get("location")
        if not loc:
            break
        s, h, b = request(loc, method, body, ctype)
        hops += 1
    return s, h, b

def get(path, **kw):
    s, h, b = request_follow(path, "GET", **kw)
    return s, h, b

def json_body(b):
    try: return json.loads(b)
    except Exception: return b[:400].decode("utf-8", "replace")

if __name__ == "__main__":
    for p in sys.argv[1:]:
        s, h, b = get(p)
        print(f"=== GET {p} -> {s}")
        print(json.dumps(json_body(b), indent=1)[:3000])
