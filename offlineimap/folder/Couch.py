# Maildir folder support
# Copyright (C) 2002 - 2013 Benjamin Koch
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

import socket
import time
import re
import os
from .Base import BaseFolder
from threading import Lock

from desktopcouch.records.server import CouchDatabase
from desktopcouch.records.record import Record as CouchRecord

import base64

try:
    from hashlib import md5
except ImportError:
    from md5 import md5

try:  # python 2.6 has set() built in
    set
except NameError:
    from sets import Set as set

from offlineimap import OfflineImapError

# Find the UID in a message filename
re_uidmatch = re.compile(',U=(\d+)')
# Find a numeric timestamp in a string (filename prefix)
re_timestampmatch = re.compile('(\d+)');

timeseq = 0
lasttime = 0
timelock = Lock()

def gettimeseq():
    global lasttime, timeseq, timelock
    timelock.acquire()
    try:
        thistime = long(time.time())
        if thistime == lasttime:
            timeseq += 1
            return (thistime, timeseq)
        else:
            lasttime = thistime
            timeseq = 0
            return (thistime, timeseq)
    finally:
        timelock.release()


class CouchFolder(BaseFolder):
    def __init__(self, db, record, repository):
        self.sep = "/"
        super(CouchFolder, self).__init__(record.value["name"], repository)

        self.db = db
        self.record = record
        self.messagelist = None

        self.mailpath = record.value["mailpath"]
        self.folder = record.value["name"]

        #"""infosep is the separator between maildir name and flag appendix"""
        #self.re_flagmatch = re.compile('%s2,(\w*)' % self.infosep)
        #self.ui is set in BaseFolder.init()
        # Everything up to the first comma or colon (or ! if Windows):
        #self.re_prefixmatch = re.compile('([^'+ self.infosep + ',]*)')
        #folder's md, so we can match with recorded file md5 for validity
        #self._foldermd5 = md5(self.getvisiblename()).hexdigest()

    def getroot(self):
        return self.record.value["mailpath"]

    def getname(self):
        return self.record.value["name"]

    def get_uidvalidity(self):
        """Retrieve the current connections UIDVALIDITY value

        Maildirs have no notion of uidvalidity, so we just return a magic
        token."""
        #TODO we could improve that for CouchDB, I think
        return 42

    def _load_messages(self):
        """Cache the message list from a Maildir.

        Maildir flags are: R (replied) S (seen) T (trashed) D (draft) F
        (flagged).
        :returns: dict that can be used as self.messagelist"""

        retval = {}

        results = self.db.execute_view("mail_items", "mail")

        #TODO use these...
        maxage = self.config.getdefaultint("Account " + self.accountname,
                                           "maxage", None)
        maxsize = self.config.getdefaultint("Account " + self.accountname,
                                            "maxsize", None)

        # we only want dirs with the right mailpath
        for rec in results[[self.mailpath, self.folder]:[self.mailpath, self.folder, {}]]:
            # check maxage/maxsize if this message should be considered
            #if maxage and not self._iswithinmaxage(filename, maxage):
            #    continue
            #if maxsize and (os.path.getsize(os.path.join(
            #            self.getfullname(), filepath)) > maxsize):
            #    continue
            retval[rec.value["uid"]] = self._decode(rec.value)

        return retval

    def cachemessagelist(self):
        if self.messagelist is None:
            self.messagelist = self._load_messages()

    def getmessagelist(self):
        return self.messagelist

    def getmessage(self, uid):
        """Return the content of the message"""
        return base64.b64encode(self.messagelist[uid]['content64'])

    def getmessagetime(self, uid):
        return self.messagelist[uid]['rtime']

    def _decode(self, record):
        """undo stuff we had to do to put the record into the database"""
        record["flags"] = set(record["flags"])
        if record["rtime"]:
            record["rtime"] = time.strptime(record["time"], "%Y-%m-%d %H:%M:%S")

    def savemessage(self, uid, content, flags, rtime):
        """Writes a new message, with the specified uid.

        See folder/Base for detail. Note that savemessage() does not
        check against dryrun settings, so you need to ensure that
        savemessage is never called in a dryrun mode."""
        # This function only ever saves to tmp/,
        # but it calls savemessageflags() to actually save to cur/ or new/.
        self.ui.savemessage('couch', uid, flags, self)
        if uid < 0:
            # We cannot assign a new uid.
            return uid

        if uid in self.messagelist:
            # We already have it, just update flags.
            self.savemessageflags(uid, flags)
            return uid

        x = {
            "mailpath"  : self.mailpath,
            "folder"    : self.folder,
            "uid"       : uid,
            "content64" : base64.b64encode(content),     # TODO simply fix encoding; simplejson tries to decode it from utf-8 which fails sometimes
            "flags"     : reduce(lambda a,b: a+b, flags, ""),
            "rtime"     : rtime and time.strftime("%Y-%m-%d %H:%M:%S", rtime)     # TODO
        }
        #print repr(x)

        record = CouchRecord(x, "http://bbbsnowball.dyndns.org/couchdb/mail_item")
        couch_id = self.db.put_record(record)

        if self.messagelist:
            self.messagelist[uid] = record

        return uid

    def getmessageflags(self, uid):
        return self.messagelist[uid]['flags']

    def savemessageflags(self, uid, flags):
        """Sets the specified message's flags to the given set.

        This function moves the message to the cur or new subdir,
        depending on the 'S'een flag.

        Note that this function does not check against dryrun settings,
        so you need to ensure that it is never called in a
        dryrun mode."""
        record = self.messagelist[uid]
        record.update({"flags" : flags})

    def change_message_uid(self, uid, new_uid):
        """Change the message from existing uid to new_uid

        This will not update the statusfolder UID, you need to do that yourself.
        :param new_uid: (optional) If given, the old UID will be changed
            to a new UID. The Maildir backend can implement this as an efficient
            rename."""
        if not uid in self.messagelist:
            raise OfflineImapError("Cannot change unknown Couch UID %s" % uid)
        if uid == new_uid: return

        #raise OfflineImapError("Cannot change UID in CouchDB")

        record = self.messagelist[uid]
        record.update({"uid" : new_uid})

        del(self.messagelist[uid])
        self.messagelist[new_uid] = record
        
    def deletemessage(self, uid):
        """Unlinks a message file from the Maildir.

        :param uid: UID of a mail message
        :type uid: String
        :return: Nothing, or an Exception if UID but no corresponding file
                 found.
        """
        self.db.delete_record(uid)
        if self.messagelist:
            del(self.messagelist[uid])
