import json
import os
import math
import time
import sys
import random
import subprocess
import io
from tournament.graphics.runner import GRAPHICS_DIR
from tournament import posts
from tournament.webhook import Webhook
from pathlib import Path

try: 
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    import shutil
    uv = shutil.which("uv")
    # quietly install it.
    subprocess.run([uv, "add", "pillow"], check=True)
    from PIL import Image, ImageDraw, ImageFont

##### start setup #####

FILES = {
    "title.png": "https://files.catbox.moe/aw1utu.png",
    "logo.png": "https://files.catbox.moe/g92tad.png",
    "arial.ttf": "https://cdn.jsdelivr.net/gh/taveevut/Windows-10-Fonts-Default@master/arial.ttf",
    "arialbd.ttf": "https://cdn.jsdelivr.net/gh/taveevut/Windows-10-Fonts-Default@master/arialbd.ttf",
}

def download(url: str, path: str) -> bool:
    """downloads a file from a url to a path"""
    try:
        subprocess.run(["curl", "-fsSL", url, "-o", path], check=True)
        return True
    except subprocess.CalledProcessError:
        return False


def verify_dependencies() -> bool:
    """verifies that all dependencies are present"""
    download_statuses = []
    for file, url in FILES.items():
        file_path = GRAPHICS_DIR / file
        if not file_path.exists():
            download_statuses.append(download(url, str(file_path)))

    return all(download_statuses)

##### end setup #####

LOGO_FILE = GRAPHICS_DIR / "logo.png"
TITLE_FILE = GRAPHICS_DIR / "title.png"

REGULAR = GRAPHICS_DIR / "arial.ttf"
BOLD = GRAPHICS_DIR / "arialbd.ttf"

WIDTH = 1920
HEIGHT = 1080

BRACKET_WIDTH = 3840
BRACKET_HEIGHT = 2160

BG = (5, 4, 13)
WHITE = (244, 245, 255)
MUTED = (140, 148, 175)
MUTED_BOARD = (160, 168, 195)
PURPLE = (190, 72, 255)
BLUE = (45, 180, 255)
CYAN = (70, 215, 255)
GREEN = (75, 230, 160)
RED = (240, 70, 85)
RED_BOARD = (255, 95, 125)
GOLD = (255, 215, 0)
SILVER = (192, 192, 192)
BRONZE = (205, 127, 50)
BYE = (255, 204, 82)

GROUP_COLORS = [
    PURPLE,
    BLUE,
    (210, 80, 255),
    (65, 145, 255),
    (160, 75, 255),
    (55, 200, 235),
]


def read_json(file_path: Path) -> dict:
    if not file_path.exists():
        raise FileNotFoundError(f"Missing JSON file: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


# the matches are stored as {"teams": [{"idx", "name", "score", "series"}, ...], "winner_idx": ...},
# the drawing code below reads flat "team1" / "team2" / "winner" values, this converts one into the other.


def adapt_match(match: dict, missing=None) -> dict:
    """adds team1, team2 and winner (names) to a stored match."""
    teams = match.get("teams")
    if not isinstance(teams, list) or len(teams) < 2:
        return match
    names = [team.get("name") for team in teams[:2]]
    winner_idx = match.get("winner_idx")
    winner = names[winner_idx] if winner_idx in (0, 1) else None
    return {
        **match,
        "team1": names[0] if names[0] is not None else missing,
        "team2": names[1] if names[1] is not None else missing,
        "winner": winner,
    }


def adapt_round(round_data: dict, missing=None) -> dict:
    """adapts all the matches of a round."""
    matches = round_data.get("matches", {})
    return {
        **round_data,
        "matches": {key: adapt_match(match, missing) for key, match in matches.items()},
    }


def adapt_group_stage(data: dict) -> dict:
    """adapts all the matches of all the groups, a missing team (None) is a bye there."""
    groups = {}
    for group_id, group in data.get("groups", {}).items():
        rounds = {key: adapt_round(rd) for key, rd in group.get("rounds", {}).items()}
        groups[group_id] = {**group, "rounds": rounds}
    return {**data, "groups": groups}


def get_font(size, bold=False):
    path = BOLD if bold else REGULAR
    return ImageFont.truetype(path, size)


def text_size(draw, text, font):
    b = draw.textbbox((0, 0), text, font=font)
    return b[2] - b[0], b[3] - b[1]


def fit_font(draw, text, max_width, size, bold=True, minimum=8):
    while size > minimum:
        f = get_font(size, bold)
        if text_size(draw, text, f)[0] <= max_width:
            return f
        size -= 1
    return get_font(minimum, bold)


def truncate_text(draw, text, max_width, font):
    if text_size(draw, text, font)[0] <= max_width:
        return text
    for i in range(len(text), 0, -1):
        truncated = text[:i] + "..."
        if text_size(draw, truncated, font)[0] <= max_width:
            return truncated
    return "..."


def center_text(draw, cx, cy, text, font, fill):
    tw, th = text_size(draw, text, font)
    draw.text((cx - tw / 2, cy - th / 2), text, font=font, fill=fill)


def center_text_box(draw, box, text, font, fill):
    x1, y1, x2, y2 = box
    tw, th = text_size(draw, text, font)
    draw.text(
        (x1 + (x2 - x1 - tw) / 2, y1 + (y2 - y1 - th) / 2), text, font=font, fill=fill
    )


def draw_star_icon(d, cx, cy, size, fill):
    points = []
    for i in range(10):
        r = size if i % 2 == 0 else size / 2.5
        angle = i * math.pi / 5 - math.pi / 2
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    d.polygon(points, fill=fill)


def draw_rounded_rect(
    img, box, radius=16, fill=(10, 9, 22, 230), outline=(190, 72, 255, 180), width=2
):
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    d.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
    img.alpha_composite(overlay)


def draw_alpha_rounded_rectangle(
    img, box, radius=12, fill=(10, 9, 22, 140), outline=None, width=1
):
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    d.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
    img.alpha_composite(overlay)


def make_background(
    width,
    height,
    ellipse1,
    ellipse2,
    star_count=0,
    star_seed=19,
    star_radius_choices=(1, 1, 1, 2),
    star_alpha_range=(25, 95),
):
    img = Image.new("RGBA", (width, height), (*BG, 255))
    d = ImageDraw.Draw(img)

    top, bottom = (4, 3, 11), (12, 7, 28)
    for y in range(height):
        t = y / (height - 1)
        c = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        d.line((0, y, width, y), fill=(*c, 255))

    box1, color1, w1 = ellipse1
    d.ellipse(box1, outline=color1, width=w1)
    box2, color2, w2 = ellipse2
    d.ellipse(box2, outline=color2, width=w2)

    if star_count:
        random.seed(star_seed)
        for _ in range(star_count):
            x = random.randrange(width)
            y = random.randrange(height)
            r = random.choice(star_radius_choices)
            a = random.randrange(*star_alpha_range)
            d.ellipse((x - r, y - r, x + r, y + r), fill=(150, 130, 220, a))

    return img


def add_watermark(
    img, logo_file, canvas_w, canvas_h, alpha, thumb_size=(800, 800), y_pos=None
):
    if not os.path.exists(logo_file):
        return
    logo = Image.open(logo_file).convert("RGBA")
    logo.thumbnail(thumb_size, Image.Resampling.LANCZOS)
    alpha_channel = logo.getchannel("A").point(lambda a: int(a * alpha))
    logo.putalpha(alpha_channel)
    x = (canvas_w - logo.width) // 2
    y = y_pos if y_pos is not None else (canvas_h - logo.height) // 2
    img.alpha_composite(logo, (x, y))


def generate_match_result(data: dict, webhook: Webhook) -> None:
    img = make_background(
        WIDTH,
        HEIGHT,
        ellipse1=((-200, -300, WIDTH + 200, HEIGHT + 400), (*PURPLE, 25), 4),
        ellipse2=((-100, -200, WIDTH + 100, HEIGHT + 300), (*BLUE, 20), 3),
    )
    add_watermark(img, LOGO_FILE, WIDTH, HEIGHT, alpha=0.15)
    d = ImageDraw.Draw(img)
    center_x = WIDTH // 2

    top_y = 50
    if os.path.exists(TITLE_FILE):
        try:
            title_img = Image.open(TITLE_FILE).convert("RGBA")
            title_img.thumbnail((650, 200), Image.Resampling.LANCZOS)
            img.alpha_composite(title_img, ((WIDTH - title_img.width) // 2, top_y))
            top_y += title_img.height + 25
        except Exception:
            top_y += 15
    top_y -= 20

    banner_font = get_font(34, bold=True)
    header_text = "MATCH RESULT"
    tw, th = text_size(d, header_text, banner_font)

    badge_w, badge_h = tw + 80, th + 28
    badge_box = (
        center_x - badge_w // 2,
        top_y,
        center_x + badge_w // 2,
        top_y + badge_h,
    )
    glow_box = (badge_box[0] - 6, badge_box[1] - 6, badge_box[2] + 6, badge_box[3] + 6)

    draw_rounded_rect(
        img, glow_box, radius=14, fill=(255, 215, 0, 25), outline=(*GOLD, 100), width=1
    )
    draw_rounded_rect(
        img, badge_box, radius=10, fill=(20, 16, 38, 245), outline=(*GOLD, 230), width=2
    )
    center_text(d, center_x, top_y + badge_h // 2, header_text, banner_font, GOLD)
    draw_star_icon(d, center_x - tw / 2 - 25, top_y + badge_h // 2, 8, GOLD)
    draw_star_icon(d, center_x + tw / 2 + 25, top_y + badge_h // 2, 8, GOLD)

    top_y += badge_h + 35

    card_w, card_h = 1400, 520
    card_box = (center_x - card_w // 2, top_y, center_x + card_w // 2, top_y + card_h)
    draw_rounded_rect(
        img,
        card_box,
        radius=20,
        fill=(12, 10, 26, 235),
        outline=(*PURPLE, 200),
        width=3,
    )

    vs_font = get_font(42, bold=True)
    vs_sub_font = get_font(28, bold=True)

    t1_is_winner = data["winner"] == data["team1"]
    t2_is_winner = data["winner"] == data["team2"]

    t1_color = GREEN if t1_is_winner else RED
    center_text(d, center_x - 320, top_y + 70, f"{data['team1']}", vs_font, t1_color)
    center_text(d, center_x, top_y + 70, "VS", vs_sub_font, CYAN)
    t2_color = GREEN if t2_is_winner else RED
    center_text(d, center_x + 320, top_y + 70, f"{data['team2']}", vs_font, t2_color)

    d.line(
        [(center_x - 620, top_y + 125), (center_x + 620, top_y + 125)],
        fill=(*CYAN, 100),
        width=2,
    )

    box_h = 240
    box_y = top_y + 160
    box1_coords = (center_x - 620, box_y, center_x - 40, box_y + box_h)
    box2_coords = (center_x + 40, box_y, center_x + 620, box_y + box_h)

    b1_bg = (20, 45, 30, 210) if t1_is_winner else (45, 18, 24, 210)
    b1_outline = (*GREEN, 200) if t1_is_winner else (*RED, 180)
    b2_bg = (20, 45, 30, 210) if t2_is_winner else (45, 18, 24, 210)
    b2_outline = (*GREEN, 200) if t2_is_winner else (*RED, 180)

    draw_rounded_rect(
        img, box1_coords, radius=14, fill=b1_bg, outline=b1_outline, width=2
    )
    draw_rounded_rect(
        img, box2_coords, radius=14, fill=b2_bg, outline=b2_outline, width=2
    )

    label_font = get_font(20, bold=True)
    val_font = get_font(46, bold=True)

    center_text(d, center_x - 330, box_y + 45, "ROUNDS WON", label_font, MUTED)
    center_text(d, center_x - 330, box_y + 95, str(data["score1"]), val_font, WHITE)
    center_text(d, center_x - 330, box_y + 155, "SERIES WON", label_font, MUTED)
    center_text(d, center_x - 330, box_y + 195, str(data["series1"]), label_font, CYAN)

    center_text(d, center_x + 330, box_y + 45, "ROUNDS WON", label_font, MUTED)
    center_text(d, center_x + 330, box_y + 95, str(data["score2"]), val_font, WHITE)
    center_text(d, center_x + 330, box_y + 155, "SERIES WON", label_font, MUTED)
    center_text(d, center_x + 330, box_y + 195, str(data["series2"]), label_font, CYAN)

    winner_text = f"WINNER: {data['winner'].upper()}"
    tag_font = get_font(22, bold=True)
    center_text(d, center_x, top_y + card_h - 55, winner_text, tag_font, GOLD)

    footer_font = get_font(22, bold=True)
    center_text(
        d, center_x, HEIGHT - 55, f"SEASON {data['season_id']}", footer_font, MUTED
    )

    with io.BytesIO() as image_buffer:
        img.convert("RGB").save(image_buffer, "PNG", optimize=True)
        image_buffer.seek(0)
        files = webhook.create("match_result.png", image_buffer)
        webhook.send("results", f"match_result-{data["key"]}", files)


def generate_player_standings(
    season_id: str, stats_file: Path, webhook: Webhook
) -> None:
    raw_data = read_json(stats_file)

    players = list(raw_data.values())
    players.sort(key=lambda x: x.get("rank", 999))
    top_players = players[:10]

    img = make_background(
        WIDTH,
        HEIGHT,
        ellipse1=((-200, -300, WIDTH + 200, HEIGHT + 400), (*PURPLE, 25), 4),
        ellipse2=((-100, -200, WIDTH + 100, HEIGHT + 300), (*BLUE, 20), 3),
    )
    add_watermark(img, LOGO_FILE, WIDTH, HEIGHT, alpha=0.12)
    d = ImageDraw.Draw(img)
    center_x = WIDTH // 2

    top_y = 35
    if os.path.exists(TITLE_FILE):
        try:
            title_img = Image.open(TITLE_FILE).convert("RGBA")
            title_img.thumbnail((580, 180), Image.Resampling.LANCZOS)
            img.alpha_composite(title_img, ((WIDTH - title_img.width) // 2, top_y))
            top_y += title_img.height + 15
        except Exception:
            top_y += 10

    banner_font = get_font(28, bold=True)
    header_text = f"PLAYERS STANDINGS - SEASON {season_id}"
    tw, th = text_size(d, header_text, banner_font)

    badge_w, badge_h = tw + 100, th + 22
    badge_box = (
        center_x - badge_w // 2,
        top_y,
        center_x + badge_w // 2,
        top_y + badge_h,
    )
    glow_box = (badge_box[0] - 5, badge_box[1] - 5, badge_box[2] + 5, badge_box[3] + 5)

    draw_rounded_rect(
        img, glow_box, radius=12, fill=(255, 215, 0, 20), outline=(*GOLD, 80), width=1
    )
    draw_rounded_rect(
        img, badge_box, radius=10, fill=(20, 16, 38, 245), outline=(*GOLD, 230), width=2
    )
    center_text(d, center_x, top_y + badge_h // 2, header_text, banner_font, GOLD)
    draw_star_icon(d, center_x - tw / 2 - 25, top_y + badge_h // 2, 8, GOLD)
    draw_star_icon(d, center_x + tw / 2 + 25, top_y + badge_h // 2, 8, GOLD)

    top_y += badge_h + 25

    board_w = 1520
    board_box = (center_x - board_w // 2, top_y, center_x + board_w // 2, top_y + 730)
    draw_rounded_rect(
        img,
        board_box,
        radius=18,
        fill=(12, 10, 26, 235),
        outline=(*PURPLE, 180),
        width=3,
    )

    col_rank = center_x - 680
    col_name = center_x - 480
    col_score = center_x + 30
    col_kills = center_x + 230
    col_deaths = center_x + 380
    col_kd = center_x + 530
    col_games = center_x + 670

    header_y = top_y + 22
    col_font = get_font(18, bold=True)
    d.text((col_rank, header_y), "RANK", font=col_font, fill=CYAN)
    d.text((col_name, header_y), "PLAYER NAME", font=col_font, fill=CYAN)
    d.text((col_score, header_y), "SCORE", font=col_font, fill=CYAN)
    d.text((col_kills, header_y), "KILLS", font=col_font, fill=CYAN)
    d.text((col_deaths, header_y), "DEATHS", font=col_font, fill=CYAN)
    d.text((col_kd, header_y), "K/D", font=col_font, fill=CYAN)
    d.text((col_games, header_y), "GAMES", font=col_font, fill=CYAN)

    d.line(
        [(center_x - 720, top_y + 55), (center_x + 720, top_y + 55)],
        fill=(*CYAN, 90),
        width=2,
    )

    row_y = top_y + 68
    row_h = 63
    row_font = get_font(21, bold=True)

    for i, p in enumerate(top_players):
        rank = p.get("rank", i + 1)
        name = p.get("name", "Unknown")
        score = p.get("score", 0)
        kills = p.get("kills", 0)
        deaths = p.get("deaths", 0)
        games = p.get("games", 0)
        kd_val = round(kills / max(1, deaths), 2)

        if rank == 1:
            rank_str, row_bg, row_outline, text_color = (
                "#1",
                (40, 32, 12, 220),
                (*GOLD, 220),
                GOLD,
            )
        elif rank == 2:
            rank_str, row_bg, row_outline, text_color = (
                "#2",
                (30, 32, 40, 220),
                (*SILVER, 200),
                SILVER,
            )
        elif rank == 3:
            rank_str, row_bg, row_outline, text_color = (
                "#3",
                (35, 22, 15, 220),
                (*BRONZE, 200),
                BRONZE,
            )
        else:
            rank_str, row_bg, row_outline, text_color = (
                f"#{rank}",
                (16, 14, 34, 180),
                (*PURPLE, 60),
                WHITE,
            )

        row_box = (center_x - 730, row_y, center_x + 730, row_y + row_h - 8)
        draw_rounded_rect(
            img,
            row_box,
            radius=8,
            fill=row_bg,
            outline=row_outline,
            width=2 if rank <= 3 else 1,
        )

        ty = row_y + 12
        d.text((col_rank, ty), rank_str, font=row_font, fill=text_color)
        d.text((col_name, ty), name, font=row_font, fill=text_color)
        d.text((col_score, ty), f"{score:,}", font=row_font, fill=WHITE)
        d.text((col_kills, ty), str(kills), font=row_font, fill=GREEN)
        d.text((col_deaths, ty), str(deaths), font=row_font, fill=(240, 70, 85))
        d.text((col_kd, ty), f"{kd_val:.2f}", font=row_font, fill=CYAN)
        d.text((col_games, ty), str(games), font=row_font, fill=MUTED)

        row_y += row_h

    with io.BytesIO() as image_buffer:
        img.convert("RGB").save(image_buffer, "PNG", optimize=True)
        image_buffer.seek(0)
        files = webhook.create("player-standings.png", image_buffer)

        # firstly check if players standings is sent for this season.
        data = webhook.get("player-standings")
        if data:
            # there is a standings already sent.
            # we will edit it.
            webhook.edit("player-standings", files)
            return

        # there is no standings sent yet, we will send a new one.
        webhook.send("dashboard", "player-standings", files)


def _draw_group_header(img, subtitle):
    top_y = 22

    if os.path.exists(TITLE_FILE):
        title_img = Image.open(TITLE_FILE).convert("RGBA")
        title_img.thumbnail((910, 170), Image.Resampling.LANCZOS)
        center_x = (WIDTH - title_img.width) // 2
        img.alpha_composite(title_img, (center_x, top_y))

    left_x = 75
    badge_y = top_y + 12

    d = ImageDraw.Draw(img)
    badge_font = get_font(24, True)
    tw, th = text_size(d, subtitle, badge_font)
    badge_w, badge_h = tw + 48, th + 24

    draw_alpha_rounded_rectangle(
        img,
        (left_x, badge_y, left_x + badge_w, badge_y + badge_h),
        radius=10,
        fill=(14, 12, 30, 230),
        outline=(*CYAN, 240),
        width=2,
    )
    center_text_box(
        d,
        (left_x, badge_y, left_x + badge_w, badge_y + badge_h),
        subtitle,
        badge_font,
        CYAN,
    )


def _group_background():
    return make_background(
        WIDTH,
        HEIGHT,
        ellipse1=((-250, -420, WIDTH + 250, HEIGHT + 600), (*PURPLE, 35), 4),
        ellipse2=((-160, -330, WIDTH + 160, HEIGHT + 510), (*BLUE, 28), 3),
        star_count=220,
        star_seed=19,
    )


def standings_title(name):
    return f"GROUP {name} STANDINGS".upper()


def schedule_title(name):
    return f"GROUP {name}".upper()


def _generate_single_group_standings(
    group_name: str, group_data: dict, winning_limit: int, webhook: Webhook
) -> None:
    img = _group_background()
    add_watermark(img, LOGO_FILE, WIDTH, HEIGHT, alpha=0.4, y_pos=125)
    _draw_group_header(img, standings_title(group_name))

    d = ImageDraw.Draw(img)
    standings = group_data.get("standings_sorted", [])

    card_x, card_y = 100, 175
    card_w = WIDTH - (card_x * 2)
    card_h = HEIGHT - card_y - 75
    draw_alpha_rounded_rectangle(
        img,
        (card_x, card_y, card_x + card_w, card_y + card_h),
        radius=16,
        fill=(9, 8, 20, 160),
        outline=(*PURPLE, 180),
        width=2,
    )

    header_y = card_y + 18
    header_h = 52
    draw_alpha_rounded_rectangle(
        img,
        (card_x + 15, header_y, card_x + card_w - 15, header_y + header_h),
        radius=10,
        fill=(18, 15, 38, 220),
        outline=(*CYAN, 120),
        width=1,
    )

    col_rank = card_x + 35
    col_team = card_x + 130
    col_wins = card_x + 900
    col_loses = card_x + 1050
    col_pts = card_x + 1200
    col_diff = card_x + 1340
    col_rounds = card_x + 1500

    header_font = get_font(13, True)
    d.text((col_rank, header_y + 18), "#", font=header_font, fill=CYAN)
    d.text((col_team, header_y + 18), "TEAM", font=header_font, fill=CYAN)
    d.text((col_wins, header_y + 18), "WINS", font=header_font, fill=CYAN)
    d.text((col_loses, header_y + 18), "LOSSES", font=header_font, fill=CYAN)
    d.text((col_pts, header_y + 18), "POINTS", font=header_font, fill=CYAN)
    d.text((col_diff, header_y + 18), "ROUND DIFF", font=header_font, fill=CYAN)
    d.text(
        (col_rounds, header_y + 18), "ROUNDS (WON/LOST)", font=header_font, fill=CYAN
    )

    rows_start_y = header_y + header_h + 16
    team_count = len(standings)

    layout_count = max(team_count, 8)
    available_height = (card_y + card_h - 25) - rows_start_y
    row_h = available_height / layout_count

    base_team_font_size = int(min(26, row_h * 0.38))
    base_num_font_size = int(min(22, row_h * 0.34))
    row_font = get_font(base_team_font_size, True)
    num_font = get_font(base_num_font_size, False)
    max_team_w = col_wins - col_team - 35

    for i, team_data in enumerate(standings):
        ry = rows_start_y + i * row_h
        rank = i + 1
        is_advancing = rank <= winning_limit

        row_outline = (*GREEN, 140) if is_advancing else (50, 50, 80, 100)
        draw_alpha_rounded_rectangle(
            img,
            (card_x + 15, ry, card_x + card_w - 15, ry + row_h - 10),
            radius=8,
            fill=(14, 12, 30, 180),
            outline=row_outline,
            width=2 if is_advancing else 1,
        )

        text_y = ry + (row_h // 2) - (base_team_font_size // 2) - 4
        rank_color = GREEN if is_advancing else MUTED_BOARD
        d.text((col_rank + 2, text_y), f"{rank:02d}", font=row_font, fill=rank_color)

        team_name = str(team_data.get("id", "UNKNOWN"))
        fitted_font = fit_font(d, team_name, max_team_w, base_team_font_size, True)
        safe_team_name = truncate_text(d, team_name, max_team_w, fitted_font)
        d.text((col_team, text_y), safe_team_name, font=fitted_font, fill=WHITE)

        wins = str(team_data.get("wins", 0))
        loses = str(team_data.get("loses", 0))
        pts = str(team_data.get("points", 0))
        diff = str(team_data.get("diff", 0))
        rd_str = f"{team_data.get('rounds_won', 0)}/{team_data.get('rounds_lost', 0)}"

        d.text((col_wins + 2, text_y + 2), wins, font=num_font, fill=WHITE)
        d.text((col_loses + 2, text_y + 2), loses, font=num_font, fill=WHITE)
        d.text((col_pts + 2, text_y + 2), pts, font=row_font, fill=CYAN)
        d.text((col_diff + 2, text_y + 2), diff, font=num_font, fill=WHITE)
        d.text((col_rounds + 2, text_y + 2), rd_str, font=num_font, fill=MUTED_BOARD)

    footer_text = f"TOP {winning_limit} TEAMS ADVANCE TO PLAYOFFS"
    fw, fh = text_size(d, footer_text, get_font(13, True))
    d.text(
        ((WIDTH - fw) / 2, HEIGHT - 42),
        footer_text,
        font=get_font(13, True),
        fill=(*GREEN, 255),
    )

    with io.BytesIO() as image_buffer:
        img.convert("RGB").save(image_buffer, "PNG", optimize=True)
        image_buffer.seek(0)
        files = webhook.create(f"standings_group-{group_name}.png", image_buffer)

        # firstly check if there have been a standings sent for this group.
        data = webhook.get(f"standings_group-{group_name}")
        if data:
            # there is a standings already sent.
            # we will edit it.
            webhook.edit(f"standings_group-{group_name}", files)
            return

        # there is no standings sent yet, we will send a new one.
        webhook.send("dashboard", f"standings_group-{group_name}", files)


def generate_group_standings(json_file: Path, webhook: Webhook) -> None:
    data = adapt_group_stage(read_json(json_file))

    groups = data.get("groups", {})
    winning_limit = int(data.get("winning_teams_per_group", 4))

    for group_name, group_data in groups.items():
        _generate_single_group_standings(group_name, group_data, winning_limit, webhook)


def team_label(name):
    return "BYE" if name is None else str(name)


def collect_teams(group):
    teams = {}
    for rd in group.get("rounds", {}).values():
        for match in rd.get("matches", {}).values():
            for key in ("team1", "team2"):
                t = match.get(key)
                if t is not None:
                    teams[str(t)] = True
    return list(teams.keys())


def is_bye(match):
    return match.get("team1") is None or match.get("team2") is None


def draw_schedule_match_card(img, x, y, w, h, match_number, match):
    bye = is_bye(match)
    outline = BYE if bye else (65, 65, 105)

    draw_alpha_rounded_rectangle(
        img,
        (x, y, x + w, y + h),
        radius=10,
        fill=(9, 9, 20, 140),
        outline=(*outline, 210),
        width=1,
    )

    draw = ImageDraw.Draw(img)
    draw.text(
        (x + 12, y + 6),
        f"MATCH {match_number}",
        font=get_font(11, True),
        fill=(*MUTED_BOARD, 255),
    )

    t1 = team_label(match.get("team1"))
    t2 = team_label(match.get("team2"))

    if bye:
        actual = t1 if t1 != "BYE" else t2
        center_text_box(
            draw,
            (x + 15, y + 26, x + w - 15, y + h - 35),
            actual,
            fit_font(draw, actual, w - 30, 24, True),
            WHITE,
        )
        center_text_box(
            draw,
            (x + 10, y + h - 32, x + w - 10, y + h - 8),
            "BYE  •  AUTOMATIC WIN",
            fit_font(draw, "BYE  •  AUTOMATIC WIN", w - 20, 11, True),
            BYE,
        )
        return

    winner = match.get("winner")
    c1 = GREEN if winner == match.get("team1") else WHITE
    c2 = GREEN if winner == match.get("team2") else WHITE

    team1_box = (x + 15, y + 22, x + w - 15, y + (h // 2) - 4)
    tf1 = fit_font(draw, t1, w - 30, 22, True, minimum=10)
    center_text_box(draw, team1_box, t1, tf1, c1)

    mid_y = y + (h // 2) + 2
    draw.line((x + 25, mid_y, x + w - 25, mid_y), fill=(50, 50, 80, 120), width=1)

    vs_w, vs_h = 32, 16
    vs_box = (
        x + (w // 2) - (vs_w // 2),
        mid_y - (vs_h // 2),
        x + (w // 2) + (vs_w // 2),
        mid_y + (vs_h // 2),
    )
    draw_alpha_rounded_rectangle(
        img, vs_box, radius=5, fill=(14, 12, 30, 230), outline=(*CYAN, 200), width=1
    )
    center_text_box(draw, vs_box, "VS", get_font(9, True), CYAN)

    team2_box = (x + 15, y + (h // 2) + 8, x + w - 15, y + h - 8)
    tf2 = fit_font(draw, t2, w - 30, 22, True, minimum=10)
    center_text_box(draw, team2_box, t2, tf2, c2)


def draw_round_column(img, x, y, w, h, round_number, round_data, accent):
    draw_alpha_rounded_rectangle(
        img,
        (x, y, x + w, y + h),
        radius=14,
        fill=(9, 8, 20, 130),
        outline=(*accent, 190),
        width=2,
    )
    draw_alpha_rounded_rectangle(
        img, (x + 1, y + 1, x + w - 1, y + 62), radius=13, fill=(*accent, 35)
    )

    d = ImageDraw.Draw(img)
    status = str(round_data.get("status", "PENDING")).upper()

    center_text_box(
        d,
        (x + 15, y + 8, x + w - 15, y + 37),
        f"ROUND {round_number}",
        get_font(21, True),
        WHITE,
    )
    status_color = BLUE if status == "IN_PROGRESS" else PURPLE
    center_text_box(
        d,
        (x + 15, y + 36, x + w - 15, y + 57),
        status.replace("_", " "),
        get_font(11, True),
        status_color,
    )

    matches = list(round_data.get("matches", {}).values())
    match_count = max(len(matches), 1)

    content_y = y + 74
    content_h = h - 88
    gap = 12
    card_h = (content_h - gap * (match_count - 1)) / match_count

    for i, match in enumerate(matches):
        my = content_y + i * (card_h + gap)
        draw_schedule_match_card(
            img, x + 12, int(my), w - 24, int(card_h), i + 1, match
        )


def _generate_group_schedule(group_name: str, group_data: dict, webhook: Webhook):
    img = _group_background()
    add_watermark(img, LOGO_FILE, WIDTH, HEIGHT, alpha=0.4, y_pos=125)
    _draw_group_header(img, schedule_title(group_name))

    d = ImageDraw.Draw(img)
    rounds = list(group_data.get("rounds", {}).items())

    if not rounds:
        d.text((70, 250), "NO ROUNDS FOUND", font=get_font(32, True), fill=RED_BOARD)
    else:
        left, top, bottom, gap = 40, 180, 65, 14
        available = WIDTH - 2 * left - gap * (len(rounds) - 1)
        col_w = available / len(rounds)
        col_h = HEIGHT - top - bottom

        for i, (round_name, round_data) in enumerate(rounds):
            x = left + i * (col_w + gap)
            draw_round_column(
                img,
                int(x),
                top,
                int(col_w),
                int(col_h),
                i + 1,
                round_data,
                GROUP_COLORS[i % len(GROUP_COLORS)],
            )

    footer = "NO OPPONENT  =  BYE  /  AUTOMATIC WIN"
    fw, fh = text_size(d, footer, get_font(13, True))
    d.text(
        ((WIDTH - fw) / 2, HEIGHT - 34),
        footer,
        font=get_font(13, True),
        fill=(*BYE, 255),
    )

    with io.BytesIO() as image_buffer:
        img.convert("RGB").save(image_buffer, "PNG", optimize=True)
        image_buffer.seek(0)
        # sent as a new message, or edited if this group's bracket was already sent (held for the
        # owners to review first, when that is on).
        posts.submit(
            webhook,
            "brackets",
            f"group-{group_name}",
            f"Group {group_name} brackets",
            filename=f"group-{group_name}.png",
            image=image_buffer.getvalue(),
            content=f"<@&{group_data['role_id']}>, Your brackets are here.",
        )


def _generate_group_overview(groups: dict, webhook: Webhook) -> None:
    # the overview is sent once per season, nothing to do if it was already sent (or is held).
    if webhook.get("group-overview") or posts.find_pending(webhook.season_id, "group-overview"):
        return
    img = _group_background()
    add_watermark(img, LOGO_FILE, WIDTH, HEIGHT, alpha=0.4, y_pos=125)
    _draw_group_header(img, "GROUP STAGE")

    d = ImageDraw.Draw(img)
    group_count = len(groups)

    if group_count <= 2:
        cols = group_count
    elif group_count <= 4:
        cols = 2
    elif group_count <= 9:
        cols = 3
    elif group_count <= 16:
        cols = 4
    else:
        cols = 5
    rows = math.ceil(group_count / cols)

    left, right, top, bottom, gx, gy = 42, 42, 180, 42, 18, 18
    card_w = (WIDTH - left - right - gx * (cols - 1)) / cols
    card_h = (HEIGHT - top - bottom - gy * (rows - 1)) / rows

    for i, (name, group) in enumerate(groups.items()):
        r, c = i // cols, i % cols
        x = left + c * (card_w + gx)
        y = top + r * (card_h + gy)
        accent = GROUP_COLORS[i % len(GROUP_COLORS)]

        draw_alpha_rounded_rectangle(
            img,
            (x, y, x + card_w, y + card_h),
            radius=16,
            fill=(9, 8, 20, 130),
            outline=(*accent, 200),
            width=2,
        )
        draw_alpha_rounded_rectangle(
            img, (x + 1, y + 1, x + card_w - 1, y + 62), radius=15, fill=(*accent, 45)
        )

        d.text(
            (x + 18, y + 15), schedule_title(name), font=get_font(24, True), fill=WHITE
        )

        teams = collect_teams(group)
        content_top = y + 80
        content_bottom = y + card_h - 18
        team_count = max(len(teams), 1)
        row_h = min(42, max(24, (content_bottom - content_top) / team_count))
        team_font_size = int(min(18, max(10, row_h * 0.48)))

        count_label = f"{len(teams)} TEAMS"
        cw, ch = text_size(d, count_label, get_font(11, True))
        d.text(
            (x + card_w - cw - 18, y + 20),
            count_label,
            font=get_font(11, True),
            fill=(*MUTED_BOARD, 255),
        )

        for j, team in enumerate(teams):
            yy = content_top + j * row_h
            number_box_w = 38

            draw_alpha_rounded_rectangle(
                img,
                (x + 16, yy, x + 16 + number_box_w, yy + row_h - 5),
                radius=7,
                fill=(18, 17, 34, 180),
                outline=(*accent, 100),
                width=1,
            )
            center_text_box(
                d,
                (x + 16, yy, x + 16 + number_box_w, yy + row_h - 5),
                str(j + 1),
                get_font(max(10, team_font_size - 2), True),
                (*accent, 255),
            )

            max_team_width = int(card_w - 82)
            tf = fit_font(d, str(team), max_team_width, team_font_size, True, minimum=8)
            d.text(
                (x + 68, yy + max(2, int(row_h * 0.18))), str(team), font=tf, fill=WHITE
            )

            if j < len(teams) - 1:
                divider_y = yy + row_h - 6
                d.line(
                    (x + 68, divider_y, x + card_w - 18, divider_y),
                    fill=(55, 55, 82, 100),
                    width=1,
                )

    with io.BytesIO() as image_buffer:
        img.convert("RGB").save(image_buffer, "PNG", optimize=True)
        image_buffer.seek(0)
        posts.submit(
            webhook,
            "brackets",
            "group-overview",
            "Group stage overview",
            filename="group-overview.png",
            image=image_buffer.getvalue(),
            once=True,
        )


def generate_group_stage(json_file: Path, webhook: Webhook) -> None:
    data = adapt_group_stage(read_json(json_file))

    groups = data.get("groups", {})
    if not isinstance(groups, dict) or not groups:
        raise ValueError("JSON must contain a non-empty 'groups' object.")

    _generate_group_overview(groups, webhook)

    for group_name, group_data in groups.items():
        _generate_group_schedule(group_name, group_data, webhook)


def _bracket_background():
    return make_background(
        BRACKET_WIDTH,
        BRACKET_HEIGHT,
        ellipse1=(
            (-400, -700, BRACKET_WIDTH + 400, BRACKET_HEIGHT + 900),
            (*PURPLE, 30),
            6,
        ),
        ellipse2=(
            (-300, -600, BRACKET_WIDTH + 300, BRACKET_HEIGHT + 800),
            (*BLUE, 25),
            4,
        ),
        star_count=450,
        star_seed=42,
        star_radius_choices=(2, 2, 3),
        star_alpha_range=(20, 80),
    )


def draw_bracket_header(img):
    if not os.path.exists(TITLE_FILE):
        return
    title_img = Image.open(TITLE_FILE).convert("RGBA")
    title_img.thumbnail((1200, 200), Image.Resampling.LANCZOS)
    center_x = (BRACKET_WIDTH - title_img.width) // 2
    img.alpha_composite(title_img, (center_x, 20))


def draw_bracket_match_card(img, d, box, team1, team2, winner, font, card_w, card_h):
    x1, y1, x2, y2 = box
    mid_y = y1 + (card_h / 2)

    draw_alpha_rounded_rectangle(
        img, box, radius=10, fill=(10, 9, 22, 235), outline=(*PURPLE, 160), width=2
    )

    t1_box = (x1 + 4, y1 + 4, x2 - 4, mid_y - 2)
    is_t1_winner = bool(winner) and winner == team1
    draw_alpha_rounded_rectangle(
        img,
        t1_box,
        radius=5,
        fill=(20, 45, 30, 210) if is_t1_winner else (16, 14, 34, 190),
    )
    t1_text = truncate_text(d, team1, card_w - 20, font)
    t1_color = GREEN if is_t1_winner else (WHITE if team1 != "TBD" else MUTED)
    center_text_box(d, t1_box, t1_text, font, t1_color)

    divider = Image.new("RGBA", img.size, (0, 0, 0, 0))
    div_d = ImageDraw.Draw(divider)
    div_d.line([(x1 + 10, mid_y), (x2 - 10, mid_y)], fill=(*CYAN, 180), width=2)
    img.alpha_composite(divider)

    t2_box = (x1 + 4, mid_y + 2, x2 - 4, y2 - 4)
    is_t2_winner = bool(winner) and winner == team2
    draw_alpha_rounded_rectangle(
        img,
        t2_box,
        radius=5,
        fill=(20, 45, 30, 210) if is_t2_winner else (16, 14, 34, 190),
    )
    t2_text = truncate_text(d, team2, card_w - 20, font)
    t2_color = GREEN if is_t2_winner else (WHITE if team2 != "TBD" else MUTED)
    center_text_box(d, t2_box, t2_text, font, t2_color)


def load_round_data(json_files: list) -> dict:
    rounds_data = {}
    for filepath in json_files:
        if isinstance(filepath, str):
            filepath = Path(filepath)
        if filepath.exists():
            # the main stage has no byes, a team that is not known yet is "TBD".
            rounds_data[filepath.name] = adapt_round(read_json(filepath), "TBD")
    return rounds_data


def generate_mainstage_bracket(json_files: list, webhook: Webhook) -> None:
    img = _bracket_background()
    add_watermark(
        img,
        LOGO_FILE,
        BRACKET_WIDTH,
        BRACKET_HEIGHT,
        alpha=0.20,
        thumb_size=(1200, 1200),
    )
    draw_bracket_header(img)

    rounds_data = load_round_data(json_files)
    if not rounds_data:
        return

    d = ImageDraw.Draw(img)

    first_file = Path(json_files[0])
    r1_filename = first_file.name
    total_r1_matches = len(
        list(rounds_data.get(r1_filename, {}).get("matches", {}).values())
    )
    wing_rounds = int(math.log2(total_r1_matches // 2)) + 1

    side_margin = 120
    center_reserved_w = 700
    available_half_width = (BRACKET_WIDTH / 2) - (center_reserved_w / 2) - side_margin

    card_w = max(240, min(340, available_half_width / wing_rounds * 0.75))
    wing_gap = (available_half_width - card_w) / max(1, (wing_rounds - 1))

    max_matches_per_wing = total_r1_matches // 2
    card_h = max(70, min(100, (BRACKET_HEIGHT - 400) / max_matches_per_wing * 0.65))

    top_y = 220
    usable_h = (BRACKET_HEIGHT - 100) - top_y
    center_x = BRACKET_WIDTH / 2

    left_positions = {}
    right_positions = {}

    for r_idx in range(wing_rounds):
        matches_per_wing = (total_r1_matches // 2) // (2**r_idx)
        step_y = usable_h / matches_per_wing
        left_x = side_margin + (card_w / 2) + (r_idx * wing_gap)
        right_x = BRACKET_WIDTH - side_margin - (card_w / 2) - (r_idx * wing_gap)

        for m_idx in range(matches_per_wing):
            if r_idx == 0:
                cy = top_y + (m_idx * step_y) + (step_y / 2)
            else:
                p1_y = left_positions[(r_idx - 1, m_idx * 2)]["cy"]
                p2_y = left_positions[(r_idx - 1, m_idx * 2 + 1)]["cy"]
                cy = (p1_y + p2_y) / 2
            left_positions[(r_idx, m_idx)] = {"cx": left_x, "cy": cy}

            p1_ry = right_positions[(r_idx - 1, m_idx * 2)]["cy"] if r_idx > 0 else cy
            p2_ry = (
                right_positions[(r_idx - 1, m_idx * 2 + 1)]["cy"] if r_idx > 0 else cy
            )
            right_cy = (p1_ry + p2_ry) / 2 if r_idx > 0 else cy
            right_positions[(r_idx, m_idx)] = {"cx": right_x, "cy": right_cy}

    for r_idx in range(wing_rounds - 1):
        m_count = (total_r1_matches // 2) // (2**r_idx)
        for m in range(m_count):
            p_L = left_positions[(r_idx, m)]
            c_L = left_positions[(r_idx + 1, m // 2)]
            mid_L = (
                p_L["cx"]
                + (card_w / 2)
                + ((c_L["cx"] - (card_w / 2)) - (p_L["cx"] + (card_w / 2))) / 2
            )
            d.line(
                [(p_L["cx"] + card_w / 2, p_L["cy"]), (mid_L, p_L["cy"])],
                fill=(*CYAN, 140),
                width=2,
            )
            d.line([(mid_L, p_L["cy"]), (mid_L, c_L["cy"])], fill=(*CYAN, 140), width=2)
            d.line(
                [(mid_L, c_L["cy"]), (c_L["cx"] - card_w / 2, c_L["cy"])],
                fill=(*CYAN, 140),
                width=2,
            )

            p_R = right_positions[(r_idx, m)]
            c_R = right_positions[(r_idx + 1, m // 2)]
            mid_R = (
                p_R["cx"]
                - (card_w / 2)
                - ((p_R["cx"] - (card_w / 2)) - (c_R["cx"] + (card_w / 2))) / 2
            )
            d.line(
                [(p_R["cx"] - card_w / 2, p_R["cy"]), (mid_R, p_R["cy"])],
                fill=(*CYAN, 140),
                width=2,
            )
            d.line([(mid_R, p_R["cy"]), (mid_R, c_R["cy"])], fill=(*CYAN, 140), width=2)
            d.line(
                [(mid_R, c_R["cy"]), (c_R["cx"] + card_w / 2, c_R["cy"])],
                fill=(*CYAN, 140),
                width=2,
            )

    team_font = get_font(max(14, int(card_h * 0.22)), True)

    for r_idx in range(wing_rounds):
        m_count = (total_r1_matches // 2) // (2**r_idx)
        fname = Path(json_files[r_idx]).name if r_idx < len(json_files) else ""
        m_data = list(rounds_data.get(fname, {}).get("matches", {}).values())

        for m in range(m_count):
            match_L = m_data[m] if m < len(m_data) else {}
            pos_L = left_positions[(r_idx, m)]
            box_L = (
                pos_L["cx"] - card_w / 2,
                pos_L["cy"] - card_h / 2,
                pos_L["cx"] + card_w / 2,
                pos_L["cy"] + card_h / 2,
            )
            draw_bracket_match_card(
                img,
                d,
                box_L,
                match_L.get("team1", "TBD"),
                match_L.get("team2", "TBD"),
                match_L.get("winner"),
                team_font,
                card_w,
                card_h,
            )

            match_R = m_data[m + m_count] if (m + m_count) < len(m_data) else {}
            pos_R = right_positions[(r_idx, m)]
            box_R = (
                pos_R["cx"] - card_w / 2,
                pos_R["cy"] - card_h / 2,
                pos_R["cx"] + card_w / 2,
                pos_R["cy"] + card_h / 2,
            )
            draw_bracket_match_card(
                img,
                d,
                box_R,
                match_R.get("team1", "TBD"),
                match_R.get("team2", "TBD"),
                match_R.get("winner"),
                team_font,
                card_w,
                card_h,
            )

    center_card_w, center_card_h = 360, 110
    finals_y = top_y + 450
    third_y = finals_y + 480

    finals_data = list(rounds_data.get("finals.json", {}).get("matches", {}).values())
    grand_final = finals_data[0] if len(finals_data) > 0 else {}
    third_match = finals_data[1] if len(finals_data) > 1 else {}

    semi_L = left_positions[(wing_rounds - 1, 0)]
    semi_R = right_positions[(wing_rounds - 1, 0)]

    mid_gf_L = (
        semi_L["cx"]
        + (card_w / 2)
        + ((center_x - center_card_w / 2) - (semi_L["cx"] + card_w / 2)) * 0.5
    )
    d.line(
        [(semi_L["cx"] + card_w / 2, semi_L["cy"]), (mid_gf_L, semi_L["cy"])],
        fill=(*GOLD, 200),
        width=3,
    )
    d.line([(mid_gf_L, semi_L["cy"]), (mid_gf_L, finals_y)], fill=(*GOLD, 200), width=3)
    d.line(
        [(mid_gf_L, finals_y), (center_x - center_card_w / 2, finals_y)],
        fill=(*GOLD, 200),
        width=3,
    )

    mid_gf_R = (
        semi_R["cx"]
        - (card_w / 2)
        - ((semi_R["cx"] - card_w / 2) - (center_x + center_card_w / 2)) * 0.5
    )
    d.line(
        [(semi_R["cx"] - card_w / 2, semi_R["cy"]), (mid_gf_R, semi_R["cy"])],
        fill=(*GOLD, 200),
        width=3,
    )
    d.line([(mid_gf_R, semi_R["cy"]), (mid_gf_R, finals_y)], fill=(*GOLD, 200), width=3)
    d.line(
        [(mid_gf_R, finals_y), (center_x + center_card_w / 2, finals_y)],
        fill=(*GOLD, 200),
        width=3,
    )

    mid_3rd_L = (
        semi_L["cx"]
        + (card_w / 2)
        + ((center_x - center_card_w / 2) - (semi_L["cx"] + card_w / 2)) * 0.35
    )
    d.line(
        [(semi_L["cx"] + card_w / 2, semi_L["cy"]), (mid_3rd_L, semi_L["cy"])],
        fill=(*BRONZE, 180),
        width=2,
    )
    d.line(
        [(mid_3rd_L, semi_L["cy"]), (mid_3rd_L, third_y)], fill=(*BRONZE, 180), width=2
    )
    d.line(
        [(mid_3rd_L, third_y), (center_x - center_card_w / 2, third_y)],
        fill=(*BRONZE, 180),
        width=2,
    )

    mid_3rd_R = (
        semi_R["cx"]
        - (card_w / 2)
        - ((semi_R["cx"] - card_w / 2) - (center_x + center_card_w / 2)) * 0.35
    )
    d.line(
        [(semi_R["cx"] - card_w / 2, semi_R["cy"]), (mid_3rd_R, semi_R["cy"])],
        fill=(*BRONZE, 180),
        width=2,
    )
    d.line(
        [(mid_3rd_R, semi_R["cy"]), (mid_3rd_R, third_y)], fill=(*BRONZE, 180), width=2
    )
    d.line(
        [(mid_3rd_R, third_y), (center_x + center_card_w / 2, third_y)],
        fill=(*BRONZE, 180),
        width=2,
    )

    h_font = get_font(20, True)
    center_font = get_font(20, True)

    gf_box = (
        center_x - center_card_w / 2,
        finals_y - center_card_h / 2,
        center_x + center_card_w / 2,
        finals_y + center_card_h / 2,
    )
    tw, th = text_size(d, "GRAND FINALS", h_font)
    draw_alpha_rounded_rectangle(
        img,
        (
            center_x - tw / 2 - 16,
            finals_y - center_card_h / 2 - 40,
            center_x + tw / 2 + 16,
            finals_y - center_card_h / 2 - 6,
        ),
        radius=6,
        fill=(18, 15, 38, 230),
        outline=(*GOLD, 220),
        width=2,
    )
    d.text(
        (center_x - tw / 2, finals_y - center_card_h / 2 - 34),
        "GRAND FINALS",
        font=h_font,
        fill=GOLD,
    )
    draw_bracket_match_card(
        img,
        d,
        gf_box,
        grand_final.get("team1", "TBD"),
        grand_final.get("team2", "TBD"),
        grand_final.get("winner"),
        center_font,
        center_card_w,
        center_card_h,
    )

    tp_box = (
        center_x - center_card_w / 2,
        third_y - center_card_h / 2,
        center_x + center_card_w / 2,
        third_y + center_card_h / 2,
    )
    tw, th = text_size(d, "3RD PLACE MATCH", h_font)
    draw_alpha_rounded_rectangle(
        img,
        (
            center_x - tw / 2 - 16,
            third_y - center_card_h / 2 - 40,
            center_x + tw / 2 + 16,
            third_y - center_card_h / 2 - 6,
        ),
        radius=6,
        fill=(18, 15, 38, 230),
        outline=(*BRONZE, 200),
        width=2,
    )
    d.text(
        (center_x - tw / 2, third_y - center_card_h / 2 - 34),
        "3RD PLACE MATCH",
        font=h_font,
        fill=BRONZE,
    )
    draw_bracket_match_card(
        img,
        d,
        tp_box,
        third_match.get("team1", "TBD"),
        third_match.get("team2", "TBD"),
        third_match.get("winner"),
        center_font,
        center_card_w,
        center_card_h,
    )

    gf_winner = grand_final.get("winner")
    gf_t1 = grand_final.get("team1", "TBD")
    gf_t2 = grand_final.get("team2", "TBD")
    gf_runnerup = (
        gf_t2 if gf_winner == gf_t1 else (gf_t1 if gf_winner == gf_t2 else None)
    )

    top_podium_box = (center_x - 240, top_y + 20, center_x + 240, top_y + 200)
    d.line(
        [(center_x, finals_y - center_card_h / 2), (center_x, top_podium_box[3])],
        fill=(*GOLD, 220),
        width=3,
    )

    if gf_winner:
        draw_alpha_rounded_rectangle(
            img,
            top_podium_box,
            radius=16,
            fill=(35, 28, 10, 245),
            outline=(*GOLD, 255),
            width=3,
        )
        center_text_box(
            d,
            (
                top_podium_box[0],
                top_podium_box[1] + 12,
                top_podium_box[2],
                top_podium_box[1] + 40,
            ),
            "CHAMPIONS",
            get_font(18, True),
            GOLD,
        )
        r1_text = truncate_text(d, f"RANK 1: {gf_winner}", 440, get_font(24, True))
        center_text_box(
            d,
            (
                top_podium_box[0],
                top_podium_box[1] + 52,
                top_podium_box[2],
                top_podium_box[1] + 90,
            ),
            r1_text,
            get_font(24, True),
            GOLD,
        )
        r2_text = truncate_text(d, f"RANK 2: {gf_runnerup}", 440, get_font(20, True))
        center_text_box(
            d,
            (
                top_podium_box[0],
                top_podium_box[1] + 102,
                top_podium_box[2],
                top_podium_box[1] + 140,
            ),
            r2_text,
            get_font(20, True),
            SILVER,
        )
    else:
        draw_alpha_rounded_rectangle(
            img,
            top_podium_box,
            radius=16,
            fill=(10, 9, 22, 190),
            outline=(*MUTED, 120),
            width=2,
        )
        center_text_box(
            d,
            (
                top_podium_box[0],
                top_podium_box[1] + 25,
                top_podium_box[2],
                top_podium_box[1] + 55,
            ),
            "CHAMPIONS",
            get_font(18, True),
            MUTED,
        )
        center_text_box(
            d,
            (
                top_podium_box[0],
                top_podium_box[1] + 75,
                top_podium_box[2],
                top_podium_box[1] + 115,
            ),
            "RANK 1 & 2: TBD",
            get_font(22, True),
            MUTED,
        )

    tp_winner = third_match.get("winner")
    bot_podium_box = (
        center_x - 210,
        third_y + center_card_h / 2 + 50,
        center_x + 210,
        third_y + center_card_h / 2 + 160,
    )
    d.line(
        [(center_x, third_y + center_card_h / 2), (center_x, bot_podium_box[1])],
        fill=(*BRONZE, 200),
        width=3,
    )

    if tp_winner:
        draw_alpha_rounded_rectangle(
            img,
            bot_podium_box,
            radius=14,
            fill=(30, 20, 12, 245),
            outline=(*BRONZE, 255),
            width=3,
        )
        center_text_box(
            d,
            (
                bot_podium_box[0],
                bot_podium_box[1] + 10,
                bot_podium_box[2],
                bot_podium_box[1] + 36,
            ),
            "THIRD PLACE",
            get_font(17, True),
            BRONZE,
        )
        r3_text = truncate_text(d, f"RANK 3: {tp_winner}", 360, get_font(22, True))
        center_text_box(
            d,
            (
                bot_podium_box[0],
                bot_podium_box[1] + 44,
                bot_podium_box[2],
                bot_podium_box[3] - 10,
            ),
            r3_text,
            get_font(22, True),
            WHITE,
        )
    else:
        draw_alpha_rounded_rectangle(
            img,
            bot_podium_box,
            radius=14,
            fill=(10, 9, 22, 190),
            outline=(*MUTED, 120),
            width=2,
        )
        center_text_box(
            d,
            (
                bot_podium_box[0],
                bot_podium_box[1] + 14,
                bot_podium_box[2],
                bot_podium_box[1] + 40,
            ),
            "THIRD PLACE",
            get_font(17, True),
            MUTED,
        )
        center_text_box(
            d,
            (
                bot_podium_box[0],
                bot_podium_box[1] + 48,
                bot_podium_box[2],
                bot_podium_box[3] - 10,
            ),
            "RANK 3: TBD",
            get_font(20, True),
            MUTED,
        )

    with io.BytesIO() as image_buffer:
        img.convert("RGB").save(image_buffer, "PNG", optimize=True)
        image_buffer.seek(0)
        posts.submit(
            webhook,
            "brackets",
            "mainstage-brackets",
            "Main stage bracket",
            filename="mainstage-brackets.png",
            image=image_buffer.getvalue(),
        )


# ---- double elimination ----
# one image: the winners bracket on top, the losers bracket below it and the grand final on the right.
# a slot that waits for a team says where the team comes from ("Winner W1.2", "Loser W2.1"), W is the
# winners bracket and L the losers bracket, the number is round.match.

DE_CARD_W, DE_CARD_H = 330, 92
DE_COL_GAP, DE_ROW_GAP = 70, 34
DE_MARGIN = 90
DE_HEADER_H = 250
DE_SECTION_H = 150
DE_WINNERS_COLOR = BLUE
DE_LOSERS_COLOR = (255, 150, 60)


def de_label(bracket: str, round_id, match_id) -> str:
    if bracket == "winners":
        return f"W{round_id}.{match_id}"
    if bracket == "losers":
        return f"L{round_id}.{match_id}"
    return f"GF{match_id}"


def de_slot_text(slot: dict) -> tuple[str, tuple]:
    """the text and colour of a team slot."""
    if slot.get("name") is not None:
        return str(slot["name"]), WHITE
    if slot.get("bye"):
        return "BYE", BYE
    source = slot.get("source")
    if not source:
        return "TBD", MUTED
    kind, bracket, round_id, match_id = source.split(":")
    word = "Winner" if kind == "W" else "Loser"
    return f"{word} {de_label(bracket, round_id, match_id)}", MUTED


def draw_de_card(img, d, box, match: dict, accent, font, label_font):
    x1, y1, x2, y2 = box
    mid_y = (y1 + y2) / 2
    played_bye = match.get("is_bye", False)
    outline = (*accent, 70 if played_bye else 190)
    draw_alpha_rounded_rectangle(
        img, box, radius=10, fill=(10, 9, 22, 150 if played_bye else 235), outline=outline, width=2
    )
    d.text(
        (x1 + 2, y1 - 20),
        de_label(match["bracket"], match["round_id"], match["match_id"]),
        font=label_font,
        fill=(*accent, 255),
    )

    winner_idx = match.get("winner_idx")
    rows = ((x1 + 4, y1 + 4, x2 - 4, mid_y - 2), (x1 + 4, mid_y + 2, x2 - 4, y2 - 4))
    for idx, (slot, row) in enumerate(zip(match["teams"][:2], rows)):
        won = winner_idx == idx and match.get("status") == "COMPLETED" and not played_bye
        lost = winner_idx is not None and winner_idx != idx and match.get("status") == "COMPLETED"
        draw_alpha_rounded_rectangle(
            img, row, radius=5, fill=(20, 45, 30, 210) if won else (16, 14, 34, 190)
        )
        text, color = de_slot_text(slot)
        if won:
            color = GREEN
        elif lost and slot.get("name") is not None:
            color = RED_BOARD
        name_box = (row[0] + 6, row[1], row[2] - 56, row[3])
        center_text_box(d, name_box, truncate_text(d, text, name_box[2] - name_box[0], font), font, color)
        if slot.get("name") is not None and match.get("status") == "COMPLETED" and not played_bye:
            center_text_box(d, (row[2] - 52, row[1], row[2] - 6, row[3]), str(slot.get("score", 0)), font, WHITE)


def read_rounds(season_dir: Path, bracket: str) -> dict:
    """the rounds of a bracket (the files of its folder), as {round id: round}, in the order they are played."""
    rounds = {}
    for file in (season_dir / "rounds" / bracket).glob("*.json"):
        rounds[file.stem] = read_json(file)
    return dict(sorted(rounds.items(), key=lambda item: item[1].get("number", 0)))


def generate_double_elimination(season_dir: Path, webhook: Webhook) -> None:
    data = {
        "winners": read_rounds(season_dir, "winners"),
        "losers": read_rounds(season_dir, "losers"),
        "grand_final": read_rounds(season_dir, "grand-final").get("1", {}),
        "placements": read_json(season_dir / "brackets.json").get("placements", []),
    }
    winners = data.get("winners", {})
    losers = data.get("losers", {})
    grand_final = data.get("grand_final", {}).get("matches", {})
    if not winners:
        return

    winner_rounds = sorted(winners, key=int)
    loser_rounds = sorted(losers, key=int)
    columns = max(len(winner_rounds), len(loser_rounds)) + 1  # + the grand final
    first_round_matches = len(winners[winner_rounds[0]]["matches"])

    unit = DE_CARD_H + DE_ROW_GAP
    winners_h = first_round_matches * unit
    losers_h = max(1, first_round_matches // 2) * unit
    width = DE_MARGIN * 2 + columns * DE_CARD_W + (columns - 1) * DE_COL_GAP
    width = max(width, 1700)
    legend_h = 150
    height = DE_HEADER_H + DE_SECTION_H + winners_h + DE_SECTION_H + losers_h + legend_h

    # the final ranks panel sits under the grand final, make sure the image is tall enough for it.
    placements = data.get("placements", [])
    if placements:
        grand_final_y = (DE_HEADER_H + DE_SECTION_H + DE_HEADER_H + DE_SECTION_H * 2 + winners_h + losers_h) / 2 - DE_CARD_H
        panel_bottom = grand_final_y + 2 * (DE_CARD_H + DE_SECTION_H) + 60 + 34 * min(8, len(placements)) + 40
        height = max(height, int(panel_bottom))

    img = make_background(
        width,
        height,
        ellipse1=((-400, -700, width + 400, height + 900), (*PURPLE, 30), 6),
        ellipse2=((-300, -600, width + 300, height + 800), (*BLUE, 25), 4),
        star_count=max(200, width * height // 20000),
        star_seed=42,
        star_radius_choices=(2, 2, 3),
        star_alpha_range=(20, 80),
    )
    add_watermark(img, LOGO_FILE, width, height, alpha=0.12, thumb_size=(1000, 1000))
    if os.path.exists(TITLE_FILE):
        title_img = Image.open(TITLE_FILE).convert("RGBA")
        title_img.thumbnail((1200, 200), Image.Resampling.LANCZOS)
        img.alpha_composite(title_img, ((width - title_img.width) // 2, 20))
    d = ImageDraw.Draw(img)

    title_font = get_font(44, True)
    sub_font = get_font(22, False)
    round_font = get_font(22, True)
    card_font = get_font(25, True)
    label_font = get_font(17, True)

    def column_x(col: int) -> float:
        return DE_MARGIN + col * (DE_CARD_W + DE_COL_GAP)

    positions = {}  # "bracket-round-match" -> (left x, centre y)

    def place(bracket: str, rounds: dict, order: list, top: float, total_h: float):
        for col, round_id in enumerate(order):
            matches = rounds[round_id]["matches"]
            count = len(matches)
            for match_id in sorted(matches, key=int):
                cy = top + (int(match_id) - 0.5) * (total_h / count)
                positions[f"{bracket}-{round_id}-{match_id}"] = (column_x(col), cy)

    winners_top = DE_HEADER_H + DE_SECTION_H
    losers_top = winners_top + winners_h + DE_SECTION_H
    place("winners", winners, winner_rounds, winners_top, winners_h)
    place("losers", losers, loser_rounds, losers_top, losers_h)

    # the grand final sits in the last column, in the middle of the two brackets
    gf_x = column_x(columns - 1)
    gf_cy = (winners_top + losers_top + losers_h) / 2 - DE_CARD_H
    for i, match_id in enumerate(sorted(grand_final, key=int)):
        positions[f"grand-final-1-{match_id}"] = (gf_x, gf_cy + i * (DE_CARD_H + DE_SECTION_H))

    # section titles
    def section(text, hint, color, y):
        d.text((DE_MARGIN, y), text, font=title_font, fill=color)
        d.text((DE_MARGIN, y + 56), hint, font=sub_font, fill=MUTED)

    section("WINNERS BRACKET", "Lose once and you drop to the losers bracket.", DE_WINNERS_COLOR, winners_top - DE_SECTION_H + 10)
    section("LOSERS BRACKET", "Lose here and you are out.", DE_LOSERS_COLOR, losers_top - DE_SECTION_H + 10)
    d.text((gf_x, winners_top - DE_SECTION_H + 10), "GRAND FINAL", font=title_font, fill=GOLD)
    d.text((gf_x, winners_top - DE_SECTION_H + 66), "Winners champion must lose twice.", font=sub_font, fill=MUTED)

    # round names above the first match of each column
    def round_names(rounds: dict, order: list, top: float, accent, last_name: str):
        for col, round_id in enumerate(order):
            name = last_name if col == len(order) - 1 else f"ROUND {round_id}"
            d.text((column_x(col), top - 44), name, font=round_font, fill=accent)

    round_names(winners, winner_rounds, winners_top, DE_WINNERS_COLOR, "WINNERS FINAL")
    if loser_rounds:
        round_names(losers, loser_rounds, losers_top, DE_LOSERS_COLOR, "LOSERS FINAL")

    # lines: a winner moving on in the same bracket, and the two champions going to the grand final
    def connect(a: tuple, b: tuple, color):
        ax, ay = a[0] + DE_CARD_W, a[1]
        bx, by = b[0], b[1]
        mid = ax + (bx - ax) / 2
        d.line([(ax, ay), (mid, ay), (mid, by), (bx, by)], fill=(*color, 150), width=3)

    every = [m for part in (winners, losers) for rd in part.values() for m in rd["matches"].values()]
    every += list(grand_final.values())
    for match in every:
        key = f"{match['bracket']}-{match['round_id']}-{match['match_id']}"
        for slot in match["teams"]:
            source = slot.get("source")
            if not source:
                continue
            kind, bracket, round_id, match_id = source.split(":")
            if kind == "W" and f"{bracket}-{round_id}-{match_id}" in positions:
                color = GOLD if match["bracket"] == "grand-final" else (
                    DE_WINNERS_COLOR if bracket == "winners" else DE_LOSERS_COLOR
                )
                connect(positions[f"{bracket}-{round_id}-{match_id}"], positions[key], color)

    # the cards
    for match in every:
        key = f"{match['bracket']}-{match['round_id']}-{match['match_id']}"
        x, cy = positions[key]
        accent = {"winners": DE_WINNERS_COLOR, "losers": DE_LOSERS_COLOR}.get(match["bracket"], GOLD)
        box = (x, cy - DE_CARD_H / 2, x + DE_CARD_W, cy + DE_CARD_H / 2)
        draw_de_card(img, d, box, match, accent, card_font, label_font)

    # the final ranks, once the bracket is over
    if placements:
        medals = {1: GOLD, 2: SILVER, 3: BRONZE}
        panel_x, panel_y = gf_x, gf_cy + 2 * (DE_CARD_H + DE_SECTION_H) - 20
        d.text((panel_x, panel_y), "FINAL RANKS", font=round_font, fill=GOLD)
        for i, place_data in enumerate(placements[:8]):
            rank = place_data["rank"]
            d.text(
                (panel_x, panel_y + 40 + i * 34),
                f"{rank}. {truncate_text(d, str(place_data['team']), DE_CARD_W - 50, card_font)}",
                font=card_font,
                fill=medals.get(rank, WHITE),
            )

    legend_y = height - legend_h + 30
    d.text(
        (DE_MARGIN, legend_y),
        "W2.1 = winners bracket, round 2, match 1     L3.2 = losers bracket, round 3, match 2     GF = grand final",
        font=sub_font,
        fill=MUTED,
    )
    d.text(
        (DE_MARGIN, legend_y + 36),
        "'Loser W2.1' means the loser of that match drops into this slot.   BYE = no opponent, the team moves on.",
        font=sub_font,
        fill=MUTED,
    )

    with io.BytesIO() as image_buffer:
        img.convert("RGB").save(image_buffer, "PNG", optimize=True)
        image_buffer.seek(0)
        posts.submit(
            webhook,
            "brackets",
            "double-elimination-brackets",
            "Double elimination bracket",
            filename="double-elimination-brackets.png",
            image=image_buffer.getvalue(),
        )


def generate_player_registration(name: str, webhook: Webhook):
    """
    Generates a registration card.
    """
    card_w, card_h = 800, 380

    img = make_background(
        card_w, card_h,
        ellipse1=((-80, -100, card_w + 80, card_h + 120), (*CYAN, 35), 2),
        ellipse2=((-40, -60, card_w + 40, card_h + 80), (*GOLD, 30), 2),
        star_count=50, star_seed=77,
    )
    
    add_watermark(img, LOGO_FILE, card_w, card_h, alpha=0.15, thumb_size=(320, 320))
    d = ImageDraw.Draw(img)
    center_x = card_w // 2

    top_y = 25

    if os.path.exists(TITLE_FILE):
        try:
            title_img = Image.open(TITLE_FILE).convert("RGBA")
            title_img.thumbnail((480, 110), Image.Resampling.LANCZOS)
            img.alpha_composite(title_img, ((card_w - title_img.width) // 2, top_y))
            top_y += title_img.height + 10
        except Exception as e:
            print("Title image warning:", e)
            top_y += 10
    else:
        main_title_font = get_font(34, bold=True)
        center_text(d, center_x, top_y + 20, "SUCCESSFULLY REGISTERED", main_title_font, GOLD)
        top_y += 50

    # Main Card Container Frame
    box_w, box_h = 720, 210
    box_coords = (center_x - box_w // 2, top_y, center_x + box_w // 2, top_y + box_h)
    draw_rounded_rect(img, box_coords, radius=14, fill=(8, 15, 35, 230), outline=(*CYAN, 200), width=2)

    # Confirmation Subtitle
    sub_font = get_font(18, bold=True)
    center_text(d, center_x, top_y + 35, "TOURNAMENT REGISTRATION CONFIRMED", sub_font, CYAN)

    d.line([(center_x - 300, top_y + 65), (center_x + 300, top_y + 65)], fill=(*CYAN, 90), width=2)

    player_name = str(name)
    name_font = fit_font(d, player_name, max_width=660, size=44, bold=True, minimum=18)
    center_text(d, center_x, top_y + 108, player_name, name_font, GREEN)

    with io.BytesIO() as image_buffer:
        img.convert("RGB").save(image_buffer, "PNG", optimize=True)
        image_buffer.seek(0)
        files = webhook.create("registration-card.png", image_buffer)
        webhook.send("registrations", f"player-registration-{player_name}", files)


if __name__ == "__main__":
    if not verify_dependencies():
        print("Missing dependencies, or download failed.")
        sys.exit(1)

    data = json.loads(sys.argv[1])
    season_id = data["season_id"]
    webhook = Webhook(season_id)
    SEASON_DIR = GRAPHICS_DIR.parent / "seasons" / season_id

    if data["type"] == "registration":
        generate_player_registration(data["name"], webhook)

    elif data["type"] == "match-announcement":
        participant_role_id = data["participant_role_id"]
        team1, team2 = data["team1"], data["team2"]
        message = f"<@&{participant_role_id}> **{team1}** vs **{team2}** is live now in the server!"
        webhook.send("announcements", key="match-announcement", content=message)

    elif data["type"] == "results":
        # for match result
        generate_match_result(data["details"], webhook)

    elif data["type"] == "player-standings":
        # for player standings
        # wait 10 seconds for the stats to be updated.
        time.sleep(10)
        generate_player_standings(season_id, SEASON_DIR / "stats.json", webhook)

    elif data["type"] == "group-standings":
        # for group standings
        generate_group_standings(SEASON_DIR / "rounds" / "group-stage.json", webhook)

    elif data["type"] == "group-stage":
        # for group stage
        generate_group_stage(SEASON_DIR / "rounds" / "group-stage.json", webhook)

    elif data["type"] == "double-elimination":
        generate_double_elimination(SEASON_DIR, webhook)

    elif data["type"] == "main-stage":
        # for main stage
        # the rounds are in the order they are played (the first one has the most matches).
        rounds = read_rounds(SEASON_DIR, "main-stage")
        sorted_files = [SEASON_DIR / "rounds" / "main-stage" / f"{name}.json" for name in rounds]
        generate_mainstage_bracket(
            json_files=sorted_files, webhook=webhook
        )
