import re

with open("ground_station/analysis/live_tracker.py") as f:
    text = f.read()

# Fix the E702 issues:
# 258: self.g32.DeleteObject(self._gdi[3]); self.g32.DeleteDC(self._gdi[2]); self._gdi = None
text = text.replace(
    "self.g32.DeleteObject(self._gdi[3]); self.g32.DeleteDC(self._gdi[2]); self._gdi = None",
    "self.g32.DeleteObject(self._gdi[3])\n            self.g32.DeleteDC(self._gdi[2])\n            self._gdi = None"
)

# 265: mdc = self.g32.CreateCompatibleDC(dc); bmp = self.g32.CreateCompatibleBitmap(dc, w, h)
text = text.replace(
    "mdc = self.g32.CreateCompatibleDC(dc); bmp = self.g32.CreateCompatibleBitmap(dc, w, h)",
    "mdc = self.g32.CreateCompatibleDC(dc)\n            bmp = self.g32.CreateCompatibleBitmap(dc, w, h)"
)

# 266: self.u32.ReleaseDC(self.hwnd, dc); self.g32.SelectObject(mdc, bmp)
text = text.replace(
    "self.u32.ReleaseDC(self.hwnd, dc); self.g32.SelectObject(mdc, bmp)",
    "self.u32.ReleaseDC(self.hwnd, dc)\n            self.g32.SelectObject(mdc, bmp)"
)

# 850: c = np.asarray(cam_xy_m, float); e = np.asarray(earth_cm, float)
text = text.replace(
    "c = np.asarray(cam_xy_m, float); e = np.asarray(earth_cm, float)",
    "c = np.asarray(cam_xy_m, float)\n        e = np.asarray(earth_cm, float)"
)

with open("ground_station/analysis/live_tracker.py", "w") as f:
    f.write(text)
print("done")
