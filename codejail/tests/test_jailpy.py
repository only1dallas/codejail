"""Test jail_code.py"""

import os
import os.path
import shutil
import textwrap
import tempfile
import unittest

from nose.plugins.skip import SkipTest

from codejail.jail_code import jail_code, is_configured, set_limit, LIMITS

dedent = textwrap.dedent


def jailpy(*args, **kwargs):
    """Run `jail_code` on Python."""
    return jail_code("python", *args, **kwargs)


def file_here(fname):
    """Return the full path to a file alongside this code."""
    return os.path.join(os.path.dirname(__file__), fname)


class JailCodeHelpers(object):
    """Assert helpers for jail_code tests."""
    def setUp(self):
        super(JailCodeHelpers, self).setUp()
        if not is_configured("python"):
            raise SkipTest

    def assertResultOk(self, res):
        """Assert that `res` exited well (0), and had no stderr output."""
        self.assertEqual(res.stderr, "")
        self.assertEqual(res.status, 0)


class TestFeatures(JailCodeHelpers, unittest.TestCase):
    def test_hello_world(self):
        res = jailpy(code="print 'Hello, world!'")
        self.assertResultOk(res)
        self.assertEqual(res.stdout, 'Hello, world!\n')

    def test_argv(self):
        res = jailpy(
                code="import sys; print ':'.join(sys.argv[1:])",
                argv=["Hello", "world", "-x"]
                )
        self.assertResultOk(res)
        self.assertEqual(res.stdout, "Hello:world:-x\n")

    def test_ends_with_exception(self):
        res = jailpy(code="""raise Exception('FAIL')""")
        self.assertNotEqual(res.status, 0)
        self.assertEqual(res.stdout, "")
        self.assertEqual(res.stderr, dedent("""\
            Traceback (most recent call last):
              File "jailed_code", line 1, in <module>
                raise Exception('FAIL')
            Exception: FAIL
            """))

    def test_stdin_is_provided(self):
        res = jailpy(
            code="import json,sys; print sum(json.load(sys.stdin))",
            stdin="[1, 2.5, 33]"
        )
        self.assertResultOk(res)
        self.assertEqual(res.stdout.strip(), "36.5")

    def test_files_are_copied(self):
        res = jailpy(
            code="print 'Look:', open('hello.txt').read()",
            files=[file_here("hello.txt")]
        )
        self.assertResultOk(res)
        self.assertEqual(res.stdout, 'Look: Hello there.\n\n')

    def test_directories_are_copied(self):
        res = jailpy(
            code=dedent("""\
                import os
                for path, dirs, files in os.walk("."):
                    print (path, sorted(dirs), sorted(files))
                """),
            files=[file_here("hello.txt"), file_here("pylib")]
        )
        self.assertResultOk(res)
        self.assertEqual(res.stdout, dedent("""\
            ('.', ['pylib'], ['hello.txt', 'jailed_code'])
            ('./pylib', [], ['module.py', 'module.pyc'])
            """))

    def test_executing_a_copied_file(self):
        res = jailpy(
            files=[file_here("doit.py")],
            argv=["doit.py", "1", "2", "3"]
        )
        self.assertResultOk(res)
        self.assertEqual(res.stdout, "This is doit.py!\nMy args are ['doit.py', '1', '2', '3']\n")


class TestLimits(JailCodeHelpers, unittest.TestCase):
    def test_cant_use_too_much_memory(self):
        # Default is 30Mb, try to get a 50Mb object.
        res = jailpy(code="print len(bytearray(50000000))")
        self.assertEqual(res.stdout, "")
        self.assertNotEqual(res.status, 0)

    def test_cant_use_too_much_cpu(self):
        res = jailpy(code="print sum(xrange(100000000))")
        self.assertEqual(res.stdout, "")
        self.assertNotEqual(res.status, 0)

    def test_cant_use_too_much_time(self):
        res = jailpy(code=dedent("""\
                import time
                time.sleep(5)
                print 'Done!'
                """))
        self.assertNotEqual(res.status, 0)
        self.assertEqual(res.stdout, "")

    def test_cant_write_files(self):
        res = jailpy(code=dedent("""\
                print "Trying"
                with open("mydata.txt", "w") as f:
                    f.write("hello")
                with open("mydata.txt") as f2:
                    print "Got this:", f2.read()
                """))
        self.assertNotEqual(res.status, 0)
        self.assertEqual(res.stdout, "Trying\n")
        self.assertIn("ermission denied", res.stderr)

    def test_cant_use_network(self):
        res = jailpy(code=dedent("""\
                import urllib
                print "Reading google"
                u = urllib.urlopen("http://google.com")
                google = u.read()
                print len(google)
                """))
        self.assertNotEqual(res.status, 0)
        self.assertEqual(res.stdout, "Reading google\n")
        self.assertIn("IOError", res.stderr)

    def test_cant_fork(self):
        res = jailpy(code=dedent("""\
                import os
                print "Forking"
                child_ppid = os.fork()
                """))
        self.assertNotEqual(res.status, 0)
        self.assertEqual(res.stdout, "Forking\n")
        self.assertIn("OSError", res.stderr)

    def test_cant_see_environment_variables(self):
        os.environ['HONEY_BOO_BOO'] = 'Look!'
        res = jailpy(code=dedent("""\
                import os
                for name, value in os.environ.items():
                    print "%s: %r" % (name, value)
                """))
        self.assertResultOk(res)
        self.assertNotIn("HONEY", res.stdout)


class TestSymlinks(JailCodeHelpers, unittest.TestCase):
    """Testing symlink behavior."""

    def setUp(self):
        # Make a temp dir, and arrange to have it removed when done.
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir)

        # Make a directory that won't be copied into the sandbox.
        self.not_copied = os.path.join(tmp_dir, "not_copied")
        os.mkdir(self.not_copied)
        self.linked_txt = os.path.join(self.not_copied, "linked.txt")
        with open(self.linked_txt, "w") as linked:
            linked.write("Hi!")

        # Make a directory that will be copied into the sandbox, with a
        # symlink to a file we aren't copying in.
        self.copied = os.path.join(tmp_dir, "copied")
        os.mkdir(self.copied)
        self.here_txt = os.path.join(self.copied, "here.txt")
        with open(self.here_txt, "w") as here:
            here.write("012345")
        self.link_txt = os.path.join(self.copied, "link.txt")
        os.symlink(self.linked_txt, self.link_txt)
        self.herelink_txt = os.path.join(self.copied, "herelink.txt")
        os.symlink("here.txt", self.herelink_txt)

    def test_symlinks_in_directories_wont_copy_data(self):
        # Run some code in the sandbox, with a copied directory containing
        # the symlink.
        res = jailpy(
                code=dedent("""\
                    print open('copied/here.txt').read()        # can read
                    print open('copied/herelink.txt').read()    # can read
                    print open('copied/link.txt').read()        # can't read
                    """),
                files=[self.copied],
                )
        self.assertEqual(res.stdout, "012345\n012345\n")
        self.assertIn("ermission denied", res.stderr)

    def test_symlinks_wont_copy_data(self):
        # Run some code in the sandbox, with a copied file which is a symlink.
        res = jailpy(
                code=dedent("""\
                    print open('here.txt').read()       # can read
                    print open('herelink.txt').read()   # can read
                    print open('link.txt').read()       # can't read
                    """),
                files=[self.here_txt, self.herelink_txt, self.link_txt],
                )
        self.assertEqual(res.stdout, "012345\n012345\n")
        self.assertIn("ermission denied", res.stderr)


class TestChangingLimits(JailCodeHelpers, unittest.TestCase):
    def setUp(self):
        super(TestChangingLimits, self).setUp()
        self.old_vm = LIMITS['VMEM']

    def tearDown(self):
        set_limit('VMEM', self.old_vm)
        super(TestChangingLimits, self).tearDown()

    def test_changing_vmem_limit(self):
        # This will fail (default is 30Mb)
        res = jailpy(code="print len(bytearray(50000000))")
        self.assertEqual(res.stdout, "")
        self.assertNotEqual(res.status, 0)

        # Up the limit, it will succeed
        set_limit('VMEM', 600000000)
        res = jailpy(code="print len(bytearray(50000000))")
        self.assertEqual(res.stdout, "50000000\n")
        self.assertEqual(res.status, 0)

    def test_disabling_vmem_limit(self):
        # This will fail (default is 30Mb)
        res = jailpy(code="print len(bytearray(50000000))")
        self.assertEqual(res.stdout, "")
        self.assertNotEqual(res.status, 0)

        # Disable the limit, it will succeed
        set_limit('VMEM', 0)
        res = jailpy(code="print len(bytearray(50000000))")
        self.assertEqual(res.stdout, "50000000\n")
        self.assertEqual(res.status, 0)


class TestMalware(JailCodeHelpers, unittest.TestCase):
    def test_crash_cpython(self):
        # http://nedbatchelder.com/blog/201206/eval_really_is_dangerous.html
        res = jailpy(code=dedent("""\
            import new, sys
            crash_me = new.function(new.code(0,0,0,0,"KABOOM",(),(),(),"","",0,""), {})
            print "Here we go..."
            sys.stdout.flush()
            crash_me()
            print "The afterlife!"
            """))
        self.assertNotEqual(res.status, 0)
        self.assertEqual(res.stdout, "Here we go...\n")
        self.assertEqual(res.stderr, "")

    def test_read_etc_passwd(self):
        res = jailpy(code=dedent("""\
            bytes = len(open('/etc/passwd').read())
            print 'Gotcha', bytes
            """))
        self.assertNotEqual(res.status, 0)
        self.assertEqual(res.stdout, "")
        self.assertIn("ermission denied", res.stderr)

    def test_find_other_sandboxes(self):
        res = jailpy(code=dedent("""
            import os
            places = [
                "..", "/tmp", "/", "/home", "/etc",
                "/var"
                ]
            for place in places:
                try:
                    files = os.listdir(place)
                except Exception:
                    # darn
                    pass
                else:
                    print "Files in %r: %r" % (place, files)
            print "Done."
            """))
        self.assertResultOk(res)
        self.assertEqual(res.stdout, "Done.\n")
