"""Render a monochrome Noto Emoji glyph and title into one RLCD frame."""

import base64
import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path

import aiohttp
from core.device_use_turn import call_metadata, control_url
from PIL import Image, ImageDraw, ImageFont

from plugins_func.register import Action, ActionResponse, ToolType, register_function


WIDTH = 160
HEIGHT = 120
EMOJI_FONT = Path(__file__).resolve().parents[2] / "assets/fonts/NotoEmoji-wght.ttf"
NERD_FONT = Path(__file__).resolve().parents[2] / "assets/fonts/SymbolsNerdFontMono-Regular.ttf"
NERD_NAMES = Path(__file__).resolve().parents[2] / "assets/fonts/nerd-glyphnames.json"
SF_FONT = Path("/System/Library/Fonts/SFNS.ttf")

# Names help models that do not reliably emit Unicode Emoji. The images still
# come from the installed font; there are no hard-coded drawing templates.
ICON_CATALOG = dict(
    cat="🐱", dog="🐶", panda="🐼", rabbit="🐰", bear="🐻", fox="🦊",
    tiger="🐯", lion="🦁", cow="🐮", pig="🐷", mouse="🐭", frog="🐸",
    monkey="🐵", bird="🐦", owl="🦉", butterfly="🦋", bee="🐝", fish="🐟",
    dolphin="🐬", whale="🐳", turtle="🐢", octopus="🐙", dinosaur="🦖",
    horse="🐴", unicorn="🦄", penguin="🐧", chicken="🐔", snake="🐍",
    sun="☀️", moon="🌙", star="⭐", cloud="☁️", rain="🌧️", snow="❄️",
    lightning="⚡", rainbow="🌈", fire="🔥", leaf="🍃", tree="🌳",
    flower="🌼", rose="🌹", mushroom="🍄", mountain="⛰️", earth="🌍",
    apple="🍎", banana="🍌", strawberry="🍓", watermelon="🍉",
    pizza="🍕", burger="🍔", cake="🎂", coffee="☕", tea="🍵",
    bread="🍞", egg="🥚", candy="🍬", icecream="🍦",
    house="🏠", castle="🏰", tent="⛺", car="🚗", bus="🚌",
    train="🚆", airplane="✈️", rocket="🚀", ship="🚢", bicycle="🚲",
    heart="❤️", smile="😊", laugh="😄", wink="😉", sad="😢",
    angry="😠", surprise="😮", robot="🤖", ghost="👻", alien="👽",
    crown="👑", music="🎵", camera="📷", book="📖", lightbulb="💡",
    clock="⏰", bell="🔔", gift="🎁", balloon="🎈", game="🎮",
    gear="⚙️", key="🔑", lock="🔒", phone="📱", computer="💻",
    wrench="🔧", hammer="🔨", check="✅", warning="⚠️", question="❓",
)
ICON_ALIASES = {
    "猫": "cat", "猫咪": "cat", "小猫": "cat", "狗": "dog", "小狗": "dog",
    "太阳": "sun", "月亮": "moon", "星星": "star", "房子": "house",
    "爱心": "heart", "心": "heart", "笑脸": "smile", "机器人": "robot",
    "汽车": "car", "飞机": "airplane", "火箭": "rocket", "花": "flower",
}

bitmap_desc = {
    "type": "function",
    "function": {
        "name": "draw_rlcd_bitmap",
        "description": (
            "用户要在 RLCD 屏幕画图标或带标题的图时调用。选择一个 Emoji 图标（如 🐱）"
            "或图标名，例如 cat,dog,panda,rabbit,fox,sun,moon,star,rainbow,flower,"
            "house,car,rocket,heart,smile,robot,music,camera,book,clock,gift。"
            "默认使用黑白 Noto Emoji；技术/系统图标可选 Nerd Font，"
            "图标名如 md-cat,md-home,md-robot,fa-rocket。给出简短标题。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "icon": {
                    "type": "string",
                    "description": "Emoji 字符或图库中的英文图标名；例如 🐱 或 cat。",
                },
                "title": {
                    "type": "string", "maxLength": 24,
                    "description": "图下方显示的短标题，建议不超过 8 个汉字。",
                },
                "durationMs": {
                    "type": "integer", "minimum": 1000, "maximum": 30000,
                    "description": "显示时长，省略时为 8 秒。",
                },
                "source": {
                    "type": "string", "enum": ["emoji", "nerd"],
                    "description": "图标来源，默认 emoji；Nerd Font 图标名需以 md-/fa-/cod- 等开头。",
                },
                "titleFont": {
                    "type": "string", "enum": ["auto", "sf", "cjk"],
                    "description": "标题字体：auto 自动选，sf 使用本机 SF 字体排英文，cjk 使用中文字体。",
                },
            },
            "required": ["icon", "title"],
        },
    },
}


def _resolve_icon(icon: str) -> str:
    if not isinstance(icon, str):
        raise ValueError("icon must be a string")
    name = icon.strip().lower()
    name = ICON_ALIASES.get(name, name)
    if name in ICON_CATALOG:
        return ICON_CATALOG[name]
    if not 1 <= len(icon) <= 8 or any(ord(char) < 32 for char in icon):
        raise ValueError("unknown icon")
    # One Emoji may contain variation selectors, skin tones, or ZWJ characters.
    if not any(ord(char) >= 0x2000 for char in icon):
        raise ValueError("unknown icon")
    return icon


@lru_cache(maxsize=3)
def _title_font_path(style: str) -> Path:
    if style == "sf":
        path = Path(os.environ.get("RLCD_SF_FONT_FILE", str(SF_FONT))).expanduser()
        if not path.is_file():
            raise ValueError("SF font is unavailable on this host")
        return path
    configured = os.environ.get("RLCD_TITLE_FONT_FILE")
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise ValueError("RLCD_TITLE_FONT_FILE does not exist")
        return path
    result = subprocess.run(
        ["fc-match", "-f", "%{file}", "PingFang SC"],
        capture_output=True, text=True, timeout=3, check=False,
    )
    path = Path(result.stdout.strip())
    if result.returncode != 0 or not path.is_file():
        raise ValueError("no Chinese title font found; set RLCD_TITLE_FONT_FILE")
    return path


@lru_cache(maxsize=1)
def _emoji_font():
    path = Path(os.environ.get("RLCD_EMOJI_FONT_FILE", str(EMOJI_FONT))).expanduser()
    if not path.is_file():
        raise ValueError("no Emoji font found; set RLCD_EMOJI_FONT_FILE")
    return ImageFont.truetype(str(path), 68)


@lru_cache(maxsize=1)
def _nerd_font():
    path = Path(os.environ.get("RLCD_NERD_FONT_FILE", str(NERD_FONT))).expanduser()
    if not path.is_file():
        raise ValueError("Nerd Font is unavailable on this host")
    return ImageFont.truetype(str(path), 72)


@lru_cache(maxsize=1)
def _nerd_names():
    return json.loads(NERD_NAMES.read_text(encoding="utf-8"))


def _mono_glyph_bitmap(glyph: str, font, *, emoji: bool) -> Image.Image:
    scratch = Image.new("L", (256, 96), 255)
    painter = ImageDraw.Draw(scratch)
    box = painter.textbbox((0, 0), glyph, font=font)
    if not box or box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError("Emoji glyph is empty")
    if box[2] - box[0] > 110:
        raise ValueError("icon is too wide for this screen")
    # Missing glyphs use the font's narrow .notdef box instead of a full Emoji.
    if emoji and box[2] - box[0] < 45:
        raise ValueError("Emoji is absent from Noto Emoji")
    painter.text((-box[0], -box[1]), glyph, font=font, fill=0)
    glyph_image = scratch.crop((0, 0, box[2] - box[0], box[3] - box[1]))
    glyph_image.thumbnail((78, 73), Image.Resampling.LANCZOS)
    if not glyph_image.getextrema()[0] < 240:
        raise ValueError("Emoji produced no visible pixels")
    # Both source fonts are monochrome. Threshold only anti-aliasing at the
    # final 1-bit resolution; no color to grayscale conversion is involved.
    return glyph_image.point(lambda pixel: 0 if pixel < 190 else 255).convert("1")


def render_icon_title(icon: str, title: str, source: str = "emoji",
                      title_font: str = "auto") -> bytes:
    if source == "emoji":
        symbol = _mono_glyph_bitmap(_resolve_icon(icon), _emoji_font(), emoji=True)
    elif source == "nerd":
        if not isinstance(icon, str):
            raise ValueError("Nerd icon name must be a string")
        name = icon.removeprefix("nf-")
        entry = _nerd_names().get(name)
        if not isinstance(entry, dict) or "char" not in entry:
            raise ValueError("unknown Nerd Font icon")
        symbol = _mono_glyph_bitmap(entry["char"], _nerd_font(), emoji=False)
    else:
        raise ValueError("unknown icon source")
    if not isinstance(title, str) or not title.strip() or len(title) > 24 or \
            any(ord(char) < 32 for char in title):
        raise ValueError("title must contain 1..24 printable characters")
    if title_font not in ("auto", "sf", "cjk"):
        raise ValueError("unknown title font")
    frame = Image.new("1", (WIDTH, HEIGHT), 1)
    frame.paste(symbol, ((WIDTH - symbol.width) // 2, (77 - symbol.height) // 2))

    title = title.strip()
    # The locally installed SF font has no Chinese glyphs. Use the CJK font
    # for any non-ASCII title even when SF was selected.
    style = "sf" if title_font != "cjk" and title.isascii() else "cjk"
    font_path = _title_font_path(style)
    drawer = ImageDraw.Draw(frame)
    display = title
    for size in range(23, 12, -1):
        font = ImageFont.truetype(str(font_path), size)
        if drawer.textbbox((0, 0), display, font=font)[2] <= WIDTH - 10:
            break
    else:
        while title and drawer.textbbox((0, 0), title + "…", font=font)[2] > WIDTH - 10:
            title = title[:-1]
        display = title + "…"
    box = drawer.textbbox((0, 0), display, font=font)
    drawer.text(((WIDTH - (box[2] - box[0])) // 2 - box[0], 86 - box[1]),
                display, font=font, fill=0)
    return frame.tobytes()


@register_function("draw_rlcd_bitmap", bitmap_desc, ToolType.SYSTEM_CTL)
async def draw_rlcd_bitmap(conn, icon: str, title: str, durationMs: int = 8000,
                           source: str = "emoji", titleFont: str = "auto"):
    allowed = {
        item.strip().lower()
        for item in os.environ.get("XIAOZHI_DEVICE_USE_DEVICE_IDS", "").split(",")
        if item.strip()
    }
    if conn.headers.get("device-id", "").lower() not in allowed:
        return ActionResponse(Action.RESPONSE, response="当前设备未获准绘图")
    if type(durationMs) is not int or not 1000 <= durationMs <= 30000:
        return ActionResponse(Action.RESPONSE, response="绘图时长无效")
    try:
        bits = render_icon_title(icon, title, source, titleFont)
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        return ActionResponse(Action.RESPONSE, response=f"绘图参数无效：{error}")
    token_path = os.environ.get("RLCD_DEVICE_USE_TOKEN_FILE", "")
    if not token_path or not Path(token_path).is_file():
        return ActionResponse(Action.RESPONSE, response="Device Use Host 令牌未配置")
    token = Path(token_path).read_text(encoding="ascii").strip()
    if len(token) != 64:
        return ActionResponse(Action.RESPONSE, response="Device Use Host 令牌无效")
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            headers = {"Authorization": f"Bearer {token}"}
            metadata = await call_metadata(conn, session, headers)
            async with session.post(
                control_url("/bitmap"),
                json={"width": WIDTH, "height": HEIGHT, "durationMs": durationMs,
                      "dataBase64": base64.b64encode(bits).decode("ascii"), **metadata},
                headers=headers,
            ) as response:
                result = await response.json()
                if response.status != 200 or not result.get("accepted"):
                    raise RuntimeError(result.get("error", "设备未接受位图"))
    except (aiohttp.ClientError, TimeoutError, ValueError, RuntimeError) as error:
        return ActionResponse(Action.RESPONSE, response=f"绘图失败：{error}")
    return ActionResponse(Action.RECORD, result=f"已在屏幕显示{title.strip()}")
