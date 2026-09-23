from __future__ import annotations


POPULARITY_SOURCE = r'''
DEFAULT_POPULARITY_URL = (
    "https://hugovk.dev/top-pypi-packages/top-pypi-packages.min.json"
)


def popularity_file(url):
    root = os.path.join(
        tempfile.gettempdir(), "remote-linux-pip-" + str(os.getuid())
    )
    try:
        os.makedirs(root, mode=0o700, exist_ok=True)
        if os.stat(root).st_uid != os.getuid():
            return ""
        os.chmod(root, 0o700)
    except OSError:
        return ""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return os.path.join(root, "popularity-" + digest + ".json")


def read_popularity(path):
    try:
        with open(path, "r", encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, TypeError, ValueError):
        return {}
    rows = payload.get("rows", []) if isinstance(payload, dict) else []
    result = {}
    for position, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        name = row.get("project") or row.get("name")
        if not name:
            continue
        count = row.get("download_count", 0)
        try:
            result[normalized(name)] = (-int(count), position)
        except (TypeError, ValueError):
            result[normalized(name)] = (0, position)
    return result


def popularity(urls, trusted, cert, proxy):
    override = os.environ.get("REMOTE_LINUX_PIP_POPULARITY_URL", "").strip()
    if not override and not any(
        (urllib.parse.urlsplit(url).hostname or "").casefold() == "pypi.org"
        for url in urls
    ):
        return {}
    url = override or DEFAULT_POPULARITY_URL
    cache = popularity_file(url)
    if cache and os.path.isfile(cache) and time.time() - os.path.getmtime(cache) < 86400:
        return read_popularity(cache)
    request = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "Remote-Linux-pip-search/1",
    })
    host = urllib.parse.urlsplit(url).hostname or ""
    handlers = [urllib.request.HTTPSHandler(context=ssl_context(cert, host, trusted))]
    if proxy:
        handlers.insert(0, urllib.request.ProxyHandler({
            "http": proxy, "https": proxy,
        }))
    temporary = ""
    try:
        with urllib.request.build_opener(*handlers).open(request, timeout=8) as response:
            payload = response.read(4 * 1024 * 1024)
        parsed = json.loads(payload.decode("utf-8"))
        if not isinstance(parsed, dict) or not isinstance(parsed.get("rows"), list):
            raise ValueError("invalid popularity data")
        if cache:
            handle = tempfile.NamedTemporaryFile(
                dir=os.path.dirname(cache), delete=False
            )
            temporary = handle.name
            handle.write(payload)
            handle.close()
            os.chmod(temporary, 0o600)
            os.replace(temporary, cache)
            temporary = ""
        return read_popularity(cache) if cache else {
            normalized(row.get("project") or row.get("name")): (
                -int(row.get("download_count", 0)), position
            )
            for position, row in enumerate(parsed["rows"])
            if isinstance(row, dict) and (row.get("project") or row.get("name"))
        }
    except Exception:
        return read_popularity(cache) if cache and os.path.isfile(cache) else {}
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
'''
