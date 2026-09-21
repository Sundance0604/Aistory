from __future__ import annotations

import re
from functools import lru_cache


TOKENIZER_VERSION = "tiktoken-v1:o200k_base-fallback"


@lru_cache(maxsize=1)
def _encoding():
    try:
        import tiktoken

        return tiktoken.get_encoding("o200k_base")
    except Exception:
        return None


def count_visible_tokens(text: str, model: str | None = None) -> int:
    if not text:
        return 0
    encoding = _encoding()
    if encoding is not None:
        return len(encoding.encode(text, disallowed_special=()))
    # Keeps imports/tests usable before optional dependencies are installed.
    # Production installs use tiktoken; this conservative fallback is documented.
    cjk = re.findall(r"[\u3400-\u9fff]", text)
    other = re.findall(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_\u3400-\u9fff]", text)
    return len(cjk) + len(other)
