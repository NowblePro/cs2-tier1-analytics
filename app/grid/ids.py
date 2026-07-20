import hashlib


def stable_negative_id(value: str) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return -int(digest[:12], 16)

