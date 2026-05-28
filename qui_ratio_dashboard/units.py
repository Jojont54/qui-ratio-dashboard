import re


_SUFFIX = {
    "B": 1,
    "O": 1,
    "K": 1024,
    "KO": 1024,
    "KB": 1024,
    "KIO": 1024,
    "KIB": 1024,
    "M": 1024**2,
    "MO": 1024**2,
    "MB": 1024**2,
    "MIO": 1024**2,
    "MIB": 1024**2,
    "G": 1024**3,
    "GO": 1024**3,
    "GB": 1024**3,
    "GIO": 1024**3,
    "GIB": 1024**3,
    "T": 1024**4,
    "TO": 1024**4,
    "TB": 1024**4,
    "TIO": 1024**4,
    "TIB": 1024**4,
    "P": 1024**5,
    "PO": 1024**5,
    "PB": 1024**5,
    "PIO": 1024**5,
    "PIB": 1024**5,
}

def has_byte_unit(value) -> bool:
    if value is None or isinstance(value, (int, float)):
        return False
    text = str(value).strip()
    if not text:
        return False
    match = re.match(
        r"^[+-]?[0-9]+(?:[\.,][0-9]+)?\s*([A-Za-z]{1,4})$", text
    )
    return bool(match and match.group(1).upper() in _SUFFIX)


def parse_bytes(value) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return 0
    match = re.match(r"^([+-]?[0-9]+(?:[\.,][0-9]+)?)\s*([A-Za-z]{1,4})$", text)
    if not match:
        try:
            return int(text)
        except ValueError:
            return 0
    number = float(match.group(1).replace(",", "."))
    suffix = match.group(2).upper()
    if suffix not in _SUFFIX:
        return 0
    return int(number * _SUFFIX[suffix])


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
