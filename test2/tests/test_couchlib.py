# python -m unittest test2.tests.test_couchlib

import unittest
import offlineimap.couchlib

class TestCouchlib(unittest.TestCase):

	def setUp(self):
		self.couch = offlineimap.couchlib.Couch("tmp://couch_", "test")
		self.db = self.couch.db

	def tearDown(self):
		self.couch.mycouch.shutdown()

	def _get_ip(self):
		# try to determine the IP of our network card
		# http://stackoverflow.com/a/166589
		import socket
		s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		s.connect(("google.com",80))
		ip = s.getsockname()[0]
		s.close()
		return ip

	def debug_stop(self):
		url = self.couch.futon_url()
		import re, sys, subprocess
		m = re.match(".*(127.0.0.1|localhost):([0-9]+)", url)
		forwarding_process = None
		if m:
			print "Forwarding the connection"
			# It's a local connection, so we may have to
			# forward the port. We start a forwarding process
			# in any case. It will fail to start, if the port
			# is already used, which most likely means that
			# CouchDB listens on a public port.
			ip = self._get_ip()
			port = m.group(2)
			# netcat only forwards one connection and quits (at least the one I have), so
			# we use socat instead
			#forward_cmd = "nc -c \"nc 127.0.0.1 %s\" -s %s -l -p %s" % (port, ip, port)
			#forward_cmd = "socat TCP-LISTEN:%s,fork,bind=%s TCP:127.0.0.1:%s" % (port, ip, port)

			# we want to kill it later, so we need to remember the PID
			#pidfile = tempfile.mktemp("", "socat_pid_")
			#cmd = "{ %s & } ; echo $! >%s" % (forward_cmd, pidfile)
			#TODO kill this process later...
			#os.system(cmd)

			forwarding_process = subprocess.Popen(["socat", "TCP-LISTEN:%s,fork,bind=%s" % (port, ip), "TCP:127.0.0.1:%s" % port])

			url = re.sub("127.0.0.1|localhost", ip, url)

		print "====================="
		print "     DEBUG STOP      "
		print "====================="
		print "You can access Futon using this URL:"
		print url
		print ""
		print "Please press ENTER to continue."
		sys.stdin.readline()

		# stop forwarding process
		if forwarding_process:
			forwarding_process.terminate()


	def test_put(self):
		self.db["test1"] = {"blub": 42}

		x = self.db["test1"]
		self.assertTrue(x)
		self.assertEqual(x["blub"], 42)

	def test_create_record(self):
		self.couch.record_type_base = "http://blub/$$"

		a = {"blub": 42, "foo": "bar"}
		x1 = self.db.create_record(a)
		x2 = self.db.create_record(a, d = 7)
		x3 = self.db.create_record(d = 7)

		self.assertTrue("_id" in x1)
		self.assertTrue("_id" in x2)
		self.assertTrue("_id" in x3)

		self.assertTrue("_rev" in x1)
		self.assertTrue("_rev" in x2)
		self.assertTrue("_rev" in x3)

		self.assertEqual(42, x1["blub"])
		self.assertEqual(42, x2["blub"])
		self.assertTrue("blub" not in x3)

		self.assertTrue("d" not in x1)
		self.assertEqual(7, x2["d"])
		self.assertEqual(7, x3["d"])
		self.assertTrue("d" not in a)

		y1 = self.db[x1["_id"]]
		y2 = self.db[x2["_id"]]
		y3 = self.db[x3["_id"]]

		self.assertEqual(x1["_rev"], y1["_rev"])
		self.assertEqual(x2["_rev"], y2["_rev"])
		self.assertEqual(x3["_rev"], y3["_rev"])

		self.assertEqual(42, y1["blub"])
		self.assertEqual(42, y2["blub"])
		self.assertTrue("blub" not in y3)

		self.assertTrue("d" not in y1)
		self.assertEqual(7, y2["d"])
		self.assertEqual(7, y3["d"])

		# we should be able to access record data as attributes
		self.assertEqual(42, x1.blub)
		self.assertEqual(42, x2.blub)
		self.assertEqual( 7, x2.d)
		self.assertEqual( 7, x3.d)
		# If the attribute doesn't exist, we get the apropriate error
		self.assertRaises(AttributeError, lambda x: x.blub, x3)

	def test_record_type(self):
		self.couch.record_type_base = "http://bbbsnowball.dyndns.org/couchdb/$$"
		r1 = self.db.create_record({"record_type": "special_note"}, title = "Test 1")
		r2 = self.db.create_record({"record_type": "http://bbbsnowball.dyndns.org/couchdb/special_note"}, title = "Test 2")
		r3 = self.db.create_record(record_type = "special_note", title = "Test 3")
		r4 = self.db.create_record(record_type = "boring_note", title = "Test 4")

		self.assertEqual("http://bbbsnowball.dyndns.org/couchdb/special_note", r1.record_type)
		self.assertEqual("http://bbbsnowball.dyndns.org/couchdb/special_note", r2.record_type)
		self.assertEqual("http://bbbsnowball.dyndns.org/couchdb/special_note", r3.record_type)
		self.assertEqual("http://bbbsnowball.dyndns.org/couchdb/boring_note",  r4.record_type)

		docs = self.db.query("function(doc) { if (doc.record_type=='http://bbbsnowball.dyndns.org/couchdb/special_note') emit(doc.title, null); }")
		titles = map(lambda doc: doc.key, docs)
		titles.sort()
		self.assertListEqual(["Test 1", "Test 2", "Test 3"], titles)
		self.assertEquals(3, docs.total_rows)

	def test_views(self):
		#NOTE This test seems to match the use of mail/mail_folder/... in the program, but it doesn't!
		#     Here we use record_type="mail", but in the main program this is the name of the design
		#     document! In the program mailpath is a string, but here we use a list. And the list goes
		#     on... So don't confused by that, please ;-)
		self.couch.record_type_base = "http://bbbsnowball.dyndns.org/couchdb/$$"
		self.db.need_record_view("mail", "mail_folder", "mail_folders", "emit([doc.mailpath, doc.name], doc);")
		def view():
			return self.db.view("mail_folder/mail_folders")

		self.assertEqual(0, view().total_rows)

		self.db.create_record(record_type = "mail", mailpath = ["a", "b", "c"], name = "T1")

		#self.debug_stop()
		self.assertEqual(1, view().total_rows)

		self.db.create_record(record_type = "mail", mailpath = ["a", "b", "c2"], name = "T2")
		self.db.create_record(record_type = "mail", mailpath = ["a", "b"], name = "T3")
		self.db.create_record(record_type = "mail", mailpath = ["a", "b"], name = "T4")

		self.assertEqual(4, view().total_rows)

		self.db.create_record(record_type = "mail", mailpath = ["a"], name = "T5")
		self.db.create_record(record_type = "mail", mailpath = ["b"], name = "T6")

		def test(view_result, *mails):
			names = map(lambda row: row.value["name"], view_result)
			mails = list(mails)
			mails.sort()
			names.sort()
			self.assertListEqual(mails, names)

		test(view(), "T1", "T2", "T3", "T4", "T5", "T6")
		test(view()[[["a","b"]]:[["a","b"],{}]], "T3", "T4")
		test(view()[[["a","b"]]:[["a","b",{}],{}]], "T3", "T4", "T1", "T2")


def run():
	suite = unittest.TestLoader().loadTestsFromTestCase(TestCouchlib)
	unittest.TextTestRunner(verbosity=2).run(suite)

if __name__ == "__main__":
	run()
