"""Find literal web references without consuming adjacent prose."""

import re


WEB_URL = re.compile(r'(?:https?://|www\.)[^\s<>"\u200e\u200f“”]+', re.IGNORECASE)
_QUERY_PART = re.compile(r"[A-Za-z0-9%._~+\-]*(?:[?&]?[A-Za-z0-9_.%~-]+)=[A-Za-z0-9%._~+&=:/?#,;@!$*()\-]*")
_NEXT_TOKEN = re.compile(r"\s+([^\s<>\"\u200e\u200f“”]+)")


def iter_web_references(text: str):
    """Yield (source start, source end, exact joined URL) in reading order."""
    end = 0
    for match in WEB_URL.finditer(text):
        if match.start() < end:
            continue
        end = match.end()
        url = match[0]
        while url[-1] in "/?&=_-":
            following = _NEXT_TOKEN.match(text, end)
            if following is None:
                break
            token = following[1]
            if not (
                _QUERY_PART.fullmatch(token)
                or (url.endswith("/") and re.fullmatch(r"[\w.%~-]+/[\w.%~/\-]*", token, re.ASCII))
            ):
                break
            url += token
            end = following.end()
        clean = url.rstrip(".,;:!?،؛؟")
        while clean.endswith(")") and clean.count(")") > clean.count("("):
            clean = clean[:-1]
        clean = clean.rstrip("]")
        end -= len(url) - len(clean)
        yield match.start(), end, clean


def stash_web_references(text: str) -> tuple[str, list[str]]:
    spans = list(iter_web_references(text))
    for idx in range(len(spans) - 1, -1, -1):
        start, end, _ = spans[idx]
        text = f"{text[:start]}\uE002{idx}\uE003{text[end:]}"
    return text, [url for _, _, url in spans]


def restore_web_references(text: str, urls: list[str], *, mark_ltr: bool = False) -> str:
    if not urls:
        return text
    mark = "\u200e" if mark_ltr else ""
    return re.sub(r"\uE002(\d+)\uE003", lambda m: f"{mark}{urls[int(m[1])]}{mark}", text)
