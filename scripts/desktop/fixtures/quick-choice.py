"""Disposable native window for the Computer quick-action acceptance check."""
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

window = Gtk.Window(title='Computer quick-action check')
window.set_default_size(640, 280)
window.connect('destroy', Gtk.main_quit)
box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
box.set_border_width(24)
window.add(box)
box.pack_start(Gtk.Label(label='Export the current report'), False, False, 0)
for label in ('Export CSV', 'Export PDF', 'Refresh report'):
    button = Gtk.Button(label=label)
    button.connect('clicked', lambda b, name=label: b.set_label({'Export CSV':'CSV exported', 'Export PDF':'PDF exported', 'Refresh report':'Report refreshed'}[name]))
    box.pack_start(button, False, False, 0)
window.show_all()
Gtk.main()
