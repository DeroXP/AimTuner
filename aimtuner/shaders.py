"""GLSL for the three passes (see gfx.py for how they fit together).

Everything in the world is described analytically and ray-cast per pixel: the
room, up to 16 cover boxes, smoke clouds, and the targets (spheres, and vertical
capsules for the Call of Duty track's dummies). There is no depth buffer; the
target pass decides visibility itself by testing its own ray against the boxes.
"""

MAX_BOXES = 16
MAX_SMOKES = 4

# Shared by the room and target fragment shaders.
_WORLD = """
uniform vec3 uCam;
uniform int uBoxN;
uniform vec3 uBoxMin[16];
uniform vec3 uBoxMax[16];
uniform int uBoxSoft;         // boxes from this index on are soft: sight stops at them, bullets do not
uniform int uSmokeN;
uniform vec4 uSmoke[4];       // xyz centre, w radius
uniform float uSmokeDens;
uniform float uTime;

float hash13(vec3 p3){
    p3 = fract(p3 * 0.1031);
    p3 += dot(p3, p3.zyx + 31.32);
    return fract((p3.x + p3.y) * p3.z);
}

float vnoise(vec3 p){
    vec3 i = floor(p), f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(mix(hash13(i), hash13(i + vec3(1, 0, 0)), f.x),
                   mix(hash13(i + vec3(0, 1, 0)), hash13(i + vec3(1, 1, 0)), f.x), f.y),
               mix(mix(hash13(i + vec3(0, 0, 1)), hash13(i + vec3(1, 0, 1)), f.x),
                   mix(hash13(i + vec3(0, 1, 1)), hash13(i + vec3(1, 1, 1)), f.x), f.y), f.z);
}

// Where the ray ro + r*t first enters a cover box, or tmax if it gets there first.
// r need not be unit length. axis is the face it entered through (-1 = none); gBox is
// which box that was.
int gBox;
float boxHit(vec3 ro, vec3 r, float tmax, out int axis){
    vec3 inv = 1.0 / r;
    float best = tmax;
    axis = -1;
    gBox = -1;
    for (int i = 0; i < uBoxN; i++) {
        vec3 a = (uBoxMin[i] - ro) * inv, b = (uBoxMax[i] - ro) * inv;
        vec3 lo = min(a, b), hi = max(a, b);
        float tn = max(lo.x, max(lo.y, lo.z));
        float tf = min(hi.x, min(hi.y, hi.z));
        if (tn < tf && tn > 0.0 && tn < best) {
            best = tn;
            axis = tn == lo.x ? 0 : (tn == lo.y ? 1 : 2);
            gBox = i;
        }
    }
    return best;
}

// Optical depth of smoke along a unit-length ray, out to distance tmax: chord
// length through each cloud, thicker toward the middle, broken up by drifting noise.
float smokeTau(vec3 ro, vec3 rd, float tmax){
    float tau = 0.0;
    for (int i = 0; i < uSmokeN; i++) {
        vec3 oc = ro - uSmoke[i].xyz;
        float R = uSmoke[i].w;
        float b = dot(oc, rd);
        float h = b * b - (dot(oc, oc) - R * R);
        if (h > 0.0) {
            h = sqrt(h);
            float t0 = max(-b - h, 0.0), t1 = min(-b + h, tmax);
            if (t1 > t0) {
                vec3 pm = ro + rd * (0.5 * (t0 + t1)) - uSmoke[i].xyz;
                float core = clamp(1.0 - dot(pm, pm) / (R * R), 0.0, 1.0);
                float n = 0.7 + 0.6 * vnoise(pm * 0.6 + vec3(0.0, -uTime * 0.16, uTime * 0.05));
                tau += uSmokeDens * (t1 - t0) * core * n;
            }
        }
    }
    return tau;
}
"""

VS_FULL = """
#version 330 core
out vec2 vNdc;
void main(){
    vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
    vNdc = p * 2.0 - 1.0;
    gl_Position = vec4(vNdc, 0.0, 1.0);
}
"""

FS_ROOM = """
#version 330 core
in vec2 vNdc;
out vec4 fragColor;

uniform vec3 uFwd, uRight, uUp;
uniform vec2 uTan;       // tan(half fov) for x and y
uniform vec2 uInvRes;    // NDC units per pixel
uniform vec3 uBMin, uBMax;
uniform int uN;
uniform vec4 uSph[16];   // xyz centre, w radius
uniform vec4 uCol[16];   // rgb linear colour, a intensity
uniform int uQuality;    // 0 bare grid, 1 + glow and shadows, 2 + floor reflections
uniform vec3 uAccent;
uniform float uDim;
""" + _WORLD + """
float hash12(vec2 p){
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

// Line coverage for a grid, given the pixel footprint `fw` in surface units.
// When a pixel grows to cover a good part of a cell the lines dissolve into
// their average brightness instead of shimmering.
float grid(vec2 uv, float cell, float halfw, vec2 fw){
    vec2 d = abs(fract(uv / cell + 0.5) - 0.5) * cell;
    vec2 a = clamp((halfw + 0.5 * fw - d) / fw, 0.0, 1.0);
    a = mix(a, vec2(2.0 * halfw / cell), smoothstep(0.15 * cell, 0.5 * cell, fw));
    return max(a.x, a.y);
}

vec3 surface(vec3 p, int axis, float ny, vec2 fw){
    vec2 uv = axis == 0 ? p.zy : (axis == 1 ? p.xz : p.xy);
    float major = grid(uv, 8.0, 0.040, fw);
    float minor = grid(uv, 2.0, 0.016, fw);
    vec3 col;
    if (axis == 1 && ny < 0.0) {            // ceiling, with light strips
        col = vec3(0.012, 0.013, 0.018) + uAccent * (0.05 * major + 0.015 * minor);
        float sx = abs(fract(p.x / 13.0 + 0.5) - 0.5) * 13.0;
        float sz = abs(fract(p.z / 11.0 + 0.5) - 0.5) * 11.0;
        float strip = clamp((0.30 + 0.5 * fw.x - sx) / fw.x, 0.0, 1.0)
                    * clamp((4.6 + 0.5 * fw.y - sz) / fw.y, 0.0, 1.0);
        col += (uAccent * 0.55 + 0.45) * 1.8 * strip;
    } else if (axis == 1) {                 // floor
        col = vec3(0.020, 0.023, 0.030) + uAccent * (0.16 * major + 0.035 * minor) + 0.03 * major;
    } else {                                // walls
        float hgt = clamp((p.y - uBMin.y) / (uBMax.y - uBMin.y), 0.0, 1.0);
        col = vec3(0.028, 0.032, 0.042) * mix(0.75, 1.3, hgt)
            + uAccent * (0.13 * major + 0.03 * minor) + 0.025 * major;
    }
    return col;
}

// Cover blocks: a little brighter than the room so they read as objects in it. Soft ones
// (bullets pass through) are drawn as boarded wood, so what can be shot through is plain.
vec3 cover(vec3 p, int axis, vec2 fw, bool soft){
    vec2 uv = axis == 0 ? p.zy : (axis == 1 ? p.xz : p.xy);
    if (soft) {
        float board = grid(uv, 0.5, 0.010, fw);
        vec3 wood = axis == 1 ? vec3(0.150, 0.104, 0.058)
                  : (axis == 0 ? vec3(0.086, 0.059, 0.033) : vec3(0.112, 0.077, 0.043));
        return wood + vec3(0.085, 0.060, 0.030) * board;
    }
    float line = grid(uv, 1.0, 0.012, fw);
    vec3 base = axis == 1 ? vec3(0.095, 0.100, 0.118)
              : (axis == 0 ? vec3(0.048, 0.052, 0.064) : vec3(0.066, 0.070, 0.086));
    return base + uAccent * 0.07 * line;
}

void main(){
    vec3 r0 = uFwd + vNdc.x * uTan.x * uRight + vNdc.y * uTan.y * uUp;
    vec3 dRx = (uInvRes.x * uTan.x) * uRight;
    vec3 dRy = (uInvRes.y * uTan.y) * uUp;

    // The camera is inside the room, so the ray leaves through the nearest far plane...
    vec3 sf = (mix(uBMin, uBMax, step(0.0, r0)) - uCam) / r0;
    int axis = 0; float s = sf.x;
    if (sf.y < s) { s = sf.y; axis = 1; }
    if (sf.z < s) { s = sf.z; axis = 2; }
    // ...unless a cover box is in the way.
    int bax;
    float sb = boxHit(uCam, r0, s, bax);
    bool onBox = bax >= 0;
    bool softBox = onBox && gBox >= uBoxSoft;
    if (onBox) { s = sb; axis = bax; }
    vec3 p = uCam + r0 * s;

    // Exact pixel footprint on that plane (derivative of the hit point with respect
    // to one pixel). fwidth() would smear a seam along every edge of the room.
    float ra = r0[axis];
    vec3 dpx = s * (dRx - r0 * (dRx[axis] / ra));
    vec3 dpy = s * (dRy - r0 * (dRy[axis] / ra));
    vec3 f3 = abs(dpx) + abs(dpy);
    vec2 fw = max(axis == 0 ? f3.zy : (axis == 1 ? f3.xz : f3.xy), vec2(1e-5));

    vec3 n = vec3(0.0); n[axis] = -sign(ra);
    vec3 col = onBox ? cover(p, axis, fw, softBox) : surface(p, axis, n.y, fw);
    float rl = length(r0);
    vec3 rd = r0 / rl;
    float t = s * rl;

    if (uQuality > 0) {
        bool isFloor = (axis == 1 && n.y > 0.0 && !onBox);
        vec3 glow = vec3(0.0);
        float shade = 1.0;
        for (int i = 0; i < uN; i++) {
            vec3 d = uSph[i].xyz - p;
            float r = uSph[i].w;
            float l2 = dot(d, d);
            float k = min(uCol[i].a, 1.0);
            float nl = max(dot(n, d) * inversesqrt(l2), 0.0);
            glow += uCol[i].rgb * (uCol[i].a * nl * r * r * 10.0 / (l2 + 6.0 * r * r));
            if (isFloor) shade *= 1.0 - 0.5 * k * smoothstep(r * 2.4, r * 0.5, length(d.xz));
        }
        col = col * shade + glow * 0.26;

        if (isFloor && uQuality > 1) {
            // Glossy floor (its own setting, off by default: a mirrored target is a second
            // moving thing to look at). Mirror the ray, look up the room again, then the spheres.
            vec3 rr = vec3(rd.x, -rd.y, rd.z);
            float fres = 0.04 + 0.5 * pow(1.0 - abs(rd.y), 3.0);
            vec3 q = p + vec3(0.0, 1e-3, 0.0);
            vec3 sf2 = (mix(uBMin, uBMax, step(0.0, rr)) - q) / rr;
            int ax2 = 0; float t2 = sf2.x;
            if (sf2.y < t2) { t2 = sf2.y; ax2 = 1; }
            if (sf2.z < t2) { t2 = sf2.z; ax2 = 2; }
            int bx2;
            float tb = boxHit(q, rr, t2, bx2);
            float tr2 = bx2 >= 0 ? tb : t2;
            int axr = bx2 >= 0 ? bx2 : ax2;
            float fp = (t + tr2) * uInvRes.y * uTan.y * 3.0 / max(abs(rr[axr]), 0.08);
            vec3 pr = q + rr * tr2;
            vec3 rc = bx2 >= 0 ? cover(pr, bx2, vec2(fp), gBox >= uBoxSoft)
                               : surface(pr, ax2, ax2 == 1 ? -sign(rr.y) : 0.0, vec2(fp));
            for (int i = 0; i < uN; i++) {
                vec3 oc = q - uSph[i].xyz;
                float r = uSph[i].w;
                float b = dot(oc, rr);
                float d2 = dot(oc, oc) - b * b;
                float cov = step(b, 0.0) * step(-b, tr2 + r) * smoothstep(r * r, r * r * 0.55, d2);
                rc = mix(rc, uCol[i].rgb * 0.8, cov * min(uCol[i].a, 1.0));
            }
            col += fres * rc;
        }
    }

    if (uSmokeN > 0) {
        float T = exp(-smokeTau(uCam, rd, t));
        col = col * T + vec3(0.30, 0.32, 0.36) * (1.0 - T);
    }

    col *= uDim * (1.0 - 0.22 * dot(vNdc, vNdc));
    col = 1.0 - exp(-col * 1.6);
    col = pow(col, vec3(0.4545));
    col += (hash12(gl_FragCoord.xy) - 0.5) / 255.0;   // dither: no banding in the dark gradients
    fragColor = vec4(col, 1.0);
}
"""

VS_SPH = """
#version 330 core
layout(location = 0) in vec4 iSph;      // xyz centre, w bounding radius
layout(location = 1) in vec4 iCol;
layout(location = 2) in vec4 iShape;    // x half-height of a vertical capsule (0 = sphere), y radius
uniform vec3 uCam, uFwd, uRight, uUp;
uniform vec2 uTan;
uniform float uPad;
out vec3 vPos;
flat out vec4 vSph;
flat out vec4 vCol;
flat out vec4 vShape;
void main(){
    vec2 c = vec2(float(gl_VertexID & 1), float((gl_VertexID >> 1) & 1)) * 2.0 - 1.0;
    vec3 oc = iSph.xyz - uCam;
    float d = length(oc);
    vec3 w = oc / d;
    vec3 u = normalize(cross(w, vec3(0.0, 1.0, 0.0)));
    vec3 v = cross(u, w);
    float r = iSph.w;
    // A quad through the centre, facing the eye, sized to the bounding sphere's silhouette cone.
    float s = r * d * inversesqrt(max(d * d - r * r, 1e-4)) * uPad;
    vec3 P = iSph.xyz + (u * c.x + v * c.y) * s;
    vec3 e = P - uCam;
    vPos = P; vSph = iSph; vCol = iCol; vShape = iShape;
    gl_Position = vec4(dot(e, uRight) / uTan.x, dot(e, uUp) / uTan.y, 0.0, dot(e, uFwd));
}
"""

FS_SPH = """
#version 330 core
in vec3 vPos;
flat in vec4 vSph;
flat in vec4 vCol;
flat in vec4 vShape;
uniform float uHalo;
out vec4 fragColor;
""" + _WORLD + """
const vec3 L = vec3(0.236, 0.849, 0.472);
void main(){
    vec3 rd = normalize(vPos - uCam);
    vec3 oc = uCam - vSph.xyz;
    float r = vShape.y, hh = vShape.x;
    float dp, tHit;         // how far the ray passes from the shape's core; distance to the surface
    vec3 n;
    if (hh <= 0.0) {
        float b = dot(oc, rd);
        float d2 = max(dot(oc, oc) - b * b, 0.0);
        dp = sqrt(d2);
        tHit = -b - sqrt(max(r * r - d2, 0.0));
        n = (oc + rd * tHit) / r;
    } else {
        // Capsule = every point within r of a segment, so the silhouette test is the
        // distance between the ray and that segment.
        vec3 ba = vec3(0.0, 2.0 * hh, 0.0);
        vec3 w = oc + vec3(0.0, hh, 0.0);                  // camera relative to the segment's foot
        float bb = dot(rd, ba), cc = dot(ba, ba), dd = dot(rd, w), ee = dot(ba, w);
        float den = cc - bb * bb;
        float sg = den > 1e-6 ? clamp((ee - bb * dd) / den, 0.0, 1.0) : 0.0;
        float tr = max(bb * sg - dd, 0.0);
        dp = length(w + rd * tr - ba * sg);
        tHit = tr - sqrt(max(r * r - dp * dp, 0.0));
        vec3 P = w + rd * tHit;
        n = normalize(P - ba * clamp(dot(P, ba) / cc, 0.0, 1.0));
    }
    int bax;
    if (uBoxN > 0 && boxHit(uCam, rd, tHit, bax) < tHit) discard;      // behind cover

    float fw = max(fwidth(dp), 1e-6);
    float a = clamp((r - dp) / fw + 0.5, 0.0, 1.0);
    float ndl = max(dot(n, L), 0.0);
    float facing = max(dot(n, -rd), 0.0);
    vec3 base = vCol.rgb;
    vec3 col = base * (0.60 + 0.40 * ndl);
    col += pow(1.0 - facing, 3.0) * 0.30 * (0.5 + 0.5 * base);
    col += pow(max(dot(reflect(rd, n), L), 0.0), 48.0) * 0.30;
    float halo = (hh <= 0.0 ? uHalo : 0.0) * 0.26 * exp(-max(dp - r, 0.0) / (r * 0.20))
               * smoothstep(r * 1.65, r * 1.15, dp) * (1.0 - a);
    vec3 outc = col * a + base * halo;
    if (uSmokeN > 0) {
        float T = exp(-smokeTau(uCam, rd, tHit));
        outc = outc * T + vec3(0.645, 0.660, 0.687) * ((1.0 - T) * a);
    }
    fragColor = vec4(outc * vCol.a, a * vCol.a);
}
"""

VS_UI = """
#version 330 core
layout(location = 0) in vec2 aPos;
layout(location = 1) in vec2 aUV;
layout(location = 2) in vec4 aCol;
uniform vec2 uScreen;
out vec2 vUV;
out vec4 vCol;
void main(){
    vUV = aUV; vCol = aCol;
    gl_Position = vec4(aPos.x / uScreen.x * 2.0 - 1.0, 1.0 - aPos.y / uScreen.y * 2.0, 0.0, 1.0);
}
"""

FS_UI = """
#version 330 core
in vec2 vUV;
in vec4 vCol;
uniform sampler2D uAtlas;
out vec4 fragColor;
void main(){
    float a = vCol.a * (vUV.x < 0.0 ? 1.0 : texture(uAtlas, vUV).r);
    fragColor = vec4(vCol.rgb * a, a);
}
"""
