"""场景包导入校验（validate_pack_zip）：安全闸的放行/拦截矩阵。"""
import io
import unittest
import zipfile

from workbuddy import web
from workbuddy.web import petpack_frame_path, validate_pack_zip


def _zip(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_petpack_frame_path_is_whitelisted_and_requires_file(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "petpack_base_dir", lambda: tmp_path)
    pack = tmp_path / "petpack_demo"
    pack.mkdir()
    frame = pack / "pet_working.jpg"
    frame.write_bytes(b"jpeg")
    assert petpack_frame_path("demo", "working") == frame
    assert petpack_frame_path("../demo", "working") is None
    assert petpack_frame_path("demo", "../../secret") is None
    assert petpack_frame_path("demo", "happy") is None


class ValidatePackZipTests(unittest.TestCase):
    def test_good_pack_passes(self):
        files, err = validate_pack_zip(_zip({
            "pet_idle.jpg": b"j" * 100,
            "pet_working.jpg": b"j" * 100,
            "pet_working_b.jpg": b"j" * 100,
        }))
        self.assertIsNone(err)
        self.assertEqual(sorted(files), ["pet_idle.jpg", "pet_working.jpg",
                                         "pet_working_b.jpg"])

    def test_rejects_bad_names_and_paths(self):
        for bad in ("evil.txt", "sub/pet_idle.jpg", "../pet_idle.jpg",
                    "pet_.jpg", "PET_IDLE.JPG", "pet_" + "x" * 20 + ".jpg"):
            files, err = validate_pack_zip(_zip({bad: b"x"}))
            self.assertIsNone(files, bad)
            self.assertIn("不允许", err)

    def test_rejects_oversize_and_count(self):
        files, err = validate_pack_zip(_zip({"pet_big.jpg": b"j" * (201 * 1024)}))
        self.assertIn("200KB", err)
        many = {f"pet_f{i:02d}.jpg": b"x" for i in range(13)}
        files, err = validate_pack_zip(_zip(many))
        self.assertIn("过多", err)

    def test_rejects_garbage_and_empty(self):
        files, err = validate_pack_zip(b"not a zip")
        self.assertIn("zip", err)
        files, err = validate_pack_zip(_zip({}))
        self.assertIn("没有文件", err)
        files, err = validate_pack_zip(b"")
        self.assertIn("空", err)


if __name__ == "__main__":
    unittest.main()
