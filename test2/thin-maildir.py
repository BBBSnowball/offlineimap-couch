#!/usr/bin/python

#python /media/nfs/development/couch/tools/offlineimap/test2/thin-maildir.py maildir2 maildir4 0.1

import os
import os.path
import random
import shutil
import sys

random.seed(123)

def walk(source, target, keep_amount):
    print source

    # create the directory
    os.mkdir(target)

    # handle subdirs
    for entry in os.listdir(source):
        spath = os.path.join(source, entry)
        tpath = os.path.join(target, entry)
        if os.path.isfile(spath):
            # maybe copy it
            if random.random() < keep_amount:
                shutil.copy(spath, tpath)
        elif os.path.isdir(spath):
            walk(spath, tpath, keep_amount)

if __name__ == "__main__":
    if len(sys.argv) == 4:
        walk(sys.argv[1], sys.argv[2], float(sys.argv[3]))
    else:
        print "usage: python %s source target keep_amount" % __file__
        print "       source: path of source directory"
        print "       target: path to target directory"
        print "       keep_amount: amount to keep, e.g. 0.1 means 10%"
