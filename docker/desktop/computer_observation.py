"""Keep image bytes out of job logs and qualify accessibility observations."""
import base64
import os
from pathlib import Path
import uuid
from contextlib import contextmanager


@contextmanager
def unobstructed_capture(client, capture):
    """Temporarily hide our presentation overlay, never an application control."""
    enabled = False
    if capture and 'get_agent_cursor_state' in client.schemas:
        enabled = client.call('get_agent_cursor_state', timeout=3).get('enabled', False)
    try:
        if enabled:
            client.call('set_agent_cursor_enabled', {'enabled': False}, timeout=3)
        yield
    finally:
        if enabled:
            client.call('set_agent_cursor_enabled', {'enabled': True}, timeout=3)


def capture_path():
    # These files belong to the guest. look_at_desktop is the chat image path.
    root = Path(os.environ.get('CALLIOPE_COMPUTER_CAPTURE_DIR', '/tmp/calliope-computer-captures'))
    root.mkdir(parents=True, exist_ok=True)
    return root / (str(uuid.uuid4()) + '.png')


def prepare_capture(schema, args):
    props = schema.get('properties', {})
    if 'include_screenshot' in props:
        args.setdefault('include_screenshot', args.get('include_accessibility_tree') is False)
    wants_image = args.get('include_screenshot') or args.get('screenshot_out_file')
    if wants_image and 'screenshot_out_file' in props:
        args.setdefault('screenshot_out_file', str(capture_path()))
    return args.get('screenshot_out_file')


def normalize_result(result, args):
    def image_file(data, mime='image/png'):
        path = capture_path()
        if mime in ('image/jpeg', 'image/jpg'): path = path.with_suffix('.jpg')
        try:
            decoded = base64.b64decode(data, validate=True)
        except (ValueError, TypeError) as exc:
            raise RuntimeError('Driver returned invalid image data') from exc
        path.write_bytes(decoded)
        return str(path)
    def walk(value):
        if isinstance(value, list): return [walk(v) for v in value]
        if not isinstance(value, dict): return value
        if value.get('type') == 'image' and isinstance(value.get('data'), str):
            return {'type': 'image_file', 'path': image_file(value['data'], value.get('mimeType', 'image/png'))}
        obj = {k: walk(v) for k, v in value.items() if k != 'screenshot_png_b64'}
        if value.get('screenshot_png_b64'):
            obj['screenshot_file_path'] = image_file(value['screenshot_png_b64'], value.get('screenshot_mime_type', 'image/png'))
        return obj
    result = walk(result)
    if isinstance(result, dict) and result.get('effect') == 'unverifiable':
        result['effect_note'] = ('This action receipt does not establish the resulting UI state. '
                                 'It is not a failure verdict; use verify_state or a fresh observation to check the outcome. '
                                 'A new confirmation dialog is progress even if its parent remains; use cua.observe for dialog layers.')
    if isinstance(result, dict) and 'elements' in result:
        notes = ['Accessibility controls do not necessarily include all visible document, canvas, terminal or web content.',
                 'An empty query is not proof that content is absent. Use browser() for page text, shell output for command results, or look_at_desktop for visual gaps.']
        if 'terminal' in str(result.get('app_name', '')).lower():
            notes.insert(0, 'Terminal menus may be exposed while its scrollback text is not.')
        returned = len(result['elements'])
        total = result.get('total_element_count', result.get('element_count'))
        query = args.get('query')
        if query and not returned:
            summary = (f'No exposed controls matched {query!r} in the snapshot of {total} controls. '
                       'This does not prove the text or file is absent. Inspect files directly, browser() page text, or look_at_desktop.')
        elif query:
            summary = (f'Query {query!r} returned {returned} rows, including matching controls and their ancestors, '
                       f'from {total} controls in the snapshot before filtering.')
        else:
            summary = f'Returned {returned} exposed control rows from this accessibility snapshot.'
        if result.get('elements_complete') is False:
            summary += ' elements_complete=false means exhaustive accessibility coverage is not proven; it is not a query failure.'
        # Callers commonly print _note instead of the whole result. Put the
        # actionable explanation there as well as in structured coverage.
        if not returned or total == 1:
            summary += ' If a native dialog is visible, use cua.observe(pid=..., window_id=...) to inspect related layers instead of querying this sparse parent again.'
        result['summary'] = result['_note'] = summary
        result['coverage'] = {
            'source': 'accessibility', 'visible_content_complete': None,
            'elements_complete': result.get('elements_complete'),
            'query': query, 'returned_rows': returned, 'snapshot_controls_before_query': total,
            'query_result': 'no_exposed_match' if query and not returned else 'see_elements',
            'degraded': bool(result.get('degraded')), 'notes': notes,
        }
        limits = {k: args[k] for k in ('max_depth', 'max_elements') if k in args}
        if limits:
            result['coverage']['requested_limits'] = limits
            result['_note'] += ' This is a bounded tree; use map()/observe() for native controls, not progressively deeper document scans.'
    return result
