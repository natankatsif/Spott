"""Incremental JSON reader for a streamed model answer: what was just written, before the JSON is complete.

    reader = JsonEvents()
    for piece in stream:
        for event in reader.feed(piece): ...

Events (path = keys and indices from the root, e.g. ("sentences", 0, "text")):
    ("text", path, piece)   a part of a string value, as soon as it arrives
    ("value", path, value)  a complete string, number, true/false/null
    ("end", path)           an object or array closed
"""

import json

ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


class JsonEvents:
    def __init__(self) -> None:
        self.stack: list[list] = []  # per open container: [bracket, current key or index, expecting a key]
        self.string: list[str] | None = None  # characters of the string being read
        self.is_key = False
        self.escape: str | None = None  # after a backslash: the escape read so far
        self.high_surrogate: int | None = None
        self.literal = ""

    def path(self) -> tuple:
        return tuple(frame[1] for frame in self.stack)

    def feed(self, text: str) -> list[tuple]:
        events: list[tuple] = []
        piece: list[str] = []  # decoded characters of the current string value in this feed
        for ch in text:
            if self.string is not None:
                if self.escape is not None:
                    self.escape += ch
                    decoded = self._unescape()
                    if decoded:
                        self.string.append(decoded)
                        piece.append(decoded)
                elif ch == "\\":
                    self.escape = ""
                elif ch == '"':
                    self._close_string(events, piece)
                    piece = []
                else:
                    self.string.append(ch)
                    piece.append(ch)
                continue
            if ch == '"':
                self._end_literal(events)
                self.is_key = bool(self.stack) and self.stack[-1][0] == "{" and self.stack[-1][2]
                self.string = []
            elif ch in "{[":
                self.stack.append([ch, None if ch == "{" else 0, ch == "{"])
            elif ch in "}]":
                self._end_literal(events)
                self.stack.pop()
                events.append(("end", self.path()))
            elif ch == ",":
                self._end_literal(events)
                frame = self.stack[-1]
                if frame[0] == "{":
                    frame[2] = True
                else:
                    frame[1] += 1
            elif ch == ":":
                pass
            elif ch.isspace():
                self._end_literal(events)
            else:
                self.literal += ch
        if piece and self.string is not None and not self.is_key:
            events.append(("text", self.path(), "".join(piece)))
        return events

    def _close_string(self, events: list, piece: list[str]) -> None:
        value = "".join(self.string or [])
        self.string = None
        if self.is_key:
            self.stack[-1][1], self.stack[-1][2] = value, False
            return
        if piece:
            events.append(("text", self.path(), "".join(piece)))
        events.append(("value", self.path(), value))

    def _end_literal(self, events: list) -> None:
        if self.literal:
            events.append(("value", self.path(), json.loads(self.literal)))
            self.literal = ""

    def _unescape(self) -> str | None:
        """The decoded character once the escape is complete; "" for the first half of a surrogate pair."""
        e = self.escape or ""
        if e[0] != "u":
            self.escape = None
            return ESCAPES.get(e, e)
        if len(e) < 5:
            return None
        self.escape = None
        code = int(e[1:], 16)
        if 0xD800 <= code < 0xDC00:
            self.high_surrogate = code
            return ""
        if 0xDC00 <= code < 0xE000 and self.high_surrogate is not None:
            code = 0x10000 + ((self.high_surrogate - 0xD800) << 10) + (code - 0xDC00)
        self.high_surrogate = None
        return chr(code)
