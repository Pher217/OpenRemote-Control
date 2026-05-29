import shlex


def parse(text: str):
    stripped = text.strip()
    if not stripped.startswith("/"):
        return ("text", text)
    tokens = shlex.split(stripped)
    command = tokens[0][1:].lower()
    args = tokens[1:]
    return ("slash", command, args)
