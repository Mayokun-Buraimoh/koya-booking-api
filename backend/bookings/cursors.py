"""Opaque paging cursor: base64 of the last `seq` the client has seen."""
import base64
import binascii

from .errors import BadRequest


def encode_cursor(seq):
    return base64.urlsafe_b64encode(f"seq:{seq}".encode()).decode()


def decode_cursor(raw):
    try:
        text = base64.urlsafe_b64decode(raw.encode()).decode()
        prefix, _, number = text.partition(":")
        if prefix != "seq" or not number.isdigit():
            raise ValueError
        return int(number)
    except (ValueError, binascii.Error, UnicodeError):
        raise BadRequest("invalid cursor")
