# Maildir repository support
# Copyright (C) 2013 Benjamin Koch
# <bbbsnowball@gmail.com>
#
#    This program is free software; you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation; either version 2 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program; if not, write to the Free Software
#    Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301 USA

from offlineimap import folder
from offlineimap.ui import getglobalui
from offlineimap.error import OfflineImapError
from offlineimap.repository.Base import BaseRepository
from stat import *

from desktopcouch.records.server import CouchDatabase
from desktopcouch.records.record import Record as CouchRecord


# quick-fixing bug in couchdb-python
# http://code.google.com/p/couchdb-python/issues/detail?id=209
import time
from email.Utils import parsedate
from datetime import datetime
import couchdb
def cache_sort(i):
    t = time.mktime(parsedate(i[1][1]['Date']))
    return datetime.fromtimestamp(t)
# And monkey-patch cache_sort function:
couchdb.http.cache_sort = cache_sort


class CouchRepository(BaseRepository):
    def __init__(self, reposname, account):
        """Initialize a MaildirRepository object.  Takes a path name
        to the directory holding all the Maildir directories."""
        BaseRepository.__init__(self, reposname, account)

        self.mailpath = self.getconf("mailpath", reposname)
        self.folders = None
        self.ui = getglobalui()

        self.connect()
        print "blub"

    def connect(self):
        print "connecting to database"

        self.db = CouchDatabase("mail", create=True)

        # delete existing data
        #self.db.server.delete("mail")
        #self.db.server.create("mail")

        #TODO use the official way to create the views
        viewfn = 'function(doc) { if (doc.record_type == "http://bbbsnowball.dyndns.org/couchdb/mail_folder") emit([doc.mailpath, doc.name], doc); }'
        self._need_view("mail", "mail_folders", viewfn)
        viewfn = 'function(doc) { if (doc.record_type == "http://bbbsnowball.dyndns.org/couchdb/mail_item") emit([doc.mailpath, doc.folder, doc.uid], doc); }'
        self._need_view("mail", "mail_items", viewfn)


    def _need_view(self, dbname, viewname, viewcode):
        if not self.db.view_exists(viewname, dbname):
            self.db.add_view(viewname, viewcode, None, dbname)

    def debug(self, msg):
        self.ui.debug('couchdb', msg)
    def info(self, msg):
        self.ui.info('couchdb: ' + msg)
    def warn(self, msg):
        self.ui.warn('couchdb: ' + msg)
    def error(self, msg):
        self.ui.error('couchdb: ' + msg)

    def getsep(self):
        return "/"

    def makefolder(self, foldername):
        """Create new Maildir folder if necessary

        This will not update the list cached in getfolders(). You will
        need to invoke :meth:`forgetfolders` to force new caching when
        you are done creating folders yourself.

        :param foldername: A relative mailbox name. The maildir will be
            created in self.root+'/'+foldername. All intermediate folder
            levels will be created if they do not exist yet. 'cur',
            'tmp', and 'new' subfolders will be created in the maildir.
        """
        self.ui.makefolder(self, foldername)
        if self.account.dryrun:
            return

        record = CouchRecord({
            'mailpath': self.mailpath,
            'name': foldername
        }, "http://bbbsnowball.dyndns.org/couchdb/mail_folder")
        self.db.put_record(record)

        if self.folders:
            self.folders.append(CouchFolder(record))

    def deletefolder(self, foldername):
        #TODO
        self.ui.warn("NOT YET IMPLEMENTED: DELETE FOLDER %s" % foldername)

    def getfolder(self, foldername):
        """Return a Folder instance of this Maildir

        If necessary, scan and cache all foldernames to make sure that
        we only return existing folders and that 2 calls with the same
        name will return the same object."""
        # getfolders() will scan and cache the values *if* necessary
        folders = self.getfolders()
        for folder2 in folders:
            if foldername == folder2.name:
                return folder2
        raise OfflineImapError("getfolder() asked for a nonexisting "
                               "folder '%s'." % foldername,
                               OfflineImapError.ERROR.FOLDER)

    def _load_folders(self):
        """Recursively scan folder 'root'; return a list of MailDirFolder

        :param root: (absolute) path to Maildir root
        :param extension: (relative) subfolder to examine within root"""

        retval = []

        results = self.db.execute_view("mail_folders", "mail")

        # we only want dirs with the right mailpath
        for rec in results[[self.mailpath]:[self.mailpath, {}]]:
            print "got dir: " + rec.value["name"]
            retval.append(folder.Couch.CouchFolder(self.db, rec, self))

            # filter out the folder?
            foldername = rec.value["name"]
            if not self.folderfilter(foldername):
                self.debug("Filtering out '%s'[%s] due to folderfilter"
                           % (foldername, self))
                retval[-1].sync_this = False
            else:
                retval[-1].sync_this = True

        return retval

    def getfolders(self):
        if self.folders == None:
            self.folders = self._load_folders()
        return self.folders

    def forgetfolders(self):
        """Forgets the cached list of folders, if any.  Useful to run
        after a sync run."""
        self.folders = None
