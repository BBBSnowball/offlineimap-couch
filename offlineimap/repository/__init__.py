# Copyright (C) 2002-2007 John Goerzen <jgoerzen@complete.org>
#               2010 Sebastian Spaeth <Sebastian@SSpaeth.de> and contributors
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

try:
    from configparser import NoSectionError
except ImportError:     # python2
    from ConfigParser import NoSectionError

from offlineimap.repository.IMAP import IMAPRepository, MappedIMAPRepository
from offlineimap.repository.Gmail import GmailRepository
from offlineimap.repository.Maildir import MaildirRepository
from offlineimap.repository.LocalStatus import LocalStatusRepository
from offlineimap.error import OfflineImapError
# we would like to import Couch, but that conflicts with the module Couch (the repository)
import offlineimap.couchlib as couchlib
from offlineimap.repository.Couch import CouchRepository


class Repository(object):
    """Abstract class that returns the correct Repository type
    instance based on 'account' and 'reqtype', e.g.  a
    class:`ImapRepository` instance."""

    def __new__(cls, account, reqtype):
        """
        :param account: :class:`Account`
        :param regtype: 'remote', 'local', or 'status'"""

        if reqtype == 'remote':
            name = account.getconf('remoterepository')
            # We don't support Maildirs on the remote side.
            typemap = {'IMAP': IMAPRepository,
                       'Gmail': GmailRepository}

        elif reqtype == 'local':
            name = account.getconf('localrepository')
            typemap = {'IMAP': MappedIMAPRepository,
                       'Maildir': MaildirRepository}

        elif reqtype == 'status':
            # create and return a LocalStatusRepository
            name = account.getconf('localrepository')
            return LocalStatusRepository(name, account)

        else:
            errstr = "Repository type %s not supported" % reqtype
            raise OfflineImapError(errstr, OfflineImapError.ERROR.REPO)

        if reqtype == 'local':
            if couchlib.Couch.available:
                typemap['Couch'] = CouchRepository

        # Get repository type
        config = account.getconfig()
        try:
            repostype = config.get('Repository ' + name, 'type').strip()
        except NoSectionError:
            errstr = ("Could not find section '%s' in configuration. Required "
                      "for account '%s'." % ('Repository %s' % name, account))
            raise OfflineImapError(errstr, OfflineImapError.ERROR.REPO)

        try:
            repo = typemap[repostype]
        except KeyError:
            errstr = "'%s' repository not supported for '%s' repositories." \
                     % (repostype, reqtype)
            raise OfflineImapError(errstr, OfflineImapError.ERROR.REPO)

        return repo(name, account)

    def __init__(self, account, reqtype):
        """Load the correct Repository type and return that. The
        __init__ of the corresponding Repository class will be
        executed instead of this stub

        :param account: :class:`Account`
        :param regtype: 'remote', 'local', or 'status'
        """
        pass
