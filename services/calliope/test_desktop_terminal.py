import json
import pytest
from desktop_terminal import read_page


def test_utf8_paging_waits_for_complete_lines(tmp_path):
    p = tmp_path / 'terminal.cast'
    first = json.dumps({'version': 2}).encode() + b'\n'
    next_record = json.dumps([.5, 'o', 'café'], ensure_ascii=False).encode() + b'\n'
    p.write_bytes(first + next_record[:-3])
    page = read_page(p)
    assert page['offset'] == len(first)
    assert page['events'] == [{'version': 2}]
    pending = read_page(p, page['offset'])
    assert pending['events'] == [] and not pending['more']
    p.write_bytes(first + next_record)
    complete = read_page(p, page['offset'])
    assert complete['events'] == [[.5, 'o', 'café']]
    assert complete['offset'] == p.stat().st_size
    with pytest.raises(ValueError, match='exceeds'):
        read_page(p, p.stat().st_size + 1)


def test_pages_are_bounded_without_losing_a_record(tmp_path):
    p = tmp_path / 'terminal.cast'
    records = [[i, 'o', 'line ' + str(i)] for i in range(200)]
    p.write_text(''.join(json.dumps(row)+'\n' for row in records))
    result, offset = [], 0
    while True:
        page = read_page(p, offset, 128)
        result.extend(page['events']); offset = page['offset']
        if not page['more']:
            break
    assert result == records
