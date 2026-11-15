"""Tests for page's file handling, without opening a window.

Run from the page folder:   python -m unittest discover -s tests -v
The round-trip tests use age.exe and age-plugin-batchpass.exe from the page folder
and are skipped if they are missing (set PAGE_REQUIRE_AGE=1 to make that a failure).
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
sys.path.insert(0, APP_DIR)

import page  # noqa: E402  (imports customtkinter, but opens no window)

AGE = os.path.join(APP_DIR, "age.exe")
CLASSIC = "age1qyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqs3290gq"


def read(path, mode="r"):
    with open(path, mode) as f:
        return f.read()


def second_name(path):
    gen = page.name_candidates(path)
    next(gen)
    return os.path.basename(next(gen))


class Names(unittest.TestCase):
    def test_numbering(self):
        cases = {
            r"C:\d\a.txt": "a (1).txt",
            r"C:\d\x.tar.gz": "x (1).tar.gz",
            r"C:\d\.gitignore": ".gitignore (1)",
            r"C:\d\noext": "noext (1)",
            r"C:\d\a.txt.age": "a (1).txt.age",
            r"C:\d\x.tar.gz.age": "x (1).tar.gz.age",
        }
        for path, want in cases.items():
            self.assertEqual(second_name(path), want, path)

    def test_decrypted_name(self):
        self.assertEqual(page.decrypted_name(r"C:\d\a.txt.age"), r"C:\d\a.txt")
        self.assertEqual(page.decrypted_name(r"C:\d\.age"), r"C:\d\.age.decrypted")

    @unittest.skipUnless(os.name == "nt", "Windows paths")
    def test_long_path(self):
        self.assertEqual(page.long_path(r"C:\a\b"), "\\\\?\\C:\\a\\b")
        self.assertEqual(page.long_path(r"\\srv\share\f"), "\\\\?\\UNC\\srv\\share\\f")
        self.assertEqual(page.long_path("relative\\p"), "relative\\p")
        self.assertEqual(page.long_path("\\\\?\\C:\\x"), "\\\\?\\C:\\x")


class PartFiles(unittest.TestCase):
    def test_publish_never_overwrites_a_late_file(self):
        with tempfile.TemporaryDirectory() as d:
            final = os.path.join(d, "a.txt")
            part, handle = page.create_part_file(final)
            with handle:
                handle.write(b"new")
            with open(final, "w") as f:
                f.write("someone else's file")  # appears while the job runs
            got = page.publish_part_file(part, final)
            self.assertEqual(os.path.basename(got), "a (1).txt")
            self.assertEqual(read(final), "someone else's file")
            self.assertEqual(read(got, "rb"), b"new")
            self.assertFalse([n for n in os.listdir(d) if n.endswith(".part")])

    def test_part_names_are_unique(self):
        with tempfile.TemporaryDirectory() as d:
            final = os.path.join(d, "a.txt")
            p1, h1 = page.create_part_file(final)
            p2, h2 = page.create_part_file(final)
            h1.close()
            h2.close()
            self.assertNotEqual(p1, p2)


class Secrets(unittest.TestCase):
    def test_without_secrets(self):
        text = "\n".join([
            "# laptop", r"C:\keys\me.txt",
            "-----BEGIN OPENSSH PRIVATE KEY-----",
            "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW",
            "-----END OPENSSH PRIVATE KEY-----",
            "QyNTUxOQAAACDzbXq9xLzv1/8+ExampleExampleExampleExampleStray",
            "AGE-SECRET-KEY-1" + "Q" * 58,
            CLASSIC,
        ])
        kept = page.without_secrets(text)
        for gone in ("PRIVATE", "b3Blbn", "QyNTUx", "AGE-SECRET"):
            self.assertNotIn(gone, kept)
        for stays in ("# laptop", r"C:\keys\me.txt", CLASSIC):
            self.assertIn(stays, kept)

    def test_keep_lines_drops_stray_text(self):
        boxes = page.keep_lines("# team\n" + CLASSIC + "\nmy secret passphrase", lambda l: l.startswith("age1"))
        self.assertNotIn("passphrase", boxes)
        self.assertIn(CLASSIC, boxes)
        paths = page.keep_lines(r"C:\keys\me.txt" + "\nhunter2", page.looks_like_path)
        self.assertEqual(paths, r"C:\keys\me.txt")

    def test_long_path_is_not_a_blob(self):
        path = r"C:\Users\abcdefghijklmnopqrstuvwxyz0123456789abcdefgh\key.txt"
        self.assertEqual(page.without_secrets(path), path)


class AdminWarning(unittest.TestCase):
    def test_exact_wording(self):
        # The owner's wording, word for word.
        self.assertEqual(page.ADMIN_WARNING,
                         "Warning: page is running as administrator. Windows hides drives mapped by your normal "
                         "account from it, also blocks drag & drop from Explorer. Close page and start it normally.")

    def test_elevation_check_runs(self):
        self.assertIsInstance(page.running_elevated(), bool)


class MissingTools(unittest.TestCase):
    def note(self, **paths):
        from types import SimpleNamespace

        fake = SimpleNamespace(**{k: paths.get(k, sys.executable)
                                  for k in ("age_path", "batchpass_path", "keygen_path", "inspect_path")})
        return page.AgeGui.missing_tools_note(fake)

    def test_all_found_says_nothing(self):
        self.assertEqual(self.note(), "")

    def test_names_what_is_missing(self):
        text = self.note(keygen_path=os.path.join(tempfile.gettempdir(), "nope", "age-keygen.exe"))
        self.assertEqual(text, "MISSING age-keygen.exe (needed for key generation)")


class ListHelpers(unittest.TestCase):
    def test_generate_passphrase(self):
        a, b = page.generate_passphrase(), page.generate_passphrase()
        self.assertNotEqual(a, b)
        self.assertRegex(a, r"^[a-z2-9]{5}(-[a-z2-9]{5}){5}$")
        self.assertNotRegex(a, r"[ilo01]")

    def test_format_size(self):
        self.assertEqual(page.format_size(None), "")
        self.assertEqual(page.format_size(5), "5 B")
        self.assertEqual(page.format_size(1536), "1.5 KB")
        self.assertEqual(page.format_size(1500 * 1024 * 1024), "1.5 GB")
        self.assertEqual(page.format_size(300 * 1024 * 1024), "300 MB")

    def test_fit_text_and_folder(self):
        self.assertEqual(page.fit_text("short.txt", 30), "short.txt")
        self.assertEqual(page.fit_text("a" * 40, 10), "a" * 9 + "\u2026")
        if os.name == "nt":
            self.assertEqual(page.folder_of(r"D:\file.txt"), "D:\\")
            self.assertEqual(page.folder_of(r"D:\Demo\file.txt"), r"D:\Demo")


class AddProblems(unittest.TestCase):
    """Dropped paths that can't be read are explained instead of "No files found"."""

    def expand(self, paths):
        from types import SimpleNamespace

        fake = SimpleNamespace()
        return page.AgeGui.expand_paths(fake, paths), fake.add_problems

    def test_missing_file_and_drive(self):
        with tempfile.TemporaryDirectory() as d:
            ok = os.path.join(d, "ok.txt")
            open(ok, "w").close()
            files, problems = self.expand([ok, os.path.join(d, "gone.txt")])
            self.assertEqual(files, [ok])
            self.assertEqual(len(problems), 1)
            self.assertTrue(problems[0].endswith("gone.txt: not found (moved or deleted?)."))
        free = next((c for c in "ZYXWVUTSRQPONMLKJIHG" if not os.path.exists(c + ":\\")), None)
        if os.name != "nt" or free is None:
            self.skipTest("needs Windows and a free drive letter")
        missing = free + r":\vault\file.txt"
        files, problems = self.expand([missing])
        self.assertEqual(files, [])
        self.assertEqual(problems, [missing + ": drive " + free + ": isn't available to page."])
        self.assertNotIn("administrator", page.problem_lines(problems))
        # The same drive behind the long-path prefix is still a drive, not a network share.
        prefixed = "\\\\?\\" + missing
        self.assertEqual(page.describe_add_error(prefixed, FileNotFoundError(2, "x")),
                         prefixed + ": drive " + free + ": isn't available to page.")

    def test_reasons(self):
        p = os.path.join(tempfile.gettempdir(), "x.txt")
        self.assertEqual(page.describe_add_error(p, PermissionError(13, "denied")), p + ": access denied.")
        self.assertEqual(page.describe_add_error(p, OSError(5, "boom")), p + ": can't be read (boom).")
        if os.name != "nt":
            return
        # An unreachable share is the server's problem, not a hidden drive (and no second network stat).
        unc = r"\\nas\share\a.txt"
        for code in (53, 64, 67, 1222):
            err = OSError(2, "network error", None, code)
            self.assertEqual(page.describe_add_error(unc, err), unc + r": network share \\nas\share isn't reachable.")
        long_unc = "\\\\?\\UNC\\nas\\share\\a.txt"
        self.assertEqual(page.describe_add_error(long_unc, OSError(2, "x", None, 53)),
                         long_unc + r": network share \\nas\share isn't reachable.")
        self.assertEqual(page.without_long_prefix(long_unc), unc)

    def test_long_lists_are_cut(self):
        problems = [f"p{i}: not found (moved or deleted?)." for i in range(8)]
        text = page.problem_lines(problems)
        self.assertEqual(text.count("\n"), 5)
        self.assertTrue(text.endswith("  ...and 3 more."))


class RecipientsFile(unittest.TestCase):
    def check(self, content):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            return page.recipients_file_problem(p)

    def test_ok_with_crlf_and_comments(self):
        problem, kinds = self.check("# Alice\r\n" + CLASSIC + "\r\n\r\n")
        self.assertIsNone(problem)
        self.assertEqual(kinds, {"classic"})

    def test_unusable_ssh_key(self):
        problem, _ = self.check(CLASSIC + "\necdsa-sha2-nistp256 AAAAE2VjZHNh bob\n")
        self.assertIn("line 2", problem)
        self.assertIn("ecdsa", problem)

    def test_secret_key(self):
        problem, _ = self.check("AGE-SECRET-KEY-1" + "Q" * 58 + "\n")
        self.assertIn("secret key", problem)

    def test_mixed_kinds_reported(self):
        _, kinds = self.check(CLASSIC + "\nage1pq1" + "q" * 60 + "\n")
        self.assertEqual(kinds, {"classic", "pq"})


def need_age(test):
    def wrapper(self):
        if not os.path.isfile(AGE):
            if os.environ.get("PAGE_REQUIRE_AGE"):
                self.fail("age.exe not found next to page.py")
            self.skipTest("age.exe not found next to page.py")
        return test(self)
    return wrapper


class RoundTrip(unittest.TestCase):
    """Runs age.exe the way page does: no -o, output into an exclusive .part file."""

    def run_age(self, args, src, final, env=None):
        part, handle = page.create_part_file(final)
        with handle:
            proc = subprocess.run([AGE] + args + [src], stdin=subprocess.DEVNULL, stdout=handle,
                                  stderr=subprocess.PIPE, env=env, creationflags=page.NO_WINDOW)
        if proc.returncode == 0:
            return page.publish_part_file(part, final)
        os.remove(part)
        return None

    def env(self, pw):
        e = dict(os.environ)
        e["AGE_PASSPHRASE"] = pw
        e["PATH"] = APP_DIR + os.pathsep + e.get("PATH", "")
        return e

    @need_age
    def test_passphrase_round_trip_and_wrong_passphrase(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "a.txt")
            with open(src, "w") as f:
                f.write("hello page\n")
            enc = self.run_age(["-e", "-j", "batchpass"], src, src + ".age", self.env("pässwörd x"))
            self.assertTrue(enc and os.path.getsize(enc) > 100)
            self.assertIsNone(self.run_age(["-d", "-j", "batchpass"], enc, page.decrypted_name(enc), self.env("wrong")))
            dec = self.run_age(["-d", "-j", "batchpass"], enc, page.decrypted_name(enc), self.env("pässwörd x"))
            self.assertEqual(os.path.basename(dec), "a (1).txt")  # the original still exists
            with open(dec, encoding="utf-8") as f:
                self.assertEqual(f.read(), "hello page\n")
            self.assertFalse([n for n in os.listdir(d) if n.endswith(".part")])

    @need_age
    def test_dry_run_catches_what_prefixes_miss(self):
        class Fake:  # just enough of the window for recipients_dry_run_problem
            age_path = AGE
        problem = page.AgeGui.recipients_dry_run_problem(Fake(), ["age1notarealkey"], "")
        self.assertIsNotNone(problem)
        self.assertIsNone(page.AgeGui.recipients_dry_run_problem(Fake(), [CLASSIC], ""))
        problem = page.AgeGui.recipients_dry_run_problem(Fake(), ["ecdsa-sha2-nistp256 AAAAE2VjZHNh"], "")
        self.assertIsNotNone(problem)


if __name__ == "__main__":
    unittest.main()
