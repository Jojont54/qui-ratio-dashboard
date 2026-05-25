import re


_SUFFIX = {
    "KIB": 1024,
    "MIB": 1024**2,
    "GIB": 1024**3,
    "TIB": 1024**4,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
}


def parse_bytes(value) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return 0
    match = re.match(r"^([+-]?[0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]{2,4})$", text)
    if not match:
        try:
            return int(text)
        except ValueError:
            return 0
    number = float(match.group(1))
    suffix = match.group(2).upper()
    return int(number * _SUFFIX.get(suffix, 1))


def fmt_bytes(value: int) -> str:
    number = int(value or 0)
    sign = "-" if number < 0 else ""
    number = abs(number)
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    converted = float(number)
    index = 0
    while converted >= 1024 and index < len(units) - 1:
        converted /= 1024
        index += 1
    return f"{sign}{converted:.2f} {units[index]}"
