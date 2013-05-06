# -*- coding: utf-8 -*-

# python -m unittest test2.tests.test_03_move_mails

import unittest
import tempfile
import shutil
import logging
import os
import os.path
import re
import subprocess
import sys
import time
import imaplib

from offlineimap.couchlib import Couch
from offlineimap.accounts import SyncableAccount
from offlineimap.CustomConfig import CustomConfigParser
from offlineimap.repository import Repository
from offlineimap.ui import Noninteractive, setglobalui


#suite = "testmail"          # about 50000 mails, 500MB, Dovecot->Maildir takes 5000 seconds
#suite = "testmail-small"    # about  1500 mails,  14MB, Dovecot->Maildir takes   75 seconds
suite = "testmail-micro"     # about   330 mails,   3MB, Dovecot->Couch   takes   20 seconds


couch = None
tmpdir = None
couch_debug = None

def setUpModule():
    logging.info("Set Up test module %s" % __name__)

    global tmpdir
    tmpdir = tempfile.mkdtemp("", "offlineimap_couch_test_")

    # start temporary CouchDB instance
    global couch
    #couch = Couch("tmp://test_couch_move_mails")
    couch = Couch("file://" + tmpdir + "/couch")

    global couch_debug
    couch_debug = couch.debug_url()
    couch_debug.__enter__()
    print "========================="
    print "CouchDB for this test: "
    print couch_debug.futon_url()
    print "========================="

    # set UI
    # We only do that once because it prints a banner.
    config = CustomConfigParser()
    Noninteractive.Basic.print_banner = lambda self: None
    setglobalui(Noninteractive.Basic(config, logging.WARN))

def tearDownModule():
    logging.info("Tear Down test module %s" % __name__)

    global couch
    couch.mycouch.shutdown()

    global tmpdir
    shutil.rmtree(tmpdir)

    global couch_debug
    # it wants (type, value, traceback), but it
    # doesn't use them anyway
    couch_debug.__exit__(None, None, None)

    # delete testmail accounts
    for acc in ["test_02_couch_to_dovecot"]:
        try:
            path = "/var/mail/" + acc
            if os.path.isdir(path):
                shutil.rmtree(path)
        except:
            #NOTE We couldn't remove the data, but at the start of the next test run
            #     it will be removed via IMAP. However, this is likely a lot slower.
            print "WARN: error while cleaning up (deleting) %s: %s" % (path, sys.exc_info()[1])

class TestMoveMailsToCouch(unittest.TestCase):
    #NOTE The tests must be run in order!

    def setUp(self):
        global tmpdir, couch, suite

        self.tmpdir = tmpdir
        self.couch_url = couch.mycouch.uri
        self.suite = suite

    def tearDown(self):
        pass


    def debug_stop(self):
        global couch
        with couch.debug_url() as url:
            print "====================="
            print "     DEBUG STOP      "
            print "====================="
            print "You can access Futon using this URL:"
            print url.futon_url()
            print ""
            print "Please press ENTER to continue."
            sys.stdin.readline()


    def tmp_file_path(self, path):
        return os.path.join(self.tmpdir, path)

    def write_file(self, path, content):
        with open(self.tmp_file_path(path), "w") as f:
            f.write(content)

    def write_config(self, name, content, **variables):
        # remove indentation
        m = re.match("\\A\r?\n?([ \t]+)", content)
        if m:
            indent = m.group(1)
            #print "indent: " + repr(indent)
            content = re.sub("^"+indent, "", content, 0, re.MULTILINE)
        # substitute tmpdir
        variables["tempdir"] = self.tmpdir
        variables["tmpdir"]  = self.tmpdir
        variables["couch"]   = self.couch_url
        variables["suite"]   = self.suite
        content = re.sub("\\$([a-z0-9A-Z]+)|\\$\\{([a-z0-9A-Z]+)\\}", lambda m: variables[m.group(1) or m.group(2)], content)
        # write it
        self.write_file(name, content)

    def run_offlineimap(self, config):
        # go to root directory of offlineimap
        oi_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        os.chdir(oi_root)

        # config is in our temporary directory
        config = os.path.join(self.tmpdir, config)

        # start offlineimap
        print "Running offlineimap with configuration in '%s'..." % config
        try:
            time1 = time.time()
            subprocess.check_call(["python", os.path.join(oi_root, "offlineimap.py"), "-c" + config], shell=False)
            time2 = time.time()
            print "finished after %0.2f seconds" % (time2-time1)
        except:
            print "Offlineimap failed for this configuration:"
            with open(config) as f:
                print f.read()
            print "END OF CONFIG"

            raise

    def load_config(self, configfile):
        # get full path of config file
        configfile = self.tmp_file_path(configfile)

        # check that it exists because CustomConfigParser will
        # ignore absent files
        if not os.path.isfile(configfile):
            raise Exception("Config file %s doesn't exist" % configfile)

        config = CustomConfigParser()
        config.read(configfile)
        config.set_if_not_exists('general','dry-run','False')

        #config.write(sys.stdout)

        return config

    def make_account_for_config(self, configfile, account_name):
        config = self.load_config(configfile)

        acc = SyncableAccount(config, account_name)

        # create the repositories
        # This usually happens in method syncrunner, but we need it here
        # although we don't want to sync (thus we cannot call syncrunner).
        acc.remoterepos = Repository(acc, 'remote')
        acc.localrepos  = Repository(acc, 'local')
        acc.statusrepos = Repository(acc, 'status')     # we probably don't need this one

        return acc

    def delete_all_folders(self, configfile, reponame=None):
        """Delete all folders on the remote IMAP repository"""
        config = self.load_config(configfile)
        if reponame:
            sections = ['Repository {0}'.format(reponame)]
        else:
            sections = [r for r in config.sections() \
                            if r.startswith('Repository')]
            sections = filter(lambda s: \
                                  config.get(s, 'Type').lower() == 'imap',
                              sections)
        for sec in sections:
            # Connect to each IMAP repo and delete all folders
            # matching the folderfilter setting. We only allow basic
            # settings and no fancy password getting here...
            # 1) connect and get dir listing
            host = config.get(sec, 'remotehost')
            user = config.get(sec, 'remoteuser')
            passwd = config.get(sec, 'remotepass')
            imapobj = imaplib.IMAP4(host)
            imapobj.login(user, passwd)
            res_t, data = imapobj.list()
            assert res_t == 'OK'
            dirs = []
            for d in data:
                m = re.search(br'''        # Find last quote
                    "((?:                 # Non-tripple quoted can contain...
                    [^"]                | # a non-quote
                    \\"                   # a backslashded quote
                    )*)"                   # closing quote
                    [^"]*$                # followed by no more quotes
                    ''', d, flags=re.VERBOSE)
                folder = bytearray(m.group(1))
                #folder = folder.replace(br'\"', b'"') # remove quoting
                dirs.append(folder)
            dirs = [d for d in dirs if d != "INBOX"]
            if len(dirs) > 0:
                dirnames = ", ".join(map(lambda d: str(d), dirs))
                print "WARN: Some directories in %s@%s are left from a previous test run: %s" % (user, host, dirnames)
            for folder in dirs:
                res_t, data = imapobj.delete(b'\"'+folder+b'\"')
                assert res_t == 'OK', "Folder deletion of {0} failed with error"\
                    ":\n{1} {2}".format(folder.decode('utf-8'), res_t, data)
            imapobj.logout()

    def assertSortedListEqual(self, list1, list2):
        list1.sort()
        list2.sort()
        return self.assertListEqual(list1, list2)

    def assertReposAreEqual(self, repo1, repo2):
        repo1.connect()
        repo2.connect()

        try:
            # get list of folders
            folders1 = repo1.getfolders()
            folders2 = repo2.getfolders()

            # sort them
            folders1.sort(key = lambda x: x.getname())
            folders2.sort(key = lambda x: x.getname())

            # compare folder list (by name)
            # We have to fix the separator ("." for IMAP, "/" for Couch)
            sep1 = repo1.getsep()
            sep2 = repo2.getsep()
            fn1 = map(lambda f: f.getname(), folders1)
            fn2 = map(lambda f: f.getname().replace(sep2, sep1), folders2)
            self.assertSortedListEqual(fn1, fn2)

            # The folders are in the same order because we have sorted them. If
            # both repos have the same folders (which we have also tested), we
            # get matching folders here.
            for folder1,folder2 in zip(folders1,folders2):
                # just to be sure
                self.assertEquals(folder1.getname(), folder2.getname().replace(sep2, sep1))

                # load message list
                folder1.cachemessagelist()
                folder2.cachemessagelist()

                try:
                    # get uids to make sure we have the same set of messages
                    #NOTE We now that we are dealing with Couch
                    #     which preserves uids. We also rely on
                    #     the IMAP server to preserve uids.
                    uid1 = folder1.getmessageuidlist()
                    uid2 = folder2.getmessageuidlist()
                    self.assertSortedListEqual(uid1, uid2)

                    # compare message contents
                    for uid in uid1:
                        self.assertEqual(folder1.getmessageflags(uid), folder2.getmessageflags(uid))
                        self.assertEqual(folder1.getmessagetime(uid), folder2.getmessagetime(uid))
                        self.assertEqual(folder1.getmessage(uid), folder2.getmessage(uid))
                except AssertionError as e:
                    text = e.args[0]
                    text += "\n\nwhile comparing folder %s of %s and %s" % (folder1.getname(), repo1.getname(), repo2.getname())
                    e.args = (text,)
                    raise
        finally:
            repo1.dropconnections()
            repo2.dropconnections()


    def test_01_dovecot_to_couch(self):
        """
        store mail data in Couch

        It's not a 'real' test of initial sync because the remote
        repo is read-only to protect our test data.
        """

        config_file = "copy_testdata_to_couch.conf"
        self.write_config(config_file, """
            [general]
            metadata = $tmpdir/copy_testdata_to_couch
            accounts = acc
            ui = quiet
            #ui = basic

            [Account acc]
            localrepository = local
            remoterepository = remote

            [Repository local]
            type = Couch
            database = $couch#mails1

            [Repository remote]
            type = IMAP
            remotehost = localhost
            remoteuser = ${suite}
            remotepass = test
            readonly = true
            """)

        self.run_offlineimap(config_file)

        #NOTE In this test, we only check that we can sync. The
        #     data will be compared in test_02.

    def test_02_couch_to_dovecot(self):
        """test initial sync from Couch to IMAP and vice versa"""

        # Couch to IMAP - that's what you usually don't use
        config_file = "sync_couch_to_dovecot.conf"
        self.write_config(config_file, """
            [general]
            metadata = $tmpdir/sync_couch_to_dovecot
            accounts = acc
            ui = quiet

            [Account acc]
            localrepository = local
            remoterepository = remote

            [Repository local]
            type = Couch
            database = $couch#mails1

            [Repository remote]
            type = IMAP
            remotehost = localhost
            remoteuser = test_02_couch_to_dovecot
            remotepass = test
            """)

        # make sure the remote account doesn't have any mails
        self.delete_all_folders(config_file, "remote")

        self.run_offlineimap(config_file)

        # IMAP to Couch - that's what we usually have "in the wild"
        config_file2 = "sync_dovecot_to_couch.conf"
        self.write_config(config_file2, """
            [general]
            metadata = $tmpdir/sync_dovecot_to_couch
            accounts = acc
            ui = quiet

            [Account acc]
            localrepository = local
            remoterepository = remote

            [Repository local]
            type = Couch
            database = $couch#mails2

            [Repository remote]
            type = IMAP
            remotehost = localhost
            remoteuser = test_02_couch_to_dovecot
            remotepass = test
            """)

        self.run_offlineimap(config_file2)

        # compare Couch repos and remote repos
        acc1 = self.make_account_for_config(config_file,  "acc")
        acc2 = self.make_account_for_config(config_file2, "acc")
        accR = self.make_account_for_config("copy_testdata_to_couch.conf", "acc")

        # readonly testdata on Dovecot
        testdata = accR.remoterepos

        # two Couch repositories
        couch1 = acc1.localrepos
        couch2 = acc2.localrepos

        # mutable IMAP on Dovecot - used by both tests
        imap = acc1.remoterepos

        # compare the folders that were synced directly, so we can
        # pinpoint an error to a particular sync
        self.assertReposAreEqual(testdata, couch1)
        self.assertReposAreEqual(couch1, imap)
        self.assertReposAreEqual(imap, couch2)

        # compare IMAP folders (synced indirectly via couch1)
        self.assertReposAreEqual(testdata, imap)

        # compare Couch folders
        self.assertReposAreEqual(couch1, couch2)

    def test_03_sync(self):
        # use same config file as test_02
        config_file = "sync_couch_to_dovecot.conf"

        raise "TODO"
