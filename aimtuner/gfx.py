"""GPU renderer.

A frame is three draw calls and the CPU never touches a pixel:

  1. room    - one full-screen triangle; the fragment shader ray-casts the arena,
               draws the anti-aliased grid, and lights the floor and walls from
               every target (coloured glow, drop shadows, optional reflections).
  2. spheres - instanced impostor quads; each fragment solves the ray/sphere
               intersection itself, so targets are perfectly round with analytic
               edge anti-aliasing at any resolution and no MSAA buffer.
  3. ui      - one batched triangle list for all text, crosshair and panels.

No depth buffer, no MSAA, no post-process passes: at 4K that keeps the
framebuffer small and the per-frame Python cost to a few dozen GL calls, which
is what lets an interpreted app hold 240 fps.
"""
from __future__ import annotations

import ctypes
import math
import os

import numpy as np
import OpenGL

if not os.environ.get("AIMTUNER_GLDEBUG"):
    # PyOpenGL's per-call error checking roughly triples the cost of a GL call.
    OpenGL.ERROR_CHECKING = False
    OpenGL.ERROR_LOGGING = False
    OpenGL.CONTEXT_CHECKING = False
    OpenGL.ARRAY_SIZE_CHECKING = False

from OpenGL import GL  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from .theme import ACCENT, PANEL, WHITE  # noqa: E402

ROOM_MIN = (-26.0, 0.0, -30.0)
ROOM_MAX = (26.0, 13.0, 14.0)
MAX_LIGHTS = 16
MAX_SPHERES = 64
MAX_UI_VERTS = 60000

from .shaders import (FS_ROOM, FS_SPH, FS_UI, MAX_BOXES, MAX_SMOKES,  # noqa: E402
                      VS_FULL, VS_SPH, VS_UI)


def _program(vs: str, fs: str) -> int:
    def compile_(src, kind):
        sh = GL.glCreateShader(kind)
        GL.glShaderSource(sh, src)
        GL.glCompileShader(sh)
        if not GL.glGetShaderiv(sh, GL.GL_COMPILE_STATUS):
            log = GL.glGetShaderInfoLog(sh)
            raise RuntimeError("shader compile failed:\n" + (log.decode() if isinstance(log, bytes) else str(log)))
        return sh

    prog = GL.glCreateProgram()
    a, b = compile_(vs, GL.GL_VERTEX_SHADER), compile_(fs, GL.GL_FRAGMENT_SHADER)
    GL.glAttachShader(prog, a)
    GL.glAttachShader(prog, b)
    GL.glLinkProgram(prog)
    if not GL.glGetProgramiv(prog, GL.GL_LINK_STATUS):
        log = GL.glGetProgramInfoLog(prog)
        raise RuntimeError("shader link failed:\n" + (log.decode() if isinstance(log, bytes) else str(log)))
    GL.glDeleteShader(a)
    GL.glDeleteShader(b)
    return prog


def _locs(prog: int, names) -> dict:
    return {n: GL.glGetUniformLocation(prog, n) for n in names}


# ---------------------------------------------------------------- font atlas
FONT_SIZES = {"s": 15, "m": 19, "l": 27, "xl": 46, "h": 84}
_CHARS = "".join(chr(c) for c in range(32, 127)) + "°±×·•–—…→←↑↓✓✗▲▼◄►‹›µ≥≤Δ●○"
_FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
_PRIMARY = ("bahnschrift.ttf", "segoeui.ttf", "arial.ttf")
_FALLBACK = ("segoeui.ttf", "seguisym.ttf", "arial.ttf")


def _open_font(names, px):
    for n in names:
        try:
            return ImageFont.truetype(os.path.join(_FONT_DIR, n), px)
        except OSError:
            continue
    return None


def _raster(font, ch):
    x0, y0, x1, y1 = font.getbbox(ch)
    w, h = max(x1 - x0, 1), max(y1 - y0, 1)
    im = Image.new("L", (w, h), 0)
    ImageDraw.Draw(im).text((-x0, -y0), ch, fill=255, font=font)
    return w, h, im.tobytes()


class _Atlas:
    """All glyphs for every text size, rasterised once per UI scale into one R8 texture."""

    def __init__(self, scale: float):
        self.glyphs: dict[str, dict] = {}
        self.line: dict[str, float] = {}
        entries = []
        for key, base in FONT_SIZES.items():
            px = max(9, round(base * scale))
            fonts = [f for f in (_open_font(_PRIMARY, px), _open_font(_FALLBACK[0:1], px),
                                 _open_font(_FALLBACK[1:2], px)) if f]
            if not fonts:
                fonts = [ImageFont.load_default()]
            if key in ("xl", "h"):
                try:
                    fonts[0].set_variation_by_name("Bold")
                except Exception:
                    pass
            # A font draws its ".notdef" box for characters it lacks; comparing against
            # that box is how a missing glyph is detected without a font-table parser.
            missing = [_raster(f, "\uffff") for f in fonts]
            asc, desc = fonts[0].getmetrics()
            self.line[key] = float(asc + desc)
            for ch in _CHARS:
                font = fonts[0]
                for f, miss in zip(fonts, missing):
                    if ch == " " or _raster(f, ch) != miss:
                        font = f
                        break
                x0, y0, x1, y1 = font.getbbox(ch)
                entries.append((key, ch, font, x0, y0, max(x1 - x0, 0), max(y1 - y0, 0), font.getlength(ch)))

        size = 512
        while True:
            placed = self._pack(entries, size)
            if placed is not None:
                break
            size *= 2
        img = Image.new("L", (size, size), 0)
        draw = ImageDraw.Draw(img)
        for (key, ch, font, x0, y0, w, h, adv), (px_, py_) in zip(entries, placed):
            g = self.glyphs.setdefault(key, {})
            if w and h:
                draw.text((px_ + 1 - x0, py_ + 1 - y0), ch, fill=255, font=font)
                g[ch] = (px_ / size, py_ / size, (px_ + w + 2) / size, (py_ + h + 2) / size,
                         x0 - 1.0, y0 - 1.0, w + 2.0, h + 2.0, adv)
            else:
                g[ch] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, adv)
        self.size = size
        self.pixels = img.tobytes()

    @staticmethod
    def _pack(entries, size):
        x = y = row = 0
        out = []
        for (_, _, _, _, _, w, h, _) in entries:
            w, h = w + 2, h + 2
            if x + w > size:
                x, y, row = 0, y + row, 0
            if y + h > size:
                return None
            out.append((x, y))
            x += w
            row = max(row, h)
        return out


# ------------------------------------------------------------------------ UI
class UI:
    """Immediate-mode 2D batch. Coordinates are 1080-line logical units; the px_*
    calls take raw framebuffer pixels (used where exact centring matters)."""

    H = 1080.0

    def __init__(self):
        self.v = np.zeros((MAX_UI_VERTS, 8), np.float32)
        self.nv = 0
        self.s = 1.0
        self.W = 1920.0
        self.fb_w = self.fb_h = 0
        self.atlas: _Atlas | None = None
        self._cache: dict = {}
        self._wrap_cache: dict = {}

    def set_size(self, w: int, h: int) -> bool:
        """Returns True when the font atlas had to be rebuilt (new UI scale)."""
        self.fb_w, self.fb_h = w, h
        s = h / self.H
        self.W = w / s
        if self.atlas is not None and abs(s - self.s) < 1e-6:
            return False
        self.s = s
        self.atlas = _Atlas(s)
        self._cache.clear()
        self._wrap_cache.clear()
        return True

    # -- primitives
    def px_rect(self, x, y, w, h, c):
        i = self.nv
        if i + 6 > MAX_UI_VERTS:
            return
        v = self.v[i:i + 6]
        x1, y1 = x + w, y + h
        v[:, 0] = (x, x1, x1, x, x1, x)
        v[:, 1] = (y, y, y1, y, y1, y1)
        v[:, 2] = -1.0
        v[:, 4:] = c
        self.nv = i + 6

    def rect(self, x, y, w, h, c):
        s = self.s
        self.px_rect(x * s, y * s, w * s, h * s, c)

    def frame(self, x, y, w, h, c, th=1.5):
        self.rect(x, y, w, th, c)
        self.rect(x, y + h - th, w, th, c)
        self.rect(x, y + th, th, h - 2 * th, c)
        self.rect(x + w - th, y + th, th, h - 2 * th, c)

    def panel(self, x, y, w, h, c=PANEL):
        self.rect(x, y, w, h, c)
        self.frame(x, y, w, h, (1.0, 1.0, 1.0, 0.07))

    def px_quad(self, p0, p1, p2, p3, c):
        i = self.nv
        if i + 6 > MAX_UI_VERTS:
            return
        v = self.v[i:i + 6]
        v[:, 0] = (p0[0], p1[0], p2[0], p0[0], p2[0], p3[0])
        v[:, 1] = (p0[1], p1[1], p2[1], p0[1], p2[1], p3[1])
        v[:, 2] = -1.0
        v[:, 4:] = c
        self.nv = i + 6

    def line(self, x0, y0, x1, y1, th, c):
        s = self.s
        x0, y0, x1, y1, th = x0 * s, y0 * s, x1 * s, y1 * s, th * s
        dx, dy = x1 - x0, y1 - y0
        ln = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / ln * th * 0.5, dx / ln * th * 0.5
        self.px_quad((x0 + nx, y0 + ny), (x1 + nx, y1 + ny), (x1 - nx, y1 - ny), (x0 - nx, y0 - ny), c)

    def tri(self, a, b, c3, c):
        s = self.s
        i = self.nv
        if i + 3 > MAX_UI_VERTS:
            return
        v = self.v[i:i + 3]
        v[:, 0] = (a[0] * s, b[0] * s, c3[0] * s)
        v[:, 1] = (a[1] * s, b[1] * s, c3[1] * s)
        v[:, 2] = -1.0
        v[:, 4:] = c
        self.nv = i + 3

    def ring(self, cx, cy, r, th, c, a0=0.0, a1=2 * math.pi, seg=48):
        s = self.s
        cx, cy, r, th = cx * s, cy * s, r * s, th * s
        ri, ro = r - th * 0.5, r + th * 0.5
        n = max(3, int(seg * abs(a1 - a0) / (2 * math.pi)))
        pa = (math.cos(a0), math.sin(a0))
        for k in range(1, n + 1):
            a = a0 + (a1 - a0) * k / n
            pb = (math.cos(a), math.sin(a))
            self.px_quad((cx + pa[0] * ri, cy + pa[1] * ri), (cx + pa[0] * ro, cy + pa[1] * ro),
                         (cx + pb[0] * ro, cy + pb[1] * ro), (cx + pb[0] * ri, cy + pb[1] * ri), c)
            pa = pb

    def disc(self, cx, cy, r, c, seg=28):
        s = self.s
        cx, cy, r = cx * s, cy * s, r * s
        pa = (cx + r, cy)
        for k in range(1, seg + 1):
            a = 2 * math.pi * k / seg
            pb = (cx + math.cos(a) * r, cy + math.sin(a) * r)
            self.px_quad((cx, cy), pa, pb, (cx, cy), c)
            pa = pb

    # -- text
    def _layout(self, text: str, size: str):
        key = (text, size)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        g = self.atlas.glyphs[size]
        arr = np.empty((len(text) * 6, 4), np.float32)
        pen, k = 0.0, 0
        q = g["?"]
        for ch in text:
            u0, v0, u1, v1, xo, yo, w, h, adv = g.get(ch, q)
            if w:
                x0, y0 = pen + xo, yo
                x1, y1 = x0 + w, y0 + h
                arr[k:k + 6] = ((x0, y0, u0, v0), (x1, y0, u1, v0), (x1, y1, u1, v1),
                                (x0, y0, u0, v0), (x1, y1, u1, v1), (x0, y1, u0, v1))
                k += 6
            pen += adv
        if len(self._cache) > 4000:
            self._cache.clear()
        hit = self._cache[key] = (arr[:k].copy(), pen)
        return hit

    def width(self, text: str, size: str = "m", scale: float = 1.0) -> float:
        return self._layout(text, size)[1] * scale / self.s

    def text(self, x, y, text, size="m", c=WHITE, align=0, scale=1.0):
        """Draw one line; (x, y) is the top-left (align 0), top-centre (1) or top-right (2).
        Returns the width in logical units."""
        arr, w = self._layout(text, size)
        n = len(arr)
        i = self.nv
        w *= scale
        if n and i + n <= MAX_UI_VERTS:
            px = x * self.s
            if align == 1:
                px -= w * 0.5
            elif align == 2:
                px -= w
            v = self.v[i:i + n]
            v[:, :4] = arr
            if scale != 1.0:
                v[:, :2] *= scale
            v[:, 0] += round(px)
            v[:, 1] += round(y * self.s)
            v[:, 4:] = c
            self.nv = i + n
        return w / self.s

    def wrap(self, text: str, size: str, max_w: float) -> list:
        key = (text, size, round(max_w))
        hit = self._wrap_cache.get(key)
        if hit is not None:
            return hit
        lines = []
        for para in text.split("\n"):
            cur = ""
            for word in para.split(" "):
                trial = word if not cur else cur + " " + word
                if cur and self.width(trial, size) > max_w:
                    lines.append(cur)
                    cur = word
                else:
                    cur = trial
            lines.append(cur)
        if len(self._wrap_cache) > 500:
            self._wrap_cache.clear()
        self._wrap_cache[key] = lines
        return lines

    def para(self, x, y, text, size="m", c=WHITE, max_w=600.0, lh=None) -> float:
        """Wrapped text; returns the y just below it."""
        lh = lh or FONT_SIZES[size] * 1.42
        for ln in self.wrap(text, size, max_w):
            self.text(x, y, ln, size, c)
            y += lh
        return y


# ------------------------------------------------------------------ renderer
class Camera:
    __slots__ = ("eye", "fwd", "right", "up", "tanx", "tany")

    def __init__(self):
        self.eye = (0.0, 5.0, 0.0)
        self.set(0.0, 0.0, 105.0, 16 / 9)

    def set(self, yaw_deg: float, pitch_deg: float, hfov_169: float, aspect: float, zoom: float = 1.0,
            roll_deg: float = 0.0):
        y, p = math.radians(yaw_deg), math.radians(pitch_deg)
        sy, cy, sp, cp = math.sin(y), math.cos(y), math.sin(p), math.cos(p)
        self.fwd = (sy * cp, sp, -cy * cp)
        self.right = (cy, 0.0, sy)
        self.up = (-sy * sp, cp, cy * sp)
        if roll_deg:        # tilt the view about its own axis (positive = head over to the right)
            r = math.radians(roll_deg)
            cr, sr = math.cos(r), math.sin(r)
            rt, up = self.right, self.up
            self.right = tuple(cr * a - sr * b for a, b in zip(rt, up))
            self.up = tuple(sr * a + cr * b for a, b in zip(rt, up))
        # The FOV setting is horizontal at 16:9; other aspect ratios keep the same
        # vertical view and gain or lose width.
        self.tany = math.tan(math.radians(hfov_169) * 0.5) * 9.0 / 16.0 / zoom
        self.tanx = self.tany * aspect

    def project(self, pos):
        """World point -> (ndc_x, ndc_y) or None when behind the camera."""
        ex, ey, ez = pos[0] - self.eye[0], pos[1] - self.eye[1], pos[2] - self.eye[2]
        f, r, u = self.fwd, self.right, self.up
        w = ex * f[0] + ey * f[1] + ez * f[2]
        if w <= 1e-4:
            return None
        return ((ex * r[0] + ey * r[1] + ez * r[2]) / (w * self.tanx),
                (ex * u[0] + ey * u[1] + ez * u[2]) / (w * self.tany))


class Renderer:
    def __init__(self, width: int, height: int, offscreen: bool = False):
        self.ui = UI()
        self.w = self.h = 0
        self.offscreen = offscreen
        self.fbo = self.fbo_tex = 0
        self.gpu_ms = 0.0
        self.info = {
            "renderer": _gl_str(GL.GL_RENDERER),
            "version": _gl_str(GL.GL_VERSION),
        }

        world = ("uBoxN", "uBoxMin", "uBoxMax", "uBoxSoft", "uSmokeN", "uSmoke", "uSmokeDens", "uTime")
        self.p_room = _program(VS_FULL, FS_ROOM)
        self.u_room = _locs(self.p_room, ("uCam", "uFwd", "uRight", "uUp", "uTan", "uInvRes", "uBMin",
                                          "uBMax", "uN", "uSph", "uCol", "uQuality", "uAccent", "uDim") + world)
        self.p_sph = _program(VS_SPH, FS_SPH)
        self.u_sph = _locs(self.p_sph, ("uCam", "uFwd", "uRight", "uUp", "uTan", "uPad", "uHalo") + world)
        self.p_ui = _program(VS_UI, FS_UI)
        self.u_ui = _locs(self.p_ui, ("uScreen", "uAtlas"))

        self.vao_full = GL.glGenVertexArrays(1)

        # one target = centre + bounding radius | colour | capsule half-height, radius
        self.inst = np.zeros((MAX_SPHERES, 12), np.float32)
        self.lsph = np.zeros((MAX_LIGHTS, 4), np.float32)
        self.lcol = np.zeros((MAX_LIGHTS, 4), np.float32)
        self.box_lo = np.zeros((MAX_BOXES, 3), np.float32)
        self.box_hi = np.zeros((MAX_BOXES, 3), np.float32)
        self.smoke = np.zeros((MAX_SMOKES, 4), np.float32)
        self.vao_sph = GL.glGenVertexArrays(1)
        self.vbo_sph = GL.glGenBuffers(1)
        GL.glBindVertexArray(self.vao_sph)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo_sph)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, self.inst.nbytes, None, GL.GL_STREAM_DRAW)
        for loc, off in ((0, 0), (1, 16), (2, 32)):
            GL.glEnableVertexAttribArray(loc)
            GL.glVertexAttribPointer(loc, 4, GL.GL_FLOAT, GL.GL_FALSE, 48, ctypes.c_void_p(off))
            GL.glVertexAttribDivisor(loc, 1)

        self.vao_ui = GL.glGenVertexArrays(1)
        self.vbo_ui = GL.glGenBuffers(1)
        GL.glBindVertexArray(self.vao_ui)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo_ui)
        for loc, n, off in ((0, 2, 0), (1, 2, 8), (2, 4, 16)):
            GL.glEnableVertexAttribArray(loc)
            GL.glVertexAttribPointer(loc, n, GL.GL_FLOAT, GL.GL_FALSE, 32, ctypes.c_void_p(off))
        GL.glBindVertexArray(0)

        self.tex_atlas = GL.glGenTextures(1)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_CULL_FACE)
        GL.glBlendFunc(GL.GL_ONE, GL.GL_ONE_MINUS_SRC_ALPHA)   # premultiplied alpha everywhere

        # Two timer queries, read one frame late so the readback never stalls the pipeline.
        self._q = list(GL.glGenQueries(2))
        self._qi = 0
        self._q_started = [False, False]
        self._q_val = GL.GLuint(0)
        self.resize(width, height)

    # -- sizing
    def resize(self, w: int, h: int):
        w, h = max(int(w), 16), max(int(h), 16)
        if (w, h) == (self.w, self.h):
            return
        self.w, self.h = w, h
        if self.offscreen:
            if self.fbo:
                GL.glDeleteFramebuffers(1, [self.fbo])
                GL.glDeleteTextures(1, [self.fbo_tex])
            self.fbo_tex = GL.glGenTextures(1)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self.fbo_tex)
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, w, h, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, None)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
            self.fbo = GL.glGenFramebuffers(1)
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
            GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D,
                                      self.fbo_tex, 0)
            if GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER) != GL.GL_FRAMEBUFFER_COMPLETE:
                raise RuntimeError("offscreen framebuffer incomplete")
        if self.ui.set_size(w, h):
            a = self.ui.atlas
            GL.glBindTexture(GL.GL_TEXTURE_2D, self.tex_atlas)
            GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_R8, a.size, a.size, 0, GL.GL_RED,
                            GL.GL_UNSIGNED_BYTE, a.pixels)
            for k, v in ((GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR), (GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR),
                         (GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE), (GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)):
                GL.glTexParameteri(GL.GL_TEXTURE_2D, k, v)
            a.pixels = None

    @property
    def aspect(self) -> float:
        return self.w / self.h

    # -- frame
    def begin(self):
        i = self._qi
        if self._q_started[i]:
            # This query was issued two frames ago; its result is ready by now.
            GL.glGetQueryObjectuiv(self._q[i], GL.GL_QUERY_RESULT, ctypes.byref(self._q_val))
            self.gpu_ms += (self._q_val.value / 1e6 - self.gpu_ms) * 0.1
        GL.glBeginQuery(GL.GL_TIME_ELAPSED, self._q[i])
        self._q_started[i] = True
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
        GL.glViewport(0, 0, self.w, self.h)
        self.ui.nv = 0

    def _world_uniforms(self, u, nb, ns, smoke_density, t, soft_from):
        GL.glUniform1i(u["uBoxN"], nb)
        GL.glUniform1i(u["uBoxSoft"], soft_from)
        if nb:
            GL.glUniform3fv(u["uBoxMin"], nb, self.box_lo)
            GL.glUniform3fv(u["uBoxMax"], nb, self.box_hi)
        GL.glUniform1i(u["uSmokeN"], ns)
        if ns:
            GL.glUniform4fv(u["uSmoke"], ns, self.smoke)
            GL.glUniform1f(u["uSmokeDens"], smoke_density)
            GL.glUniform1f(u["uTime"], t)

    def world(self, cam: Camera, spheres, quality: int, dim: float = 1.0, accent=ACCENT,
              room=None, boxes=(), smokes=(), smoke_density: float = 0.0, t: float = 0.0, soft_from=None):
        """Draw the arena and its targets.

        spheres: iterable of (pos, radius, rgb, alpha, light[, half_height]); a
                 half_height makes it a vertical capsule of that radius.
        quality: 0 bare grid, 1 adds glow and shadows, 2 also mirrors the room in the floor.
        room:    (min, max) corners; boxes: [(min, max), ...] cover; smokes: [(x, y, z, r), ...].
        soft_from: boxes from this index on are soft walls (drawn as boarded wood); None = no soft ones.
        """
        # far-to-near so alpha-blended edges composite correctly without a depth buffer
        ex, ey, ez = cam.eye
        order = sorted(spheres, key=lambda s: -((s[0][0] - ex) ** 2 + (s[0][1] - ey) ** 2 + (s[0][2] - ez) ** 2))
        n = min(len(order), MAX_SPHERES)
        nl = 0
        inst, lsph, lcol = self.inst, self.lsph, self.lcol
        for k in range(n):
            s = order[k]
            (px, py, pz), r, (cr, cg, cb), a, light = s[:5]
            hh = s[5] if len(s) > 5 else 0.0
            inst[k] = (px, py, pz, r + hh, cr, cg, cb, a, hh, r, 0.0, 0.0)
            if light > 0.0 and nl < MAX_LIGHTS:
                lsph[nl] = (px, py, pz, r)
                lcol[nl] = (cr ** 2.2, cg ** 2.2, cb ** 2.2, light * a)
                nl += 1
        nb = min(len(boxes), MAX_BOXES)
        for k in range(nb):
            self.box_lo[k], self.box_hi[k] = boxes[k]
        ns = min(len(smokes), MAX_SMOKES)
        for k in range(ns):
            self.smoke[k] = smokes[k]
        lo, hi = room or (ROOM_MIN, ROOM_MAX)

        u = self.u_room
        GL.glDisable(GL.GL_BLEND)
        GL.glUseProgram(self.p_room)
        GL.glUniform3f(u["uCam"], *cam.eye)
        GL.glUniform3f(u["uFwd"], *cam.fwd)
        GL.glUniform3f(u["uRight"], *cam.right)
        GL.glUniform3f(u["uUp"], *cam.up)
        GL.glUniform2f(u["uTan"], cam.tanx, cam.tany)
        GL.glUniform2f(u["uInvRes"], 2.0 / self.w, 2.0 / self.h)
        GL.glUniform3f(u["uBMin"], *lo)
        GL.glUniform3f(u["uBMax"], *hi)
        GL.glUniform1i(u["uN"], nl)
        if nl:
            GL.glUniform4fv(u["uSph"], nl, lsph)
            GL.glUniform4fv(u["uCol"], nl, lcol)
        GL.glUniform1i(u["uQuality"], quality)
        GL.glUniform3f(u["uAccent"], accent[0], accent[1], accent[2])
        GL.glUniform1f(u["uDim"], dim)
        soft_from = nb if soft_from is None else min(soft_from, nb)
        self._world_uniforms(u, nb, ns, smoke_density, t, soft_from)
        GL.glBindVertexArray(self.vao_full)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)

        GL.glEnable(GL.GL_BLEND)
        if n:
            u = self.u_sph
            GL.glUseProgram(self.p_sph)
            GL.glUniform3f(u["uCam"], *cam.eye)
            GL.glUniform3f(u["uFwd"], *cam.fwd)
            GL.glUniform3f(u["uRight"], *cam.right)
            GL.glUniform3f(u["uUp"], *cam.up)
            GL.glUniform2f(u["uTan"], cam.tanx, cam.tany)
            GL.glUniform1f(u["uPad"], 1.7 if quality else 1.06)
            GL.glUniform1f(u["uHalo"], 1.0 if quality else 0.0)
            self._world_uniforms(u, nb, ns, smoke_density, t, soft_from)
            GL.glBindVertexArray(self.vao_sph)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo_sph)
            GL.glBufferSubData(GL.GL_ARRAY_BUFFER, 0, n * 48, inst)
            GL.glDrawArraysInstanced(GL.GL_TRIANGLE_STRIP, 0, 4, n)

    def end(self):
        ui = self.ui
        if ui.nv:
            GL.glEnable(GL.GL_BLEND)
            GL.glUseProgram(self.p_ui)
            GL.glUniform2f(self.u_ui["uScreen"], float(self.w), float(self.h))
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self.tex_atlas)
            GL.glUniform1i(self.u_ui["uAtlas"], 0)
            GL.glBindVertexArray(self.vao_ui)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo_ui)
            GL.glBufferData(GL.GL_ARRAY_BUFFER, ui.nv * 32, ui.v, GL.GL_STREAM_DRAW)
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, ui.nv)
        GL.glEndQuery(GL.GL_TIME_ELAPSED)
        self._qi ^= 1

    def finish(self):
        GL.glFinish()

    def screenshot(self, path: str):
        GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, self.fbo)
        GL.glPixelStorei(GL.GL_PACK_ALIGNMENT, 1)
        data = GL.glReadPixels(0, 0, self.w, self.h, GL.GL_RGB, GL.GL_UNSIGNED_BYTE)
        img = Image.frombytes("RGB", (self.w, self.h), bytes(data)).transpose(Image.FLIP_TOP_BOTTOM)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        img.save(path)


def _gl_str(name) -> str:
    v = GL.glGetString(name)
    return v.decode(errors="replace") if isinstance(v, bytes) else str(v)
