# CouchDB wrapper that can:
# 1. use Desktopcouch:               "desktopcouch://dbname"
# 2. start a CouchDB in some folder: "file:///path/to/folder"
#                                    "file:///path/to/folder#dbname?section:option=value"
#    NOTE: Options cannot be applied after the server has been
#          started, so make sure that you pass the same options
#          on subsequent requests.
# 3. use a running CouchDB instance: "http://host:port"

import re
import os
import os.path
import urllib
import time
import uuid
import simplejson

try:
    import psutil
    psutil_available = True
except ImportError:
    print "WARN: psutil not available, please install with 'easy_install psutil' or 'apt-get install python-psutil'"
    psutil_available = False

try:
    import couchdb
    couchdb_available = True
except ImportError:
    couchdb_available = False

try:
    import desktopcouch
    import desktopcouch.records.server
    desktopcouch_available = True
except ImportError:
    desktopcouch_available = False

class CouchRecord(object):
    __slots__ = "data", "db"

    def __init__(self, db, data):
        # we use object.__setattr__ to bypass
        # our __setattr__ overload

        #self.db   = db
        object.__setattr__(self, "db", db)
        #self.data = data
        object.__setattr__(self, "data", data)

    def __getattr__(self, name):
        try:
            return getattr(self.data, name)
        except AttributeError:
            if name in self.data:
                return self.data[name]
            else:
                raise AttributeError(name)

    def __setattr__(self, name, value):
        self.data[name] = value

    def __contains__(self, key):
        return key in self.data

    def __iter__(self, *args):
        return self.data.__iter__(*args)

    def __len__(self, *args):
        return self.data.__len__(*args)

    def __getitem__(self, *args):
        return self.data.__getitem__(*args)

    def __setitem__(self, *args):
        return self.data.__setitem__(*args)

    def __delitem__(self, *args):
        return self.data.__delitem__(*args)

    def __str__(self, *args):
        return self.data.__str__(*args)

    def __repr__(self, *args):
        return self.data.__repr__(*args)

class CouchView(object):
    __slots__ = "db", "uri", "params"

    def __init__(self, db, design_doc, viewname, params={}):
        self.db = db
        self.uri = design_doc + "/_view/" + viewname

    def execute(self, **params):
        # merge default and request parameters
        p = self.params.copy()
        p.update(params)

        # most parameters should be JSON, boolean or a number
        # In these cases, we can encode the value as JSON and get a useful result.
        # Some parameters need strings - we won't ever encode them.
        string_params = ["stale"]   # never encode
        json_params = ["key", "keys", "startkey", "endkey"] # always encode
        params2 = {}
        for key in p:
            value = p[key]
            if key in string_params:
                # don't touch it
                pass
            elif key in json_params or not isinstance(value, str):
                value = self._encode(value)

            params2[key] = value

        self.db.resource.get_json(self.uri, **params2)

    def _rows(self, response):
        return map(lambda row: response["rows"])

    def __len__(self):
        return self.execute(limit=0)["total_rows"]

    def __iter__(self):
        # get all rows
        #TODO auto-paginate...
        return self._rows(self.execute()).__iter__()

    def __getitem__(self, index=None):
        return self(index, index)

    @staticmethod
    def _encode(str):
        return simplejson.JSONEncoder().encode(str)

    def __call__(self, index=None, **params):
        if index:
            if isinstance(index, list):
                params["keys"] = self._encode(index)
            elif isinstance(index, slice):
                params["startkey"] = index.start
                params["endkey"]   = index.stop
                if "inclusive_end" not in params and "inclusive_end" not in self.params \
                        or index.step is not None:
                    # slices exclude the end, so our default value is false
                    # index.step will be None by default, so we use false in that case
                    # The default value for CouchDB would be true.
                    if index.step:
                        params["inclusive_end"] = True
                    else:
                        params["inclusive_end"] = False
            else:
                params["key"] = self._encode(index)

        self.execute(**params)


class CouchDatabase(object):
    __slots__ = "couch", "name", "db", "_record_type_base"

    def __init__(self, couch, db):
        self.couch = couch
        self.db = db
        self._record_type_base = None

    def __getattr__(self, name):
        # redirect to db, if we cannot handle it
        try:
            return getattr(self.db, name)
        except AttributeError:
            raise AttributeError(name)

    def __contains__(self, key):
        return key in self.data

    def __iter__(self, *args):
        return self.db.__iter__(*args)

    def __getitem__(self, *args):
        return self.db.__getitem__(*args)

    def __setitem__(self, *args):
        return self.db.__setitem__(*args)

    def __delitem__(self, *args):
        return self.db.__delitem__(*args)

    def need_design(self, design_doc, design_type, name, code):
        # design documents must have a special prefix, so CouchDB
        # will recogize that they are special
        if not design_doc.startswith("_design/"):
            design_doc = "_design/" + design_doc

        # retrieve existing document or use an empty map
        doc = design_doc in self.db and self.db[design_doc] or {}

        # The parts are stored under plural keys (e.g. "views"), but
        # the user passes the singular form to us, e.g. "view".
        # We create a map with that key, if it doesn't exist.
        design_type = design_type + "s"     # view -> views
        if design_type not in doc:
            doc[design_type] = {}

        # CouchDB will recreate the index, if we change the design
        # document. Therefore, we make sure to only touch it, if the
        # code has really changed.
        #TODO compare minimized Javascript
        if name in doc[design_type] and doc[design_type][name] == code:
            # already exists and is up-to-date
            pass
        else:
            print "INFO: updating design document %s: %s/%s" % (design_doc,design_type,name)
            doc[design_type][name] = code

            # save (update or create)
            #TODO do we have to worry about concurrent modification exceptions here?
            self.db[design_doc] = doc

    def need_view(self, design_doc, viewname, viewcode):
        self.need_design(design_doc, "view", viewname, viewcode)

    def record_type_base(self):
        return self._record_type_base or self.couch.record_type_base

    def full_record_type(self, record_type):
        """expand record_type using self.record_type_base, unless it seems to be a URL"""

        if "://" in record_type:
            return record_type
        else:
            return self.record_type_base().replace("$$", record_type)

    def need_record_view(self, record_type, design_doc, viewname, viewcode):
        record_type = self.full_record_type(record_type)
        code = 'function(doc) { if (doc.record_type == \"' + record_type + '\") {\n\t' + viewcode.replace("\n", "\n\t") + '\n}}'
        self.need_view(design_doc, viewname, {"map": code})


    def create_record(self, very_long_name_that_doesnt_clash_with_a_key123 = {}, **kw_args):
        record = very_long_name_that_doesnt_clash_with_a_key123.copy()
        record.update(kw_args)

        if "record_type" in record:
            record["record_type"] = self.full_record_type(record["record_type"])
        else:
            print "WARN No record_type set!"

        while True:
            _id = str(uuid.uuid4())
            try:
                self.db[_id] = record
                break
            except couchdb.http.ResourceConflict:
                # try again with another ID
                pass

        #NOTE python-couchdb sets '_id' and '_rev' on record

        return CouchRecord(self, record)


    #def view(self, design_doc, viewname):
    #    return CouchView(self, design_doc, viewname)


class Couch(object):
    __slots__ = "server", "db", "desktopcouch", "mycouch", "record_type_base", "_db_created"

    available = couchdb_available
    desktopcouch_available = desktopcouch_available

    _re_dbname       = "(?P<dbname>[a-zA-Z0-9_]+)"
    _re_desktopcouch = re.compile("^desktopcouch://" + _re_dbname + "?$")
    _re_file         = re.compile("^file://(?P<dir>.*?)(?:#" + _re_dbname + ")?(?:\?(?P<options>.*))?$")
    _re_connect      = re.compile("^(?P<url>https?://.*?)(?:[#/]" + _re_dbname + ")?$")

    def __init__(self, url, default_dbname=None):
        if not Couch.available:
            raise ImportError("couchdb module must be available")

        # these attributes may be None, if we cannot find a better value
        # We set the default here, so we don't need to worry about that later.
        self.desktopcouch = None
        self.mycouch = None
        self.db = None

        # find a regular expression that matches the URL
        m = re.match(Couch._re_desktopcouch, url)
        if m:
            return self._init_desktopcouch(m.group("dbname") or default_dbname)
        
        m = re.match(Couch._re_file, url)
        if m:
            return self._init_with_dir(m.group("dir"), m.group("dbname") or default_dbname, m.group("options"))

        m = re.match(Couch._re_connect, url)
        if m:
            return self._init_connection(m.group("url"), m.group("dbname") or default_dbname)

        raise ValueError("I don't understand that URL: " + str(url))

    def create(self, *args, **kw_args):
        db = self.server.create(*args, **kw_args)
        return db and CouchDatabase(self, db)

    def create_or_use(self, name, *args, **kw_args):
        if name in self.server:
            self._db_created = False
            return CouchDatabase(self, self.server[name])
        else:
            db = self.create(name, *args, **kw_args)
            self._db_created = True
            return db

    def __getitem__(self, *args, **kw_args):
        db = self.server.__getitem__(*args, **kw_args)
        return db and CouchDatabase(self, db)

    def _init_desktopcouch(self, dbname):
        if not Couch.desktopcouch_available:
            raise ImportError("desktopcouch must be available, if you use a desktopcouch:// URL")

        self.desktopcouch = desktopcouch.records.server.CouchDatabase(dbname, create=True)
        self.server = self.desktopcouch.server
        self.db     = CouchDatabase(self, self.desktopcouch.db)

    def _init_with_dir(self, dir, dbname, options):
        self.mycouch = MyCouch(dir, self._decode_options(options))
        self.server   = self.mycouch.server
        if dbname:
            self.db = self.create_or_use(dbname)

    def _init_connection(self, url, dbname):
        self.server = couchdb.Server(url)
        if dbname:
            self.db = self.create_or_use(dbname)

    def _decode_options(self, options):
        if not options:
            return []

        options = options.split("&")
        return map(self._decode_option, options)

    def _decode_option(self, option):
        if "=" in option:
            name,value = option.split("=", 1)
        else:
            name = option
            value = None
        name = name.split(":")
        name = map(urllib.unquote, name)
        if value:   # value can be None and unquote doesn't like that
            value = urllib.unquote(value)

        return [name, value]

    def futon_url(self):
        """get URL for Futon interface - very useful for debugging"""
        url = self.mycouch.uri + "_utils/"
        if self.db:
            url += "database.html?" + self.db.name
        return url


class MyCouchConfig(object):
    __slots__ = "config"

    def __init__(self):
        self.config = {}

    def get(self, section, name, default=None):
        if section in self.config and name in self.config[section]:
            return self.config[section][name]
        else:
            return default

    def set(self, section, name, value):
        if section not in self.config:
            self.config[section] = {}
        self.config[section][name] = value

    def delete(self, section, name):
        # CouchDB interprets an empty value as "delete this key"
        # see couch_config.erl, function parse_ini_file, line 240
        self.set(section, name, "")

    def save(self, path):
        f = open(path, "w")
        try:
            for section_name in self.config:
                f.write("[%s]\n" % section_name)

                section = self.config[section_name]
                for key in section:
                    value = section[key]
                    #print "%s:%s = %s" % (section_name, key, value)

                    # check the values to make sure we won't get any problems
                    # The source of my information about the config file format is the CouchDB
                    # source code: couch_config.erl, function parse_ini_file in commit 63b781fe85b1598de, 2013-03-12
                    if re.match("\s+=|=\s+", value):
                        # not possible because CouchDB splits at "\s*=\s*" and then uses implode("=")
                        # which only adds the equal sign but not the spaces
                        # see couch_config.erl, function parse_ini_file, line 236
                        print "WARN The value for '%s:%s' contains a space next to an equal sign. " \
                              + "Due to a bug in CouchDB the space will be lost" % (section_name, key)
                    elif re.match("[ \t];", value):
                        # not possible because CouchDB treats this as a comment
                        print "WARN Part of this value will be treated as a comment and ignored: %s" % value
                    elif re.match("\r\n|\n|\r|\032", value):
                        # CouchDB supports multiline values, but it will convert newline to space,
                        # so that doesn't help us here.
                        print "WARN Your value contains a newline which will be replaced by a space."

                        # make sure there is a space after every newline, so CouchDB will treat
                        # it as a multiline value
                        value = re.sub("(\r\n|\n|\r|\032) ?", "\\1 ", value)

                    f.write("%s = %s\n" % (key, value))

                f.write("\n")
        finally:
            f.close()

class MyCouch(object):
    """start a private CouchDB instance in a directory"""
    __slots__ = "dir", "server", "uri", "credentials", "additional_options", "_info"


    def get_option(self, name, default=None):
        if type(name) == str:
            name = [name]
        if not self.additional_options:
            return default
        for option in self.additional_options:
            if option[0] == name:
                return option[1]
        return default

    def get_boolean_option(self, name):
        value = self.get_option(name, False)

        #print "DEBUG get_boolean_option(%s), value is %s, result is %s" % \
        #    (repr(name), repr(value), repr(value is None or value is not False and value.lower() not in ["no", "0", "false"]))
        #print "DEBUG for options: " + repr(self.additional_options)

        #NOTE The value None means no value, but
        #     key is present (i.e. "?name&...").
        #     -> The value should be true in that case.
        #     Any string except "no", "0" and "false"
        #     means True.
        return value is None or value is not False and value.lower() not in ["no", "0", "false"]

    def __init__(self, dir, additional_options):
        self.dir = dir
        self.additional_options = additional_options

        self.credentials = None
        self._info       = None
        self.server      = None
        self.uri         = None

        # we need some infos for _is_running, so we try
        # to load them now
        self._read_info()

        if not self._is_running():
            self._init_dir()
            self._start()
        else:
            if not self.get_boolean_option("any"):
                # warn the user, if the additional options are different from the options the user wants
                if self._info["additional_options"] != self._canonical_couch_option_representation():
                    print "WARN The server is already running, so we cannot change its options!"
                    print "  requested options: " + self._canonical_couch_option_representation()
                    print "  used options:      " + self._info["additional_options"]

        self._connect()

    @staticmethod
    def escape_path(path, escape="shell"):
        if escape == "shell":
            return "'" + path.replace("'", "'\\''") + "'"
        else:
            raise ValueError("unknown escape target: " + str(escape))

    def path_for(self, which, escape=False):
        if which == "couchdb.out" or which == "couchdb.err" or which == "couchdb.log":
            # output and log goes into log directory
            which = "log/" + which
        elif which in ["database", "view_index"]:
            which = "data"  # they can use the same folder (this would be /var/lib/couchdb)

        path = os.path.join(self.dir, which)
        if escape:
            path = self.escape_path(path, escape)
        return path

    def _read_pidfile(self):
        pidfile = self.path_for("couch.pid")
        if not os.path.isfile(pidfile):
            return None

        # wait a bit to make sure the process has time
        # to write the file after creating it
        time.sleep(0.1)
        
        f = open(pidfile, "r")
        try:
            pid = f.read()
        finally:
            f.close()
        #print pid

        # we sometimes only get a "\n"
        #TODO Why is that? It also happens, if the process
        #     is running.
        if pid == "\n":
            return None

        try:
            return int(pid)
        except ValueError:
            print "WARN invalid pidfile for CouchDB"
            print "  contents of '%s': " % pidfile + repr(pid)
            return None

    def _is_running(self):
        pid = self._read_pidfile()
        if not pid:
            #print "DEBUG not running - no pid file"
            return False

        if psutil_available:
            p = None
            try:
                p = psutil.Process(pid)
                status = p.status
                if status == psutil.STATUS_ZOMBIE or status == psutil.STATUS_DEAD:
                    #print "DEBUG not running - status is %s" % status
                    return False
            except psutil.error.NoSuchProcess:
                #print "DEBUG not running - no such process"
                return False

            # let's make sure that it is the right process
            # -> Erlang processes are called beam.smp (at least on my system)
            #    However, it is "beam" on a single-processor system.
            right_name = "beam.smp"     # only a guess
            if self._info and "process_name" in self._info:
                # We have saved the name -> use that one
                right_name = self._info["process_name"]
            else:
                print "WARN We haven't saved the name of the process, so we have to guess."
            if p.name != right_name:
                # It's a different process
                #print "DEBUG not running - name is '%s' instead of '%s'" % (p.name, right_name)
                return False

            # make sure it uses our config file (thus it isn't for a different directory)
            if self.path_for("couch.ini") not in p.cmdline:
                #print "DEBUG not running - couch.ini not in cmdline"
                return False

            if self._info and "process_cmdline" in self._info \
                    and repr(p.cmdline) != self._info["process_cmdline"]:
                print "INFO Command line of CouchDB process is different than the one we expected."
                print "  expected: " + self._info["process_cmdline"]
                print "  actual:   " + repr(p.cmdline)

            try:
                # get executable path because that fails, if the
                # process doesn't belong to our user (only tested on Linux)
                p.exe
            except psutil.error.AccessDenied:
                # not our process
                #print "DEBUG not running - not our process (different user)"
                return False

            # We are quite confident that it is the right process
            return True
        else:
            print "ERROR We need psutil to test whether CouchDB is running. Please install it! Using (very) optimistic hypothesis instead..."
            return True

    def _read_info(self):
        """load our information about this CouchDB - credentials, ..."""
        path = self.path_for("config.txt")
        if not os.path.exists(path):
            return False

        f = open(path, "r")
        content = None
        try:
            content = f.read()
        finally:
            f.close()

        if not content:
            return False

        lines = content.split("\n")
        info = {}
        for line in lines:
            line = line.lstrip().rstrip("\r\n")
            if line.startswith("#") or line == "":
                # comment or empty
                pass
            else:
                key,value = line.split("=", 1)
                key = key.strip()
                value = value.lstrip()

                info[key] = value

        self._info = info
        self.credentials = [info["user"], info["pass"]]

        return True

    def _save_info(self):
        """save the values that are read by _read_info"""
        path = self.path_for("config.txt")

        if not self._info:
            self._info = {}

        if self.credentials:
            self._info["user"] = self.credentials[0]
            self._info["pass"] = self.credentials[1]

        f = open(path, "w")
        try:
            for key in self._info:
                f.write("%s = %s\n" % (key, str(self._info[key])))
        finally:
            f.close()

        return True

    def _cleanup_file(self, name):
        path = self.path_for(name)
        if os.path.exists(path):
            os.remove(path)

    def _start(self):
        ### Example couchdb calls (as shown by 'ps -ef'):
        # DesktopCouch for root
        # /usr/bin/couchdb -n -a /etc/couchdb/default.ini -a /etc/xdg/desktop-couch/compulsory-auth.ini -a /etc/xdg/desktop-couch/default.ini -a       /root/.config/desktop-couch/desktop-couchdb.ini -b -r 0 -p       /root/.cache/desktop-couch/desktop-couchdb.pid -o       /root/.cache/desktop-couch/desktop-couchdb.stdout -e       /root/.cache/desktop-couch/desktop-couchdb.stderr -R
        # DesktopCouch for benny
        # /usr/bin/couchdb -n -a /etc/couchdb/default.ini -a /etc/xdg/desktop-couch/compulsory-auth.ini -a /etc/xdg/desktop-couch/default.ini -a /home/benny/.config/desktop-couch/desktop-couchdb.ini -b -r 0 -p /home/benny/.cache/desktop-couch/desktop-couchdb.pid -o /home/benny/.cache/desktop-couch/desktop-couchdb.stdout -e /home/benny/.cache/desktop-couch/desktop-couchdb.stderr -R
        # DesktopCouch as service (on Ubuntu 12.04):
        # /usr/bin/couchdb -a /etc/couchdb/default.ini -a /etc/couchdb/local.ini -b -r 5 -p /var/run/couchdb/couchdb.pid -o /dev/null -e /dev/null -R
        #   --> started with '/usr/bin/couchdb -b -o /dev/null -e /dev/null -r 5'
        #
        #TODO what is the "-R" option?
        #
        # This is the JavaScript server: /usr/lib/couchdb/bin/couchjs /usr/share/couchdb/server/main.js
        # We don't have to start it ourselves.

        cmd = "/usr/bin/couchdb"

        cmd += " -n"    # don't load system config (we load default.ini but not local.ini)
        cmd += " -a /etc/couchdb/default.ini"   # load part of default config
        cmd += " -a " + self.path_for("couch.ini", "shell")

        cmd += " -b"    # spawn in background
        cmd += " -r 1"  # respawn after 1 second, if it crashes
        cmd += " -p " + self.path_for("couch.pid", "shell")
        cmd += " -o " + self.path_for("couchdb.out", "shell")
        cmd += " -e " + self.path_for("couchdb.err", "shell")

        # delete old pidfile and old uri file
        self._cleanup_file("couch.pid")
        self._cleanup_file("couch.uri")

        print "INFO Starting CouchDB instance in %s: %s" % (self.dir, cmd)
        os.system(cmd)

        # wait for the process
        waittime  = 20   # wait at most 20 seconds
        sleeptime = 0.2  # try again every 200ms
        pid = None
        for i in xrange(int(waittime / sleeptime)):
            pid = self._read_pidfile()
            if pid:
                break
            else:
                time.sleep(sleeptime)

        if not pid:
            # timeout -> we couldn't start the process
            print "WARN pid file hasn't been created before the timeout, so our best bet is that the server is not starting for some reason"
            return False

        # save name of the process
        if psutil_available:
            p = None
            try:
                p = psutil.Process(pid)
                status = p.status
                if status == psutil.STATUS_ZOMBIE or status == psutil.STATUS_DEAD:
                    return False
            except psutil.error.NoSuchProcess:
                return False

            # store some information about the process, so we can later check it
            if not self._info:
                self._info = {}
            self._info["process_name"]    = p.name
            self._info["process_cmdline"] = repr(p.cmdline)
            self._save_info()

    def _mkdir_p(self, dir):
        if not os.path.exists(dir):
            os.makedirs(dir)

    def _init_dir(self):
        # make sure the directory exists
        dir = self.dir
        self._mkdir_p(dir)

        # create directories for data, etc.
        for subdir in ["log", "data"]:
            self._mkdir_p(os.path.join(dir, subdir))

        # create configuration files
        self._create_config()

    def _make_random_string(self):
        # using the method suggested here: http://stackoverflow.com/a/621770
        #NOTE We might want to add some more pieces of random data. If the
        #     uuid only depends on the time, an attacker might be able to
        #     guess it. However, the post says that it uses urandom or the
        #     MAC address - that should be safe enough.
        # Value shouldn't start with a number or contain an equal sign.
        return "X" + uuid.uuid4().bytes.encode("base64").strip().rstrip("=").replace("+", "").replace("/", "")

    def _create_random_credentials(self):
        # create random user and password
        user     = self._make_random_string()
        password = self._make_random_string()
        self.credentials = [user, password]

        # and save them
        self._save_info()

    def _get_or_create_info(self, name, creator):
        if not self._info:
            self._info = {}

        if name in self._info:
            return self._info[name]
        else:
            value = creator()
            self._info[name] = value
            return value

    def _get_cookie_secret(self):
        return self._get_or_create_info("cookie_secret", self._make_random_string)

    def _create_config(self):
        if not self.credentials:
            self._create_random_credentials()

        # most configuration options are in the system default.ini which we
        # load before our own config file

        config = MyCouchConfig()

        # we require users to be authenticated via HTTP basic auth or cookies
        config.set("couch_httpd_auth", "require_valid_user", "true")
        config.set("couch_httpd_auth", "authentication_handlers",
                   "{couch_httpd_auth, cookie_authentication_handler}, {couch_httpd_auth, default_authentication_handler}")
        config.set("httpd", "WWW-Authenticate", 'Basic realm="bookmarkable-user-auth"')

        # bind to random port on local interface
        config.set("httpd", "bind_address", "127.0.0.1")
        config.set("httpd", "port", "0")

        # save URI to a file, so we can find out the port
        # https://issues.apache.org/jira/browse/COUCHDB-1338
        # http://wiki.apache.org/couchdb/Additional_Instances
        config.set("couchdb", "uri_file", self.path_for("couch.uri"))

        # log to a file
        config.set("log", "file", self.path_for("couchdb.log"))
        config.set("log", "level", "info")     # or "debug"

        # set database and view dir
        config.set("couchdb", "view_index_dir", self.path_for("view_index"))
        config.set("couchdb", "database_dir", self.path_for("database"))

        # secret value for Cookie Auth
        config.set("couch_httpd_auth", "secret", self._get_cookie_secret())

        # add an admin user
        config.set("admins", self.credentials[0], self.credentials[1])

        # remove stats handler
        if not self.get_boolean_option("stats"):
            config.delete("stats", "rate")
            config.delete("stats", "samples")
            config.delete("httpd_global_handlers", "_stats")
            config.delete("daemons", "stats_collector")
            config.delete("daemons", "stats_aggregator")

        # set additional options provided by the user
        if self.additional_options:
            for option in self._only_couch_options():
                config.set(option[0][0], option[0][1], option[1])

        # put additional_options into self._info, so we can check whether the
        # user wants different options at a later time
        self._info["additional_options"] = self._canonical_couch_option_representation()

        config.save(self.path_for("couch.ini"))

    def _only_couch_options(self):
        # valid option for config file: [[section, key], value]
        # (option for self.get_option: [[name], value or None])
        return filter(lambda opt: len(opt) == 2 and len(opt[0]) == 2, self.additional_options)

    def _canonical_couch_option_representation(self):
        #NOTE This could be improved for cases with duplicate
        #     names, but we don't expect that to happen.

        # remove any options that don't affect the CouchDB config
        #NOTE We don't use _only_couch_options because some options
        #     don't go into the config, but they still affect it,
        #     e.g. stats=true.
        options = filter(lambda opt: opt[0] not in [["any"]], self.additional_options)

        # order doesn't matter, so we sort it
        options.sort()

        # return a string because we only need to compare and store it
        return self._encode_options(options)

    def _encode_options(self, options):
        x = []
        for name,value in options:
            y = ":".join(map(urllib.quote, name))
            if value:
                y += "=" + urllib.quote(value)
            x.append(y)
        return "&".join(x)

    def _read_uri(self):
        urifile = self.path_for("couch.uri")

        # wait for uri file to appear
        waittime  = 20   # wait at most 20 seconds
        sleeptime = 0.2  # try again every 200ms
        uri = None
        for i in xrange(int(waittime / sleeptime)):
            if os.path.exists(urifile):
                break
            else:
                time.sleep(sleeptime)

        if not os.path.exists(urifile):
            raise RuntimeError("URI file hasn't been created at '%s' within %f seconds! There might be a problem with CouchDB."
                               % (urifile, waittime))

        # process might need a moment to write the URI
        time.sleep(0.1)

        # read the file
        f = open(urifile, "r")
        try:
            uri = f.read().strip()
        finally:
            f.close()

        return uri

    def _connect(self):
        uri = self._read_uri()

        # add credentials
        #NOTE We don't urlencode the values because they don't contain special characters.
        uri = uri.replace("://", "://" + self.credentials[0] + ":" + self.credentials[1] + "@")

        self.uri = uri
        self.server = couchdb.Server(uri)


    def restart(self):
        # read old uri
        uri = self._read_uri()

        # remove couch.uri, so we know when we have a new one
        self._cleanup_file("couch.uri")

        try:
            self.server.resource.post("_restart", None, {"Content-Type": "application/json"})
        except:
            # something went wrong -> restore old pidfile
            f = open(self.path_for("couch.pid"), "w")
            f.write(uri)
            f.close()

            raise

        # connect again
        #WARN This will only change self.server, but it cannot change any copies of
        #     that value. This may be a problem because the Couch class uses a copy.
        self._connect()

    def shutdown(self):
        cmd = "/usr/bin/couchdb"

        cmd += " -n"    # don't load system config (we load default.ini but not local.ini)
        cmd += " -a /etc/couchdb/default.ini"   # load part of default config
        cmd += " -a " + self.path_for("couch.ini", "shell")

        cmd += " -d"    # shutdown
        cmd += " -p " + self.path_for("couch.pid", "shell")

        print "INFO Shutting down CouchDB instance in %s: %s" % (self.dir, cmd)
        os.system(cmd)

# for desktopcouch:
#NOTE Empty values aren't useless because they can delete values set
#     by the system config.
## ; /etc/xdg/desktop-couch/compulsory-auth.ini
## [couch_httpd_auth]
## require_valid_user = true
##
## [httpd]
## WWW-Authenticate = Basic realm="bookmarkable-user-auth"
##
## ; /etc/xdg/desktop-couch/default.ini
## [replicator]
## max_http_sessions = 1
##
## ; /home/benny/.config/desktop-couch/desktop-couchdb.ini
## [oauth_consumer_secrets]
## qBeLBWpAEs = BAFRIxQpRH
##
## [httpd]
## bind_address = 127.0.0.1
## port = 0
## WWW-Authenticate = Basic realm="bookmarkable-user-auth"
##
## [oauth_token_users]
## JuHPWrXldU = nQzeIcRNmL
##
## [stats]
## rate = 
## samples = 
##
## [log]
## file = /home/benny/.cache/desktop-couch/desktop-couchdb.log
## level = debug
##
## [couchdb]
## view_index_dir = /home/benny/.local/share/desktop-couch
## database_dir = /home/benny/.local/share/desktop-couch
##
## [couch_httpd_auth]
## require_valid_user = true
##
## [oauth_token_secrets]
## JuHPWrXldU = OZlWYRvGYq
##
## [admins]
## nQzeIcRNmL = -hashed-6768cc6f506d72499fd42b29f38004f02bf083bb,6c1f362a63f2f130e82b4f645be001b9
## benny = -hashed-8de60cf94ad6af89fb4321ff67ebb863ce77db60,6e3037ab5976da93f56b752bbbe6620a
##
## [httpd_global_handlers]
## _stats = 
##
## [daemons]
## stats_collector = 
## stats_aggregator = 
##
##
## ;/etc/couchdb/local.ini (not used by desktopcouch)
## [couch_httpd_auth]
## ; If you set this to true, you should also uncomment the WWW-Authenticate line
## ; above. If you don't configure a WWW-Authenticate header, CouchDB will send
## ; Basic realm="server" in order to prevent you getting logged out.
## ; require_valid_user = false
## secret = 0754bc55205ebdc6237a63d5809e91f8
