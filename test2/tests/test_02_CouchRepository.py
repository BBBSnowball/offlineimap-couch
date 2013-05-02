# -*- coding: utf-8 -*-

# python -m unittest test2.tests.test_02_CouchRepository

import unittest
import tempfile
import shutil
import logging
import time

from offlineimap.ui import Noninteractive, setglobalui
from offlineimap.CustomConfig import CustomConfigParser, ConfigHelperMixin
from offlineimap.repository import CouchRepository
from offlineimap import OfflineImapError

from offlineimap.couchlib import Couch

class FakeAccount(ConfigHelperMixin):
    def __init__(self, config, name, dryrun):
        self.config    = config
        self.name      = name
        self.localeval = config.getlocaleval()
        self.dryrun    = dryrun

    def getlocaleval(self):
        return self.localeval

    def getconfig(self):
        return self.config

    def getname(self):
        return self.name

    def __str__(self):
        return self.name

    def getsection(self):
        return 'Account ' + self.getname()

def setUpModule():
    logging.info("Set Up test module %s" % __name__)

    # set UI
    # We only do that once because it prints a banner.
    config = CustomConfigParser()
    setglobalui(Noninteractive.Basic(config, logging.INFO))

    # start temporary CouchDB instance
    global couch
    couch = Couch("tmp://test_couch_repo")

def tearDownModule():
    logging.info("Tear Down test module %s" % __name__)

    global couch
    couch.mycouch.shutdown()


class TestCouchRepository(unittest.TestCase):

    def createAccount(self, reset_data = False, dryrun = False):
        global couch
        self.couch = couch

        # We need to initialize quite a lot of things
        # to make Repositories work well...

        # use our temporary CouchDB instance
        dbname = "some_mail"
        database = self.couch.mycouch.uri + "#" + dbname
        mailpath = "my-mail"
        couch_repo_name = "couch"

        # delete existing database (if caller wants us to do so)
        if reset_data and dbname in self.couch.server:
            del self.couch.server[dbname]

        # create a minimal config object
        config = CustomConfigParser()
        config.add_section('Repository '+couch_repo_name)
        config.set('Repository '+couch_repo_name, "mailpath", mailpath)
        config.set('Repository '+couch_repo_name, "database", database)

        metadata = tempfile.mkdtemp("", "offlineimap_meta_")
        self.dirs_to_clean.append(metadata)
        config.add_section("general")
        config.set("general", "metadata", metadata)

        # create fake account object
        self.account = FakeAccount(config, "fake", dryrun)

        # create the repository
        self.repo = CouchRepository(couch_repo_name, self.account)

        return self.repo

    def setUp(self):
        self.dirs_to_clean = []
        try:
            self.createAccount(reset_data = True)
        except:
            # exceptions in setUp() don't trigger a call
            # to tearDown, so we call it ourselves
            self.tearDown()
            raise

    def tearDown(self):
        self.account = None
        self.couch = None
        self.repo = None

        #print "cleaning: " + repr(self.dirs_to_clean)
        for x in self.dirs_to_clean:
            shutil.rmtree(x)
        self.dirs_to_clean = []

    def debug_stop(self):
        import sys

        with self.couch.debug_url() as url:
            print "====================="
            print "     DEBUG STOP      "
            print "====================="
            print "You can access Futon using this URL:"
            print url.futon_url()
            print ""
            print "Please press ENTER to continue."
            sys.stdin.readline()

    def test_repository(self):
        repo = self.repo

        # create folder without cache
        repo.makefolder("abc")
        repo.makefolder("def")
        repo.makefolder("ghi")

        self.assertEqual("def", repo.getfolder("def").getname())
        self.assertEquals(3, len(repo.getfolders()))

        # create folder with hot cache
        repo.makefolder("jkl")

        self.assertEquals("jkl", repo.getfolder("jkl").getname())

        # delete folder with hot cache
        repo.deletefolder("def")

        self.assertRaises(OfflineImapError, lambda: repo.getfolder("def"))
        self.assertEquals(3, len(repo.getfolders()))

        # delete folder with cold cache
        repo.forgetfolders()
        repo.deletefolder("ghi")

        self.assertRaises(OfflineImapError, lambda: repo.getfolder("ghi"))
        self.assertEquals(2, len(repo.getfolders()))

        # now we are down to two folders
        # Recreate the accounts object, so we can be sure that
        # the folders really come from the database.
        self.createAccount(reset_data = False)

        self.assertEquals(2, len(repo.getfolders()))
        fnames = map(lambda f: f.getname(), repo.getfolders())
        fnames.sort()
        self.assertListEqual(["abc", "jkl"], fnames)

    def test_folder(self):
        repo = self.repo

        # create folder without cache
        repo.makefolder("abc")
        repo.makefolder("def")
        repo.makefolder("ghi")

        f1 = repo.getfolder("abc")
        f2 = repo.getfolder("def")
        f3 = repo.getfolder("ghi")

        self.assertEquals("my-mail", f1.getroot())
        self.assertEquals("my-mail", f2.getroot())
        self.assertEquals("my-mail", f3.getroot())

        self.assertEquals("abc", f1.getname())
        self.assertEquals("def", f2.getname())
        self.assertEquals("ghi", f3.getname())

        #TODO this is the current behaviour, but I don't know
        #     whether it's right...
        self.assertEquals(42, f1.get_uidvalidity())

        time1 = time.strptime("2013-01-30 10:11:12", "%Y-%m-%d %H:%M:%S")
        time2 = time.strptime("1990-02-05 11:22:33", "%Y-%m-%d %H:%M:%S")
        time3 = time.strptime("2010-03-25 09:07:01", "%Y-%m-%d %H:%M:%S")

        #NOTE We use arbitrary message flags here because the storage layer supports it and I
        #     don't know which ones are valid ;-)
        messages = (
            ("From: someone@nowhere.gov\nTo: someone-else@nowhere.gov\n\nA very boring message", set(["A", "B", "C"]), time1),
            ("From: someone@nowhere.gov\nTo: someone-else@nowhere.gov\n\nAnother boring message", set(["A"]), time2),
            ("From: someone@nowhere.gov\nTo: someone-else@nowhere.gov\n\nHere are some very special characters for you: ä#+ßüö",
                set(["C"]), time3),
            ("From: A\nTo: B\n\nSome text", set(), time1),
        )

        x = f1.savemessage(13, *messages[0])
        f1.savemessage(14, *messages[1])
        f1.savemessage(15, *messages[2])

        # savemessage should have returned the UID
        self.assertEquals(13, x)

        # same UID, but different message and in different folder
        # -> shouldn't be a problem for the storage layer (but Offlineimap might be confused)
        f2.savemessage(14, *messages[0])

        # fill the cache
        f1.cachemessagelist()
        f2.cachemessagelist()
        f3.cachemessagelist()

        # add some more messages after filling the cache
        f2.savemessage(13, *messages[3])
        f3.savemessage(13, *messages[3])
        f3.savemessage(17, *messages[2])

        # current state (uid => message index)
        # f1: 13 => 0, 14 => 1, 15 => 2
        # f2: 13 => 3, 14 => 0
        # f3: 13 => 3, 17 => 2

        #TODO find out what getmessagelist should return and test it

        print repr(f3.getmessagelist())

        self.assertEquals(messages[0][0], f1.getmessage(13))
        self.assertEquals(messages[1][0], f1.getmessage(14))
        self.assertEquals(messages[2][0], f1.getmessage(15))
        self.assertEquals(messages[3][0], f2.getmessage(13))
        self.assertEquals(messages[0][0], f2.getmessage(14))
        self.assertEquals(messages[3][0], f3.getmessage(13))
        self.assertEquals(messages[2][0], f3.getmessage(17))

        self.assertEquals(messages[0][1], f1.getmessageflags(13))
        self.assertEquals(messages[1][1], f1.getmessageflags(14))
        self.assertEquals(messages[2][1], f1.getmessageflags(15))
        self.assertEquals(messages[3][1], f2.getmessageflags(13))
        self.assertEquals(messages[0][1], f2.getmessageflags(14))
        self.assertEquals(messages[3][1], f3.getmessageflags(13))
        self.assertEquals(messages[2][1], f3.getmessageflags(17))

        self.assertEquals(messages[0][2], f1.getmessagetime(13))
        self.assertEquals(messages[1][2], f1.getmessagetime(14))
        self.assertEquals(messages[2][2], f1.getmessagetime(15))
        self.assertEquals(messages[3][2], f2.getmessagetime(13))
        self.assertEquals(messages[0][2], f2.getmessagetime(14))
        self.assertEquals(messages[3][2], f3.getmessagetime(13))
        self.assertEquals(messages[2][2], f3.getmessagetime(17))

        # let's change some messages

        f1.savemessageflags(14, set(['X', 'A']))
        self.assertEquals(set(['X', 'A']), f1.getmessageflags(14))

        f1.savemessageflags(15, set(['Y', 'Z']))
        self.assertEquals(set(['Y', 'Z']), f1.getmessageflags(15))

        # change some UIDs

        # this one is easy
        f1.change_message_uid(13, 18)

        # this time we do it for a changed message and the UID
        # also exists in another folder
        f1.change_message_uid(14, 400)

        # lets change an UID twice
        f3.change_message_uid(13, 300)
        f3.change_message_uid(300, 7)

        # and give the old UIDs to some other messages
        f3.change_message_uid(17, 13)
        f3.savemessage(300, *messages[1])

        # and we check it again

        # current state (uid => message index)
        # f1: 18 => 0, 400 => 1, 15 => 2
        # f2: 13 => 3,  14 => 0
        # f3:  7 => 3,  13 => 2, 300 => 1
        # f1[400].flags is XA
        # f1[ 15].flags is YA

        self.assertEquals(messages[0][0], f1.getmessage( 18))
        self.assertEquals(messages[1][0], f1.getmessage(400))
        self.assertEquals(messages[2][0], f1.getmessage( 15))
        self.assertEquals(messages[3][0], f2.getmessage( 13))
        self.assertEquals(messages[0][0], f2.getmessage( 14))
        self.assertEquals(messages[3][0], f3.getmessage(  7))
        self.assertEquals(messages[2][0], f3.getmessage( 13))
        self.assertEquals(messages[1][0], f3.getmessage(300))

        self.assertEquals(messages[0][1], f1.getmessageflags( 18))
        self.assertEquals(set(['X', 'A']),f1.getmessageflags(400))
        self.assertEquals(set(['Y', 'Z']),f1.getmessageflags( 15))
        self.assertEquals(messages[3][1], f2.getmessageflags( 13))
        self.assertEquals(messages[0][1], f2.getmessageflags( 14))
        self.assertEquals(messages[3][1], f3.getmessageflags(  7))
        self.assertEquals(messages[2][1], f3.getmessageflags( 13))
        self.assertEquals(messages[1][1], f3.getmessageflags(300))

        self.assertEquals(messages[0][2], f1.getmessagetime( 18))
        self.assertEquals(messages[1][2], f1.getmessagetime(400))
        self.assertEquals(messages[2][2], f1.getmessagetime( 15))
        self.assertEquals(messages[3][2], f2.getmessagetime( 13))
        self.assertEquals(messages[0][2], f2.getmessagetime( 14))
        self.assertEquals(messages[3][2], f3.getmessagetime(  7))
        self.assertEquals(messages[2][2], f3.getmessagetime( 13))
        self.assertEquals(messages[1][2], f3.getmessagetime(300))


        # Recreate the accounts object, so we can be sure that
        # the folders really come from the database.
        self.createAccount(reset_data = False)

        # retrieve the folders
        f1 = repo.getfolder("abc")
        f2 = repo.getfolder("def")
        f3 = repo.getfolder("ghi")

        # load data
        f1.cachemessagelist()
        f2.cachemessagelist()
        f3.cachemessagelist()

        # data shouldn't have changed, so we run the same checks as before

        self.assertEquals(messages[0][0], f1.getmessage( 18))
        self.assertEquals(messages[1][0], f1.getmessage(400))
        self.assertEquals(messages[2][0], f1.getmessage( 15))
        self.assertEquals(messages[3][0], f2.getmessage( 13))
        self.assertEquals(messages[0][0], f2.getmessage( 14))
        self.assertEquals(messages[3][0], f3.getmessage(  7))
        self.assertEquals(messages[2][0], f3.getmessage( 13))
        self.assertEquals(messages[1][0], f3.getmessage(300))

        self.assertEquals(messages[0][1], f1.getmessageflags( 18))
        self.assertEquals(set(['X', 'A']),f1.getmessageflags(400))
        self.assertEquals(set(['Y', 'Z']),f1.getmessageflags( 15))
        self.assertEquals(messages[3][1], f2.getmessageflags( 13))
        self.assertEquals(messages[0][1], f2.getmessageflags( 14))
        self.assertEquals(messages[3][1], f3.getmessageflags(  7))
        self.assertEquals(messages[2][1], f3.getmessageflags( 13))
        self.assertEquals(messages[1][1], f3.getmessageflags(300))

        self.assertEquals(messages[0][2], f1.getmessagetime( 18))
        self.assertEquals(messages[1][2], f1.getmessagetime(400))
        self.assertEquals(messages[2][2], f1.getmessagetime( 15))
        self.assertEquals(messages[3][2], f2.getmessagetime( 13))
        self.assertEquals(messages[0][2], f2.getmessagetime( 14))
        self.assertEquals(messages[3][2], f3.getmessagetime(  7))
        self.assertEquals(messages[2][2], f3.getmessagetime( 13))
        self.assertEquals(messages[1][2], f3.getmessagetime(300))

        # delete some messages
        
        f1.deletemessage(15)    # changed flags
        f1.deletemessage(400)   # changed UID

        f3.change_message_uid(13, 700)
        f3.savemessageflags(300, set('D'))
        f3.deletemessage(700)   # recently changed UID
        f3.deletemessage(300)   # recently changed flags

        f2.deletemessage(13)    # all messages in folder
        f2.deletemessage(14)    # ^^

        # make sure the other messages are still fine

        self.assertEquals(messages[0][0], f1.getmessage(18))
        self.assertEquals(messages[3][0], f3.getmessage( 7))

        self.assertEquals(messages[0][1], f1.getmessageflags(18))
        self.assertEquals(messages[3][1], f3.getmessageflags( 7))

        self.assertEquals(messages[0][2], f1.getmessagetime(18))
        self.assertEquals(messages[3][2], f3.getmessagetime( 7))

        # test length of message list
        #TODO test this properly
        self.assertEquals(1, len(f1.getmessagelist()))
        self.assertEquals(0, len(f2.getmessagelist()))
        self.assertEquals(1, len(f3.getmessagelist()))


        # Again we recreate the accounts object and load the data
        self.createAccount(reset_data = False)

        # retrieve the folders
        f1 = repo.getfolder("abc")
        f2 = repo.getfolder("def")
        f3 = repo.getfolder("ghi")

        # load data
        f1.cachemessagelist()
        f2.cachemessagelist()
        f3.cachemessagelist()

        # same checks as before

        self.assertEquals(messages[0][0], f1.getmessage(18))
        self.assertEquals(messages[3][0], f3.getmessage( 7))

        self.assertEquals(messages[0][1], f1.getmessageflags(18))
        self.assertEquals(messages[3][1], f3.getmessageflags( 7))

        self.assertEquals(messages[0][2], f1.getmessagetime(18))
        self.assertEquals(messages[3][2], f3.getmessagetime( 7))

        # test length of message list
        #TODO test this properly
        self.assertEquals(1, len(f1.getmessagelist()))
        self.assertEquals(0, len(f2.getmessagelist()))
        self.assertEquals(1, len(f3.getmessagelist()))

def run():
    suite = unittest.TestLoader().loadTestsFromTestCase(TestCouchRepository)
    unittest.TextTestRunner(verbosity=2).run(suite)

if __name__ == "__main__":
    run()
