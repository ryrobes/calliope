"""Bounded paging of an append-only terminal replay, including partial writes."""
import json


def read_page(path, offset=0, limit=128000):
    offset = max(0, int(offset))
    if not path.exists():
        return {'events': [], 'offset': offset, 'bytes': 0, 'more': False}
    size = path.stat().st_size
    if offset > size:
        raise ValueError('Terminal offset exceeds the recording')
    with path.open('rb') as stream:
        stream.seek(offset)
        raw = stream.read(limit)
    # Never consume an unfinished line; the next live poll reads it again.
    end = raw.rfind(b'\n') + 1
    if not end and len(raw) == limit:
        raise ValueError('Invalid terminal record size')
    records = [json.loads(line) for line in raw[:end].splitlines()]
    return {'events': records, 'offset': offset + end, 'bytes': size,
            'more': end > 0 and offset + end < size}
