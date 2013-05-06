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

import time
import calendar
import re
from .Base import BaseFolder
from threading import Lock

import base64

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
        super(CouchFolder, self).__init__(record.name, repository)

        self.db = db
        self.record = record
        self.messagelist = None

        self.mailpath = record.mailpath
        self.folder = record.name

        #"""infosep is the separator between maildir name and flag appendix"""
        #self.re_flagmatch = re.compile('%s2,(\w*)' % self.infosep)
        #self.ui is set in BaseFolder.init()
        # Everything up to the first comma or colon (or ! if Windows):
        #self.re_prefixmatch = re.compile('([^'+ self.infosep + ',]*)')
        #folder's md, so we can match with recorded file md5 for validity
        #self._foldermd5 = md5(self.getvisiblename()).hexdigest()

    def getroot(self):
        return self.mailpath

    def getname(self):
        return self.name

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

        results = self.db.view("mail/mail_items", include_docs = True)

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
            retval[rec.value["uid"]] = self.db.wrap_record(rec.value)

        return retval


    @staticmethod
    def _encode_time(rtime):
        # we use UTC in the database
        return rtime and time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(rtime))

    @staticmethod
    def _decode_time(rtime):
        # Maildir.getmessagetime returns the result of os.path.getmtime,
        # which is seconds since the epoch
        try:
            return rtime and calendar.timegm(time.strptime(rtime, "%Y-%m-%d %H:%M:%S"))
        except:
            print "ERROR: _decode_time failed for " + repr(rtime)
            raise

    @staticmethod
    def _encode_flags(flags):
        return reduce(lambda a,b: a+b, flags, "")

    @staticmethod
    def _decode_flags(flags):
        return set(flags)

    @staticmethod
    def _encode_text(text):
        return base64.b64encode(text)

    @staticmethod
    def _decode_text(text):
        return base64.b64decode(text)


    def cachemessagelist(self):
        if self.messagelist is None:
            self.messagelist = self._load_messages()

    def getmessagelist(self):
        # Unfortunately, there is no documentation about the interface
        # of folders, so I don't know, what I should return. Other
        # implementations return their internal representation which
        # is not the same...
        #
        # IMAP:        map of uid to {'uid': uid, 'flags': flags, 'time': rtime}
        # Maildir:     map of uid to {'flags': flags, 'filename': filepath}
        # UID mapper:  map of uid to {'uid': uid} + whatever the inner type returns
        # LocalStatus: map of uid to {'uid': uid, 'flags': flags, 'time': rtime}
        #
        # type of the fields:
        # - uid:   long
        # - flags: set of chars (1-byte strings)
        # - time:  seconds since epoch (float or whatever os.path.getmtime returns)
        #
        # I cannot find any piece of code that uses this function?!

        retval = {}
        for uid in self.messagelist:
            data = self.messagelist[uid]
            uid = long(uid)
            retval[uid] = {'uid': uid, 'flags': self._decode_flags(data['flags']), 'time': self._decode_time(data['time'])}
        return retval

    def uidexists(self, uid):
        """Returns True if uid exists"""
        return uid in self.messagelist

    def getmessageuidlist(self):
        """Gets a list of UIDs.
        You may have to call cachemessagelist() before calling this function!"""
        return self.messagelist.keys()

    def getmessagecount(self):
        """Gets the number of messages."""
        return len(self.messagelist)

    def getmessage(self, uid):
        """Return the content of the message"""
        return self._decode_text(self.messagelist[uid]['content64'])

    def getmessagetime(self, uid):
        return self._decode_time(self.messagelist[uid]['time'])

    def savemessage(self, uid, content, flags, rtime):
        """Writes a new message, with the specified uid.

        See folder/Base for detail. Note that savemessage() does not
        check against dryrun settings, so you need to ensure that
        savemessage is never called in a dryrun mode."""

        self.ui.savemessage('couch', uid, flags, self)

        #TODO what does that mean? (copied from Maildir implementation)
        if uid < 0:
            # We cannot assign a new uid.
            return uid

        #TODO we might get duplicate UIDs, if we save messages before the
        #     cache is initialized
        if self.messagelist is not None and uid in self.messagelist:
            # We already have it, just update flags.
            self.savemessageflags(uid, flags)
            return uid

        #TODO save message in a more useful format:
        #     1. text should go into an attachment
        #     2. text should be split into message text and mail attachments
        #        (so we can download them individually)
        #     3. headers should be available in the document,
        #        either as a list/map or most important ones in special fields:
        #        subject, from, to, CC&BCC (probably in to), time, ...

        x = {
            "mailpath"    : self.mailpath,
            "folder"      : self.folder,
            "uid"         : uid,
            # TODO simply fix encoding; simplejson tries to decode it
            #      from utf-8 which fails sometimes
            "content64"   : self._encode_text(content),
            "flags"       : self._encode_flags(flags),
            "time"        : self._encode_time(rtime),
            "record_type" : "mail_item"
        }

        record = self.db.create_record(x)

        if self.messagelist is not None:
            self.messagelist[uid] = record

        #print "saving message " + str(uid) + " to folder " + self.getname() + ": " + repr(self.messagelist)

        return uid

    def getmessageflags(self, uid):
        return self._decode_flags(self.messagelist[uid]['flags'])

    def savemessageflags(self, uid, flags):
        """Sets the specified message's flags to the given set.

        This function moves the message to the cur or new subdir,
        depending on the 'S'een flag.

        Note that this function does not check against dryrun settings,
        so you need to ensure that it is never called in a
        dryrun mode."""
        record = self.messagelist[uid]
        record.update({"flags" : self._encode_flags(flags)})

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
        # find ID in CouchDB
        if self.messagelist is None:
            self.cachemessagelist()
        _id = self.messagelist[uid]._id

        # delete in database and cache
        del self.db[_id]
        del self.messagelist[uid]
