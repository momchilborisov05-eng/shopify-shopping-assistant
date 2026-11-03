"""
Draws the demo store's product illustrations and the hero image (web/static).

    python data/make_images.py

Flat Scandinavian-style illustrations, drawn with Pillow at 2x and scaled down
for smooth edges. Replace them with real photos for a real store: the store
reads the images from Shopify.
"""

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "web" / "static"
W, H, S = 800, 1000, 2  # output size and supersampling
BG = (239, 236, 229)


def canvas():
    img = Image.new("RGB", (W * S, H * S), BG)
    glow = Image.new("L", (W * S, H * S), 0)
    ImageDraw.Draw(glow).ellipse([W * S * 0.1, H * S * 0.08, W * S * 0.9, H * S * 0.85], fill=60)
    glow = glow.filter(ImageFilter.GaussianBlur(160))
    img.paste((247, 245, 240), mask=glow)
    return img


def shadow(img, cx, cy, rx, ry, strength=70):
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).ellipse([(cx - rx) * S, (cy - ry) * S, (cx + rx) * S, (cy + ry) * S], fill=strength)
    mask = mask.filter(ImageFilter.GaussianBlur(22 * S))
    img.paste((150, 143, 130), mask=mask)


def P(points):
    return [(x * S, y * S) for x, y in points]


def shade(color, f):
    return tuple(max(0, min(255, int(c * f))) for c in color)


def finish(img, name):
    img = img.resize((W, H), Image.LANCZOS)
    (OUT / "products").mkdir(parents=True, exist_ok=True)
    img.save(OUT / "products" / f"{name}.jpg", quality=88, optimize=True, progressive=True)


# ---------- clothing ----------

def top(name, color, style):
    img = canvas()
    shadow(img, 400, 905, 250, 26)
    d = ImageDraw.Draw(img)
    dark, light = shade(color, 0.78), shade(color, 1.12)
    slim = style == "base"
    sx = 30 if slim else 0
    # sleeves
    d.polygon(P([(250 + sx, 240), (150 + sx, 330), (95 + sx * 1.5, 760), (170 + sx, 775), (235 + sx, 420)]), fill=dark)
    d.polygon(P([(550 - sx, 240), (650 - sx, 330), (705 - sx * 1.5, 760), (630 - sx, 775), (565 - sx, 420)]), fill=dark)
    # cuffs
    d.rounded_rectangle(P([(95 + sx * 1.5, 745), (172 + sx, 800)]), radius=10 * S, fill=shade(color, 0.6))
    d.rounded_rectangle(P([(628 - sx, 745), (705 - sx * 1.5, 800)]), radius=10 * S, fill=shade(color, 0.6))
    # body
    hem = 850 if style != "fleece" else 830
    d.polygon(P([(250 + sx, 225), (550 - sx, 225), (585 - sx, 330), (575 - sx, hem), (225 + sx, hem), (215 + sx, 330)]),
              fill=color)
    d.rounded_rectangle(P([(222 + sx, hem - 30), (578 - sx, hem + 8)]), radius=12 * S, fill=dark)
    if style == "shell":  # hood, zip, chest pocket, pit zip
        d.polygon(P([(300, 230), (330, 95), (400, 70), (470, 95), (500, 230), (400, 270)]), fill=dark)
        d.polygon(P([(330, 230), (352, 120), (400, 105), (448, 120), (470, 230), (400, 255)]), fill=shade(color, 0.55))
        d.line(P([(400, 255), (400, 850)]), fill=shade(color, 0.5), width=5 * S)
        d.line(P([(450, 330), (520, 320)]), fill=shade(color, 0.5), width=4 * S)
        d.line(P([(255, 560), (300, 700)]), fill=shade(color, 0.6), width=4 * S)
        d.line(P([(545, 560), (500, 700)]), fill=shade(color, 0.6), width=4 * S)
    elif style == "down":  # collar, zip, baffles
        d.rounded_rectangle(P([(320, 175), (480, 250)]), radius=30 * S, fill=dark)
        for y in range(300, 830, 62):
            d.arc(P([(215, y - 40), (585, y + 30)]), 15, 165, fill=shade(color, 0.6), width=4 * S)
        d.line(P([(400, 245), (400, 850)]), fill=shade(color, 0.45), width=5 * S)
    elif style == "fleece":  # half zip, collar, grid texture
        d.polygon(P([(320, 230), (330, 160), (470, 160), (480, 230), (400, 260)]), fill=dark)
        d.line(P([(400, 175), (400, 420)]), fill=shade(color, 0.45), width=5 * S)
        d.ellipse(P([(392, 410), (408, 432)]), fill=shade(color, 0.45))
        rnd = random.Random(3)
        for y in range(290, 820, 26):
            for x in range(245, 560, 26):
                if rnd.random() > 0.15:
                    d.rectangle(P([(x, y), (x + 12, y + 12)]), outline=light, width=2 * S)
    else:  # base layer: crew neck, seams
        d.ellipse(P([(335, 205), (465, 265)]), fill=shade(color, 0.6))
        d.ellipse(P([(345, 205), (455, 250)]), fill=BG)
        d.line(P([(280, 260), (290, 840)]), fill=shade(color, 0.85), width=3 * S)
        d.line(P([(520, 260), (510, 840)]), fill=shade(color, 0.85), width=3 * S)
    finish(img, name)


# ---------- footwear ----------

def boot(name, color, trail=False):
    img = canvas()
    shadow(img, 410, 760, 300, 28)
    d = ImageDraw.Draw(img)
    sole = (40, 40, 40) if not trail else (60, 62, 66)
    if trail:
        upper = [(140, 650), (165, 595), (250, 565), (370, 545), (460, 505), (530, 470), (600, 465), (640, 515),
                 (680, 590), (700, 650)]
    else:
        upper = [(160, 640), (175, 540), (260, 500), (330, 470), (360, 270), (520, 255), (560, 300), (580, 470),
                 (660, 560), (690, 640)]
    d.polygon(P(upper + [(690, 690), (160, 690)]), fill=color)
    # toe cap and heel
    d.chord(P([(120, 560), (330, 720)]), 90, 270, fill=shade(color, 0.7))
    d.pieslice(P([(560, 560), (720, 720)]), 270, 90, fill=shade(color, 0.75))
    # midsole and outsole with lugs
    midsole = (250, 250, 248) if trail else (90, 70, 55)
    d.rounded_rectangle(P([(120, 680), (710, 712)]), radius=14 * S, fill=midsole, outline=(190, 188, 182), width=3 * S)
    d.rounded_rectangle(P([(120, 705), (710, 740)]), radius=14 * S, fill=sole)
    for x in range(140, 700, 38):
        d.polygon(P([(x, 740), (x + 22, 740), (x + 16, 752), (x + 6, 752)]), fill=sole)
    # laces
    lace_points = [(400 + i * 6, y) for i, y in enumerate(range(320, 540, 34))]
    if trail:  # along the top edge of the shoe
        lace_points = [(390 + i * 32, 552 - (390 + i * 32 - 370) * 0.47) for i in range(5)]
    for x, y in lace_points:
        d.line(P([(x - 30, y), (x + 34, y + 12)]), fill=(235, 225, 205), width=5 * S)
        d.ellipse(P([(x - 38, y - 6), (x - 26, y + 6)]), fill=shade(color, 0.5))
    if trail:
        d.line(P([(230, 620), (400, 575), (560, 545), (640, 560)]), fill=(208, 102, 47), width=12 * S)
        d.line(P([(250, 660), (660, 640)]), fill=(208, 102, 47), width=6 * S)
    else:
        d.rounded_rectangle(P([(350, 255), (530, 290)]), radius=14 * S, fill=shade(color, 0.6))
    finish(img, name)


# ---------- packs ----------

def pack(name, color, tall=False, accent=None):
    img = canvas()
    shadow(img, 400, 900, 230, 26)
    d = ImageDraw.Draw(img)
    top_y = 150 if tall else 230
    d.rounded_rectangle(P([(240, top_y + 40), (560, 880)]), radius=90 * S, fill=color)
    d.rounded_rectangle(P([(255, top_y), (545, top_y + 150)]), radius=70 * S, fill=shade(color, 0.82))
    d.rounded_rectangle(P([(300, 520 if tall else 560), (500, 840)]), radius=50 * S, fill=shade(color, 0.86))
    d.line(P([(300, 600 if tall else 640), (500, 600 if tall else 640)]), fill=shade(color, 0.6), width=5 * S)
    # straps and side pockets
    for x in (275, 525):
        d.rounded_rectangle(P([(x - 12, top_y + 120), (x + 12, 800)]), radius=10 * S, fill=shade(color, 0.65))
    d.rounded_rectangle(P([(200, 560), (250, 860)]), radius=24 * S, fill=shade(color, 0.7))
    d.rounded_rectangle(P([(550, 560), (600, 860)]), radius=24 * S, fill=shade(color, 0.7))
    if tall:
        for y in (380, 470):
            d.line(P([(240, y), (560, y + 8)]), fill=(30, 30, 30), width=6 * S)
            d.rounded_rectangle(P([(390, y - 8), (420, y + 16)]), radius=4 * S, fill=(30, 30, 30))
    if accent:
        d.rounded_rectangle(P([(360, top_y + 60), (440, top_y + 85)]), radius=10 * S, fill=accent)
    # top handle
    d.arc(P([(360, top_y - 50), (440, top_y + 30)]), 180, 360, fill=shade(color, 0.6), width=10 * S)
    finish(img, name)


# ---------- accessories ----------

def bottle(name, color):
    img = canvas()
    shadow(img, 400, 885, 120, 20)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(P([(300, 260), (500, 880)]), radius=60 * S, fill=color)
    d.rounded_rectangle(P([(330, 300), (360, 840)]), radius=15 * S, fill=shade(color, 1.25))
    d.rounded_rectangle(P([(320, 160), (480, 280)]), radius=24 * S, fill=(176, 178, 176))
    for y in range(180, 270, 18):
        d.line(P([(325, y), (475, y)]), fill=(150, 152, 150), width=3 * S)
    d.rounded_rectangle(P([(300, 270), (500, 292)]), radius=8 * S, fill=shade(color, 0.7))
    finish(img, name)


def poles(name):
    img = canvas()
    shadow(img, 400, 895, 220, 22)
    d = ImageDraw.Draw(img)
    for (x0, x1), lean in (((330, 520), 1), ((470, 280), -1)):
        d.line(P([(x0, 170), (x1, 880)]), fill=(35, 36, 38), width=16 * S)
        for t in (0.42, 0.68):
            x, y = x0 + (x1 - x0) * t, 170 + 710 * t
            d.ellipse(P([(x - 13, y - 9), (x + 13, y + 9)]), fill=(200, 200, 196))
        d.rounded_rectangle(P([(x0 - 22, 140), (x0 + 22, 330)]), radius=20 * S, fill=(196, 154, 108))
        d.rounded_rectangle(P([(x0 - 24, 120), (x0 + 24, 150)]), radius=10 * S, fill=(35, 36, 38))
        d.line(P([(x0 - 22, 150), (x0 + 30 * lean, 230)]), fill=(70, 70, 72), width=5 * S)
        d.ellipse(P([(x1 - 26, 845), (x1 + 26, 860)]), fill=(60, 60, 62))
    finish(img, name)


def beanie(name, color):
    img = canvas()
    shadow(img, 400, 760, 230, 24)
    d = ImageDraw.Draw(img)
    d.chord(P([(190, 260), (610, 900)]), 180, 360, fill=color)
    for x in range(215, 600, 24):
        top = 580 - math.sqrt(max(0, 210 ** 2 - (x - 400) ** 2)) * 1.48
        d.line(P([(x, max(top + 18, 300)), (x, 575)]), fill=shade(color, 0.82), width=5 * S)
    d.rounded_rectangle(P([(180, 560), (620, 730)]), radius=40 * S, fill=shade(color, 0.9))
    for x in range(200, 610, 22):
        d.line(P([(x, 575), (x, 715)]), fill=shade(color, 0.72), width=6 * S)
    finish(img, name)


def headlamp(name):
    img = canvas()
    shadow(img, 400, 800, 240, 24)
    d = ImageDraw.Draw(img)
    d.ellipse(P([(170, 330), (630, 760)]), outline=(120, 124, 128), width=34 * S)
    d.ellipse(P([(190, 350), (610, 740)]), outline=(150, 154, 158), width=6 * S)
    d.rounded_rectangle(P([(290, 610), (510, 770)]), radius=40 * S, fill=(32, 33, 35))
    d.ellipse(P([(330, 635), (440, 745)]), fill=(210, 214, 216))
    d.ellipse(P([(350, 655), (420, 725)]), fill=(250, 248, 236))
    d.ellipse(P([(455, 660), (490, 695)]), fill=(208, 102, 47))
    d.rounded_rectangle(P([(300, 600), (500, 616)]), radius=6 * S, fill=(60, 62, 66))
    finish(img, name)


# ---------- hero ----------

def hero():
    w, h = 1920, 900
    img = Image.new("RGB", (w * S, h * S))
    d = ImageDraw.Draw(img)
    top, bottom = (232, 214, 196), (205, 216, 218)
    for y in range(h * S):
        t = y / (h * S)
        d.line([(0, y), (w * S, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom, strict=True)))
    sun = Image.new("L", img.size, 0)
    ImageDraw.Draw(sun).ellipse([1250 * S, 180 * S, 1450 * S, 380 * S], fill=255)
    img.paste((246, 222, 186), mask=sun.filter(ImageFilter.GaussianBlur(40 * S)))
    ImageDraw.Draw(img).ellipse([1300 * S, 230 * S, 1400 * S, 330 * S], fill=(250, 232, 200))
    rnd = random.Random(11)
    ridges = []
    layers = [((176, 190, 196), 420, 120), ((132, 152, 160), 500, 150), ((88, 112, 112), 600, 170),
              ((52, 78, 66), 720, 140), ((31, 61, 43), 820, 90)]
    for i, (color, base, amp) in enumerate(layers):
        f1, f2, p1, p2 = rnd.uniform(0.002, 0.004), rnd.uniform(0.007, 0.011), rnd.uniform(0, 6), rnd.uniform(0, 6)
        def ridge(x, base=base, amp=amp, f1=f1, f2=f2, p1=p1, p2=p2):
            return base - amp * (0.65 * abs(math.sin(x * f1 + p1)) + 0.35 * abs(math.sin(x * f2 + p2)))

        ridges.append(ridge)
        pts = [(0, h)] + [(x, ridge(x) + rnd.uniform(-4, 4)) for x in range(0, w + 24, 24)] + [(w, h)]
        layer = Image.new("L", img.size, 0)
        ImageDraw.Draw(layer).polygon([(px * S, py * S) for px, py in pts], fill=255)
        img.paste(color, mask=layer)
        if i < 3:  # fog band
            fog = Image.new("L", img.size, 0)
            ImageDraw.Draw(fog).rectangle([0, (base + 30) * S, w * S, (base + 90) * S], fill=110)
            img.paste((236, 232, 226), mask=fog.filter(ImageFilter.GaussianBlur(30 * S)))
    # a hiker on the near ridge
    d = ImageDraw.Draw(img)
    hx = 1180
    hy = int(min(ridges[3](x) for x in range(hx - 12, hx + 12)) + 2)
    d.ellipse(P([(hx - 7, hy - 62), (hx + 7, hy - 48)]), fill=(25, 40, 32))
    d.polygon(P([(hx - 10, hy - 48), (hx + 10, hy - 48), (hx + 9, hy - 18), (hx - 9, hy - 18)]), fill=(25, 40, 32))
    d.rectangle(P([(hx - 15, hy - 46), (hx - 5, hy - 22)]), fill=(200, 86, 43))
    d.line(P([(hx - 4, hy - 18), (hx - 10, hy)]), fill=(25, 40, 32), width=5 * S)
    d.line(P([(hx + 4, hy - 18), (hx + 9, hy)]), fill=(25, 40, 32), width=5 * S)
    d.line(P([(hx + 9, hy - 40), (hx + 20, hy + 2)]), fill=(25, 40, 32), width=2 * S)
    img = img.resize((w, h), Image.LANCZOS)
    img.save(OUT / "hero.jpg", quality=86, optimize=True, progressive=True)


if __name__ == "__main__":
    top("alpine-shell-jacket", (47, 90, 60), "shell")
    top("summit-down-jacket", (35, 50, 79), "down")
    top("trail-fleece", (150, 150, 142), "fleece")
    top("merino-base-layer", (58, 61, 64), "base")
    boot("ridge-hiking-boots", (122, 82, 54))
    boot("trail-runner-shoes", (141, 145, 150), trail=True)
    pack("fjell-daypack-28", (107, 112, 70))
    pack("expedition-pack-55", (74, 77, 82), tall=True, accent=(200, 86, 43))
    bottle("thermo-bottle", (47, 90, 60))
    poles("carbon-trekking-poles")
    beanie("wool-beanie", (181, 83, 47))
    headlamp("headlamp-400")
    hero()
    print("images written to", OUT)
