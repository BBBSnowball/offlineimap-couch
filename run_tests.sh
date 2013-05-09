#!/bin/sh

# break on error
set -e

python -m unittest test2.tests.test_couchlib
python -m unittest test2.tests.test_02_CouchRepository
python -m unittest test2.tests.test_03_move_mails
