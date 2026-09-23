from __future__ import annotations

import base64
import json
import shlex

from app.core.pip_popularity_source import POPULARITY_SOURCE
RESULT_MARKER = "__REMOTE_LINUX_PIP_PROJECTS__"
_REMOTE_SEARCH_SOURCE = r'''
import base64
import gzip
import hashlib
import html
import io
import json
import os
import re
import shlex
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
''' + POPULARITY_SOURCE + r'''


MARKER = "__REMOTE_LINUX_PIP_PROJECTS__"
DEFAULT_INDEX = "https://pypi.org/simple/"
def normalized(value):
    return re.sub(r"[-_.]+", "-", str(value or "")).casefold()
def split_value(value):
    try:
        return shlex.split(str(value or "").strip())
    except ValueError:
        return [str(value or "").strip().strip("'\"")]
def pip_settings():
    primary = os.environ.get("PIP_INDEX_URL", "").strip()
    extras = split_value(os.environ.get("PIP_EXTRA_INDEX_URL", ""))
    trusted = split_value(os.environ.get("PIP_TRUSTED_HOST", ""))
    cert = os.environ.get("PIP_CERT", "").strip()
    proxy = os.environ.get("PIP_PROXY", "").strip()
    no_index = os.environ.get("PIP_NO_INDEX", "").lower() in {
        "1", "true", "yes", "on",
    }
    result = subprocess.run(
        [sys.executable, "-m", "pip", "config", "list"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=20,
        check=False,
    )
    for raw in result.stdout.splitlines():
        key, separator, value = raw.partition("=")
        if not separator:
            continue
        key = key.strip().lower()
        values = split_value(value)
        if key.endswith(".index-url") and not key.endswith(".extra-index-url"):
            if not primary and values:
                primary = values[-1]
        elif key.endswith(".extra-index-url"):
            extras.extend(values)
        elif key.endswith(".trusted-host"):
            trusted.extend(values)
        elif key.endswith(".cert") and not cert and values:
            cert = values[-1]
        elif key.endswith(".proxy") and not proxy and values:
            proxy = values[-1]
        elif key.endswith(".no-index") and values:
            no_index = values[-1].lower() in {"1", "true", "yes", "on"}
    if no_index:
        return [], trusted, cert, proxy
    urls = [primary or DEFAULT_INDEX, *extras]
    unique = []
    for value in urls:
        value = str(value or "").strip().rstrip("/") + "/"
        if value not in unique:
            unique.append(value)
    return unique, trusted, cert, proxy
def safe_request(url):
    parts = urllib.parse.urlsplit(url)
    username = urllib.parse.unquote(parts.username or "")
    password = urllib.parse.unquote(parts.password or "")
    host = parts.hostname or ""
    netloc = host
    if parts.port:
        netloc += ":" + str(parts.port)
    path = parts.path
    if parts.scheme == "file" and path.endswith("/"):
        path += "index.html"
    clean = urllib.parse.urlunsplit(
        (parts.scheme, netloc, path, parts.query, parts.fragment)
    )
    request = urllib.request.Request(clean, headers={
        "Accept": "text/html",
        "Accept-Encoding": "gzip",
        "User-Agent": "Remote-Linux-pip-search/1",
    })
    if username:
        token = base64.b64encode(
            (username + ":" + password).encode("utf-8")
        ).decode("ascii")
        request.add_header("Authorization", "Basic " + token)
    return request, host
class ProjectParser(HTMLParser):
    def __init__(self, query, cache_writer=None):
        super().__init__(convert_charrefs=True)
        self.query = normalized(query)
        self.cache_writer = cache_writer
        self.in_link = False
        self.text = []
        self.matches = set()

    def handle_starttag(self, tag, attrs):
        del attrs
        if tag.lower() == "a":
            self.in_link = True
            self.text = []

    def handle_data(self, data):
        if self.in_link:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() != "a" or not self.in_link:
            return
        self.in_link = False
        name = html.unescape("".join(self.text)).strip()
        if name and self.cache_writer:
            self.cache_writer.write(json.dumps(name, ensure_ascii=False) + "\n")
        if self.query in normalized(name) and len(self.matches) < 10000:
            self.matches.add(name)


def ssl_context(cert, host, trusted):
    trusted_names = {value.split(":", 1)[0].casefold() for value in trusted}
    if host.casefold() in trusted_names:
        return ssl._create_unverified_context()
    if cert and os.path.isfile(cert):
        return ssl.create_default_context(cafile=cert)
    return ssl.create_default_context()
def cache_file(url):
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
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return os.path.join(root, digest + ".jsonl.gz")


def cached_names(path, query):
    matches = set()
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as stream:
        for raw in stream:
            try:
                name = str(json.loads(raw))
            except (TypeError, ValueError):
                continue
            if normalized(query) in normalized(name) and len(matches) < 10000:
                matches.add(name)
    return matches


def project_names(url, query, trusted, cert, proxy):
    request, host = safe_request(url)
    cache = cache_file(url)
    if cache and os.path.isfile(cache) and time.time() - os.path.getmtime(cache) < 3600:
        return cached_names(cache, query), host
    context = ssl_context(cert, host, trusted)
    handlers = [urllib.request.HTTPSHandler(context=context)]
    if proxy:
        handlers.insert(0, urllib.request.ProxyHandler({
            "http": proxy, "https": proxy,
        }))
    opener = urllib.request.build_opener(*handlers)
    temporary = ""
    try:
        writer = None
        if cache:
            handle = tempfile.NamedTemporaryFile(dir=os.path.dirname(cache), delete=False)
            temporary = handle.name
            handle.close()
            os.chmod(temporary, 0o600)
            writer = gzip.open(temporary, "wt", encoding="utf-8")
        parser = ProjectParser(query, writer)
        try:
            with opener.open(request, timeout=60) as response:
                stream = response
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    stream = gzip.GzipFile(fileobj=response)
                reader = io.TextIOWrapper(
                    stream, encoding="utf-8", errors="replace"
                )
                while True:
                    chunk = reader.read(65536)
                    if not chunk:
                        break
                    parser.feed(chunk)
        finally:
            if writer:
                writer.close()
        if temporary:
            os.replace(temporary, cache)
            temporary = ""
        return parser.matches, host
    except Exception:
        if cache and os.path.isfile(cache):
            return cached_names(cache, query), host
        raise
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def rank(name, query, popular):
    candidate = normalized(name)
    wanted = normalized(query)
    if candidate == wanted:
        group = 0
    elif candidate.startswith(wanted):
        group = 1
    else:
        group = 2
    popularity_key = popular.get(candidate, (0, 1000000))
    known = 0 if candidate in popular else 1
    return group, known, popularity_key, len(candidate), candidate


def main():
    query = sys.argv[1]
    limit = max(1, min(100, int(sys.argv[2])))
    urls, trusted, cert, proxy = pip_settings()
    found = {}
    errors = []
    for url in urls:
        try:
            names, host = project_names(url, query, trusted, cert, proxy)
            for name in names:
                found.setdefault(name, host)
        except Exception as exc:
            host = urllib.parse.urlsplit(url).hostname or "index pip"
            errors.append(host + ": " + str(exc))
    popular = popularity(urls, trusted, cert, proxy)
    names = sorted(found, key=lambda name: rank(name, query, popular))[:limit]
    rows = [{
        "name": name, "repo": found[name],
        "popularity_rank": popular.get(normalized(name), (0, 1000000))[1],
    } for name in names]
    print(MARKER)
    print(json.dumps({"rows": rows, "errors": errors}, ensure_ascii=False))


main()
'''


def build_repository_search_command(
    python_command: str, query: str, limit: int = 50
) -> str:
    encoded = base64.b64encode(_REMOTE_SEARCH_SOURCE.encode("utf-8")).decode("ascii")
    runner = (
        "import base64;exec(compile(base64.b64decode(" + repr(encoded) + "),"
        "'<remote-linux-pip-search>','exec'))"
    )
    return (
        f"{python_command} -c {shlex.quote(runner)} "
        f"{shlex.quote(str(query))} {max(1, min(100, int(limit)))}"
    )


def parse_repository_search(output: str) -> dict[str, object]:
    lines = str(output or "").splitlines()
    try:
        marker = lines.index(RESULT_MARKER)
        payload = json.loads(lines[marker + 1])
    except (ValueError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(output or "pip repository search failed.")) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("pip repository search returned an invalid response.")
    return payload
