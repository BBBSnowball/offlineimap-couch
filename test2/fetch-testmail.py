#!/usr/bin/python

# This script fetches messages from gmane and converts
# it from NNTP into something that looks enough like
# maildir. Mutt is used for the conversion.
# idea: http://wiki.thorx.net/wiki/MailNews

# aptitude install debconf-utils
# debconf-set-selections \
#   <(echo "slrnpull        slrnpull/run_from       select  manually")
# aptitude install slrnpull mutt socat
# rm /etc/cron.daily/slrnpull

import sys
import os
import os.path
pjoin = os.path.join

basedir = "/root/testmail"
server = "news.gmane.org"
groups = {
    "gmane/mail/imap/offlineimap/general":
        "gmane.mail.imap.offlineimap.general",
    "oi-news": "gwene.org.offlineimap.news",
    "ifile/devel": "gmane.mail.ifile.devel",
    "ilohamail/devel": "gmane.mail.ilohamail.devel",
    "ilohamail/testers": "gmane.mail.ilohamail.testers",
    "ilohamail/translators": "gmane.mail.ilohamail.translators",
    "ilohamail/users": "gmane.mail.ilohamail.users",
    "gmane_mail_im2000": "gmane.mail.im2000"
    }

imap_groups = """
gmane.mail.imap.courier.general
gmane.mail.imap.offlineimap.subversion
gmane.mail.imap.offlineimap.general
gmane.mail.imap.cyrus
gmane.mail.imap.cyrus.announce
gmane.mail.imap.binc.general
gmane.mail.imap.dovecot
gmane.mail.imap.binc.devel
gmane.mail.imap.dbmail
gmane.mail.imap.dbmail.devel
gmane.mail.imap.binc.announce
gmane.mail.imap.general
gmane.mail.imap.uw.c-client
gmane.mail.imap.cyrus.web-cyradm
gmane.mail.imap.imapfilter.devel
gmane.mail.imap.courier.server
gmane.mail.imap.courier.sqwebmail
gmane.mail.imap.isync.devel
gmane.mail.imap.openmailadmin
gmane.mail.imap.feed2imap.devel
gmane.mail.imap.aox.user
"""
for g in imap_groups.split("\n"):
    g = g.strip()
    if g != "":
        groups[g.replace(".", "/")] = g


# -> 04/07/2013 03:28:18 A total of 396865788 bytes received,
#    1243867 bytes sent in 1411 seconds.

maildir = pjoin(basedir, "maildir")


def mkdir_p(path):
    if not os.path.exists(path):
        os.makedirs(path)
mkdir_p(basedir)

# write configuration for slrnpull
f = open(pjoin(basedir, "slrnpull.conf"), 'w')
f.write("default 5000 0 0\n")
for group in groups:
    f.write("%s 5000 0 0\n" % groups[group])
f.close()

print "Running slrnpull..."
cmd = "slrnpull -d '%s' -h '%s' --no-post" % (basedir, server)
print "$ " + cmd
sys.stdout.flush()
# if you want to see the progress, run this: tail -f /var/log/news/slrnpull.log
os.system(cmd)

mkdir_p(maildir)

for group in groups:
    for dir in ["cur", "tmp"]:
        mkdir_p(pjoin(maildir, group, dir))

    # NNTP stuff goes into 'new' directory
    #NOTE We don't copy it although the files will be moved. slrnpull
    #     won't download them again.
    nntp_dir = pjoin(basedir, "news", groups[group].replace(".", "/"))
    new_dir = pjoin(maildir, group, "new")
    #print "%s -> %s" % (nntp_dir, new_dir)
    if not os.path.exists(new_dir):
        os.symlink(nntp_dir, new_dir)

    # call mutt to put mails into cur
    # We use socat because mutt needs a tty. We use STDIN, so
    # mutt's output doesn't garble our terminal. However, this means
    # that we won't see any output. We can use STDIO to get output,
    # as well.
    #TODO sometimes group is "", so mutt removes all the news folders...
    print "Running Mutt on '%s'..." % pjoin(maildir, group)
    cmd = ("echo q | TERM=xterm socat STDIN EXEC:\"mutt -n"
        + " -e \\'set mbox_type=Maildir\\'"
        + " -e \\'set folder=%s\\' -e \\'set mbox=+\\' -e \\'unmailboxes *\\'"
        + " -e \\'mailboxes ! +%s\\' -f %s\",pty,setsid,ctty")
    cmd = cmd % (basedir, group, pjoin(maildir, group))
    #print cmd
    sys.stdout.flush()
    os.system(cmd)
