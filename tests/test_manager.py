from contextlib import closing
import http.client
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import l4d2_conflict_checker as core
import l4d2_dashboard as dash
from make_test_vpks import build_vpk


class ManagerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "left4dead2"
        self.addons = self.root / "addons"
        self.addons.mkdir(parents=True)
        running = patch.object(dash.Manager, "_game_running", return_value=False)
        self.running = running.start()
        self.addCleanup(running.stop)
        self.manager = dash.Manager(self.addons)
        device = patch.object(dash.Manager, "_device", return_value={})
        device.start()
        self.addCleanup(device.stop)

    def vpk(self, filename, content=b"A"):
        target = self.addons / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(build_vpk({"materials/shared.vtf": content}))
        return target

    def test_configuration_read_and_replace_fail_closed(self):
        target = self.root / "addonlist.txt"
        original = b'"AddonList" { // "pretend.vpk" "0"\n"old.vpk" "0"\n}\n'
        target.write_bytes(original)
        self.assertEqual(core.load_addonlist(self.addons), {"old.vpk": False})
        with patch.object(core, "read_checked", side_effect=PermissionError("busy")):
            with self.assertRaises(PermissionError):
                core.set_addonlist_entries(self.addons, [("x.vpk", True)])
        with patch.object(core.os, "replace", side_effect=PermissionError("busy")):
            with self.assertRaises(PermissionError):
                core.set_addonlist_entries(self.addons, [("x.vpk", True)])
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse(list(self.root.glob(".l4d2-*")))

    def test_failed_apply_does_not_launch(self):
        self.vpk("a.vpk")
        with patch.object(self.manager, "_spawn_apply_helper"):
            self.running.return_value = True
            self.manager._set_targets(["a.vpk"], False)
        self.running.return_value = False
        with (patch.object(self.manager, "_open") as start,
              patch.object(core, "set_addonlist_entries", side_effect=PermissionError)):
            with self.assertRaises(PermissionError):
                self.manager.apply_and_launch()
            start.assert_not_called()
        self.assertTrue(self.manager._load_desired())

    def test_cross_process_updates_preserve_unrelated_keys(self):
        command = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "import l4d2_conflict_checker as c; "
            "c.set_addonlist_entries(sys.argv[2], [(sys.argv[3], False)])"
        )
        processes = [subprocess.Popen([sys.executable, "-c", command, sys.path[0],
                                       str(self.addons), str(index) + ".vpk"])
                     for index in range(4)]
        results = [process.wait(timeout=15) for process in processes]
        self.assertEqual(results, [0] * len(processes))
        self.assertEqual(len(core.load_addonlist(self.addons)), 4)

    def test_physical_changes_require_stopped_game(self):
        self.vpk("a.vpk")
        scan = self.manager.state()
        record = scan["addons"][0]
        self.running.return_value = True
        with self.assertRaises(dash._GameRunning):
            self.manager.delete(record["unit_id"], record["version"])
        with self.assertRaises(dash._GameRunning):
            self.manager.remove_all_mods(scan["removal_plan"])
        self.assertTrue((self.addons / "a.vpk").is_file())

    def test_batch_rejects_stale_confirmation_and_records_partial_moves(self):
        self.vpk("a.vpk")
        before = self.manager.state()["removal_plan"]
        self.vpk("b.vpk")
        with self.assertRaises(dash._ObjChanged):
            self.manager.remove_all_mods(before)
        config = self.root / "addonlist.txt"
        original = b'"AddonList" { "b.vpk" "0" }\n'
        config.write_bytes(original)
        move = self.manager._rename_unit

        def fail_second(source, *args):
            if source.endswith("b.vpk"):
                raise PermissionError("busy")
            return move(source, *args)

        with patch.object(self.manager, "_rename_unit", side_effect=fail_second):
            ok, _ = self.manager.remove_all_mods(self.manager.state()["removal_plan"])
        self.assertFalse(ok)
        manifest_path, = (self.root / "_manager_backup").glob("*/manifest.json")
        manifest = json.loads(manifest_path.read_bytes())
        first, second = manifest["moves"]
        self.assertTrue(first["moved"])
        self.assertTrue(Path(first["target"]).is_file())
        self.assertFalse(second["moved"])
        self.assertTrue(Path(second["source"]).is_file())
        self.assertEqual(config.read_bytes(), original)
        self.assertEqual((manifest_path.parent / config.name).read_bytes(), original)

    def test_disable_all_rechecks_confirmed_units(self):
        self.vpk("a.vpk")
        confirmed = self.manager.state()["disable_plan"]
        self.vpk("b.vpk")
        with self.assertRaises(dash._ObjChanged):
            self.manager.disable_all_mods(confirmed)
        latest = self.manager.state()["disable_plan"]
        self.assertTrue(self.manager.disable_all_mods(latest)[0])
        self.assertEqual(core.load_addonlist(self.addons), {"a.vpk": False, "b.vpk": False})

    def test_conflicts_use_physical_units_and_effective_packages(self):
        self.vpk("workshop/7.vpk", b"A")
        self.vpk("workshop/7/a.vpk", b"B")
        self.vpk("workshop/7/disabled.vpk", b"C")
        self.vpk("other_123.vpk", b"A")
        core.set_addonlist_entries(self.addons, [(r"workshop\7\disabled.vpk", False)])
        data = self.manager.state()
        self.assertEqual(len(data["pairs"]), 2)
        self.assertEqual(len(data["conflicts"][0]["addons"]), 3)
        core.set_addonlist_entries(self.addons, [(r"workshop\7\a.vpk", False)])
        self.assertEqual(self.manager.state()["pairs"], [])

    def test_loose_conflict_and_empty_directory_version(self):
        self.vpk("a.vpk")
        folder = self.addons / "loose"
        file = folder / "materials" / "shared.vtf"
        file.parent.mkdir(parents=True)
        file.write_bytes(b"A")
        scan = self.manager.state()
        self.assertEqual(len(scan["pairs"]), 1)
        record = next(item for item in scan["addons"] if item["kind"] == "loose")
        (folder / "empty").mkdir()
        with self.assertRaises(dash._ObjChanged):
            self.manager.keep_only(record["unit_id"], {
                item["unit_id"]: item["version"] for item in scan["addons"]})

    def test_invalid_vpk_reports_warning(self):
        package = bytearray(build_vpk({"materials/shared.vtf": b"A"}))
        size, = struct.unpack_from("<I", package, 8)
        struct.pack_into("<I", package, 8, size + 1)
        (self.addons / "broken.vpk").write_bytes(package)
        self.assertEqual(len(self.manager.state()["warnings"]), 1)

    def test_vpk_archive_and_v2_section_boundaries(self):
        package = build_vpk({"materials/shared.vtf": b"A"})
        magic, _, size = struct.unpack_from("<III", package)
        v2 = self.addons / "v2.vpk"
        v2.write_bytes(struct.pack("<7I", magic, 2, size, 1, 0, 0, 1)
                       + package[12:] + b"s")
        self.assertEqual(core.Vpk(v2).extract("materials/shared.vtf"), b"A")
        v2.write_bytes(v2.read_bytes()[:-1])
        with self.assertRaises(core.VpkError):
            core.Vpk(v2)
        v2.unlink()

        tree = bytearray(package[12:12 + size])
        offset = len(b"vtf\0materials\0shared\0")
        struct.pack_into("<H", tree, offset + 6, 0)
        (self.addons / "split_dir.vpk").write_bytes(package[:12] + tree)
        archive = self.addons / "split_000.vpk"
        archive.write_bytes(b"A")
        scan = self.manager.state()
        self.assertEqual(len(scan["addons"]), 1)
        self.assertEqual(scan["warnings"], [])
        archive.write_bytes(b"")
        self.assertTrue(self.manager.state()["warnings"])

    def test_pending_queue_survives_manager_restart_and_expires_stale_object(self):
        target = self.vpk("a.vpk")
        record = self.manager.state()["addons"][0]
        with patch.object(self.manager, "_spawn_apply_helper"):
            self.manager.enqueue("delete", record["unit_id"], record["version"])
        target.write_bytes(build_vpk({"materials/shared.vtf": b"replacement"}))
        restarted = dash.Manager(self.addons)
        self.assertEqual(restarted.apply_pending(), 0)
        self.assertEqual(restarted.state()["pending"], 0)
        self.assertTrue(restarted._load_pending()[0]["expired"])
        self.assertTrue(target.exists())

    def test_legacy_preferences_are_consumed_once(self):
        self.vpk("a.vpk")
        (self.root / ".l4d2_manager_state.json").write_text(
            '{"version":1,"keys":{"a.vpk":false}}', encoding="utf-8")
        self.assertEqual(self.manager.reconcile(), 1)
        core.set_addonlist_entries(self.addons, [("a.vpk", True)])
        self.assertEqual(self.manager.reconcile(), 0)
        self.assertTrue(core.load_addonlist(self.addons)["a.vpk"])

    def test_http_request_boundary(self):
        with dash.BoundedHTTPServer(("127.0.0.1", 0), dash.Handler) as server:
            server.manager = self.manager
            server.token = "sample-token"
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_port

            def request(method, path, headers, body=None):
                with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=5)) as client:
                    client.request(method, path, body, headers)
                    response = client.getresponse()
                    return response.status, response.headers, response.read()

            try:
                status, _, html = request("GET", "/", {"Host": "bad.invalid"})
                self.assertEqual(status, 403)
                self.assertNotIn(b"sample-token", html)
                host = f"localhost:{port}"
                status, headers, html = request("GET", "/", {"Host": host})
                self.assertEqual(status, 200)
                self.assertEqual(headers["X-Frame-Options"], "DENY")
                self.assertIn(b"sample-token", html)
                common = {"Host": host, "Origin": "http://" + host,
                          "X-L4D2-Token": "sample-token", "Content-Type": "application/json"}
                status, _, _ = request("POST", "/api/action", common,
                                       b'{"action":"disable_unlisted","confirmed":{}}')
                self.assertEqual(status, 200)
                status, _, _ = request("POST", "/api/action", common,
                                       b'{"value":' + b"[" * 2000 + b"]" * 2000 + b"}")
                self.assertEqual(status, 400)
                common["X-L4D2-Token"] = "\xe9"
                status, _, _ = request("POST", "/api/action", common, b"{}")
                self.assertEqual(status, 403)
            finally:
                server.shutdown()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
