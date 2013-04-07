from . import Base, Gmail, IMAP, Maildir, LocalStatus

# only import CouchDB backend, if DesktopCouch is available
couchdb_available = False
try:
    import desktopcouch.records.server
    couchdb_available = True
except ImportError:
    pass
if couchdb_available:
    from . import Couch
