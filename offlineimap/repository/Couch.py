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

from offlineimap.couchlib import Couch


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
        self.db_url = self.getconf("database")
        self.folders = None
        self.ui = getglobalui()

        self.connect()

    def connect(self):
        #print "connecting to database"

        self.couch = Couch(self.db_url, "mail")
        self.db = self.couch.db

        self.couch.record_type_base = "http://bbbsnowball.dyndns.org/couchdb/$$"

        #TODO I don't mind having a copy of every folder in the index, but for the mail_items that's too much
        #     duplication. We should only emit {"_rev": doc._rev} and use include_docs=True when we query the view.
        self.db.need_record_view("mail_folder", "mail", "mail_folders", "emit([doc.mailpath, doc.name], doc);")
        self.db.need_record_view("mail_item",   "mail", "mail_items",   "emit([doc.mailpath, doc.folder, doc.uid], doc);")


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
        # show folder creation in UI
        self.ui.makefolder(self, foldername)
        if self.account.dryrun:
            return

        # create folder in database
        record = self.db.create_record(
            mailpath    = self.mailpath,
            name        = foldername,
            record_type = "mail_folder")

        # put it into our cache
        if self.folders:
            self.folders.append(folder.Couch.CouchFolder(self.db, record, self))

    def deletefolder(self, foldername):
        # find folder with that name
        folder2 = self.getfolder(foldername)

        # remove from database
        del self.db[folder2.record["_id"]]

        #TODO We should remove all messages in that folder!

        # remove from cache
        self.folders.remove(folder2)

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

        results = self.db.view("mail/mail_folders")

        # we only want dirs with the right mailpath
        for rec in results[[self.mailpath]:[self.mailpath, {}]]:
            #print "got dir: " + rec.value["name"]
            retval.append(folder.Couch.CouchFolder(self.db, self.db.wrap_record(rec.value), self))

        return retval

    def getfolders(self):
        """Get all folders"""
        if self.folders is None:
            self.folders = self._load_folders()
        return self.folders

    def forgetfolders(self):
        """Forgets the cached list of folders, if any. Useful to run
        after a sync run."""
        self.folders = None
