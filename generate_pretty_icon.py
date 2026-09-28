#!/usr/bin/env python3
"""
Generate a sleek, modern, high-res dark-themed application icon for FKVideoPlayer:
- Rounded glass squircle with subtle specular highlight and deep radial backdrop
- Vibrant neon gradient play arrow (electric cyan -> vivid magenta/violet)
- Sleek digital creative flare (stylized brush/spark overlay)
- Export to 512x512 icon.png and multi-resolution icon.ico (16 to 256px)
"""

import math
from PIL import Image, ImageDraw, ImageFilter

def create_pretty_icon(size=512):
    # Create canvas with transparency
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    margin = size * 0.08
    rect = [margin, margin, size - margin, size - margin]
    radius = size * 0.22

    # 1. Subtle glowing outer drop shadow
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(shadow)
    s_margin = size * 0.07
    s_draw.rounded_rectangle(
        [s_margin, s_margin + size * 0.02, size - s_margin, size - s_margin + size * 0.02],
        radius=radius,
        fill=(0, 122, 255, 110)
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(size * 0.06))
    img.paste(shadow, (0, 0), shadow)

    # 2. Base Squircle with dark metallic / glass gradient
    base = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    b_draw = ImageDraw.Draw(base)
    b_draw.rounded_rectangle(rect, radius=radius, fill=(20, 20, 28, 255))

    # Add gradient to base
    grad_mask = Image.new("L", (size, size), 0)
    g_draw = ImageDraw.Draw(grad_mask)
    g_draw.rounded_rectangle(rect, radius=radius, fill=255)

    grad = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    for y in range(int(margin), int(size - margin)):
        factor = (y - margin) / (size - 2 * margin)
        # Deep blue-gray to deep violet-black
        r = int(22 + 18 * (1 - factor))
        g = int(24 + 10 * (1 - factor))
        b = int(38 + 25 * factor)
        for x in range(int(margin), int(size - margin)):
            grad.putpixel((x, y), (r, g, b, 255))
    base.paste(grad, (0, 0), grad_mask)

    # 3. Outer border with delicate glowing stroke
    b_draw = ImageDraw.Draw(base)
    b_draw.rounded_rectangle(rect, radius=radius, outline=(70, 90, 140, 180), width=int(size * 0.012))

    # Top specular rim highlight
    rim_mask = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    r_draw = ImageDraw.Draw(rim_mask)
    r_draw.rounded_rectangle([margin + 2, margin + 2, size - margin - 2, margin + size * 0.16],
                             radius=radius * 0.8, fill=(255, 255, 255, 35))
    rim_mask = rim_mask.filter(ImageFilter.GaussianBlur(size * 0.015))
    base.paste(rim_mask, (0, 0), rim_mask)

    img.paste(base, (0, 0), base)

    # 4. Play Button Geometry with Neon Gradient
    cx, cy = size * 0.52, size * 0.50
    pr = size * 0.22

    # Play triangle points
    p1 = (cx - pr * 0.70, cy - pr * 0.95)
    p2 = (cx + pr * 1.05, cy)
    p3 = (cx - pr * 0.70, cy + pr * 0.95)

    # Triangle glow
    tglow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    tg_draw = ImageDraw.Draw(tglow)
    tg_draw.polygon([p1, p2, p3], fill=(0, 200, 255, 140))
    tglow = tglow.filter(ImageFilter.GaussianBlur(size * 0.045))
    img.paste(tglow, (0, 0), tglow)

    # Triangle gradient fill: electric cyan (top-left) to vivid violet-pink (bottom-right)
    tri_img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    t_mask = Image.new("L", (size, size), 0)
    tm_draw = ImageDraw.Draw(t_mask)
    tm_draw.polygon([p1, p2, p3], fill=255)

    min_x, max_x = int(cx - pr * 0.8), int(cx + pr * 1.1)
    min_y, max_y = int(cy - pr * 1.0), int(cy + pr * 1.0)
    for y in range(min_y, max_y):
        for x in range(min_x, max_x):
            if not t_mask.getpixel((x, y)):
                continue
            fx = (x - min_x) / max(1, (max_x - min_x))
            fy = (y - min_y) / max(1, (max_y - min_y))
            # Cyan -> Magenta
            r = int(0 + 240 * fx + 50 * fy)
            g = int(210 * (1 - fx * 0.6) + 30 * fy)
            b = int(255)
            tri_img.putpixel((x, y), (min(255, r), min(255, g), min(255, b), 255))

    img.paste(tri_img, (0, 0), t_mask)

    # Draw rounded triangle contour for ultra-clean anti-aliased bevel
    line_draw = ImageDraw.Draw(img)
    line_draw.line([p1, p2, p3, p1], fill=(255, 255, 255, 180), width=int(size * 0.015))

    # 5. Stylized Creative Spark / Brush Accent at corner
    accent_center = (cx + pr * 0.88, cy - pr * 0.65)
    ax, ay = accent_center
    asize = size * 0.05
    # Draw four-pointed star / lens flare
    spark = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sp_draw = ImageDraw.Draw(spark)
    sp_draw.ellipse([ax - asize*0.5, ay - asize*0.5, ax + asize*0.5, ay + asize*0.5], fill=(255, 255, 255, 240))
    sp_draw.polygon([(ax - asize*1.8, ay), (ax, ay - asize*0.3), (ax + asize*1.8, ay), (ax, ay + asize*0.3)], fill=(255, 255, 255, 220))
    sp_draw.polygon([(ax, ay - asize*1.8), (ax + asize*0.3, ay), (ax, ay + asize*1.8), (ax - asize*0.3, ay)], fill=(255, 255, 255, 220))
    spark_blur = spark.filter(ImageFilter.GaussianBlur(size * 0.008))
    img.paste(spark_blur, (0, 0), spark_blur)
    img.paste(spark, (0, 0), spark)

    return img

if __name__ == "__main__":
    icon = create_pretty_icon(512)
    icon.save("icon.png", "PNG")
    print("Saved icon.png (512x512)")

    # Multi-resolution ICO
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    icon.save("icon.ico", format="ICO", sizes=sizes)
    print("Saved icon.ico with multi-resolution:", sizes)
