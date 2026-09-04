import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LinuxUserDeployTests(unittest.TestCase):
    def test_launcher_imports_from_src_without_install(self):
        launcher = (ROOT / "bin" / "yolo").read_text()
        self.assertIn('export PYTHONPATH="$ROOT_DIR/src', launcher)
        self.assertIn('-m crypto_yolo.cli', launcher)

    def test_user_installer_does_not_touch_system_python_or_system_units(self):
        installer = (ROOT / "deploy" / "install-user-systemd.sh").read_text()
        self.assertIn('systemctl --user', installer)
        self.assertIn('.config}/systemd/user', installer)
        self.assertNotIn('/etc/systemd/system', installer)
        self.assertNotIn('pip install', installer)
        self.assertNotIn('uv pip', installer)
        self.assertNotIn('--break-system-packages', installer)

    def test_service_uses_repo_launcher_and_no_dedicated_user(self):
        service = (ROOT / "deploy" / "systemd" / "yolo-daily.service").read_text()
        self.assertIn('ExecStart="@REPO_DIR@/bin/yolo" --wait-for-signal', service)
        self.assertNotIn('User=yolo', service)
        self.assertNotIn('Group=yolo', service)
        self.assertNotIn('/opt/yolotrading', service)
        self.assertNotIn('/var/lib/yolotrading', service)

    def test_timer_is_utc_and_non_persistent(self):
        timer = (ROOT / "deploy" / "systemd" / "yolo-daily.timer").read_text()
        self.assertIn('OnCalendar=*-*-* 09:01:00 UTC', timer)
        self.assertIn('Persistent=false', timer)


if __name__ == "__main__":
    unittest.main()
