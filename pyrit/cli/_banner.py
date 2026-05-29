# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Animated ASCII banner for PyRIT CLI.

Displays an animated raccoon mascot revealing the PYRIT logo on shell startup.
Inspired by the GitHub Copilot CLI animated banner approach:
  - Frame-based animation with ANSI cursor repositioning
  - Semantic color roles with light/dark theme support
  - Graceful degradation to static banner when animation isn't supported

The animation plays for ~2.5 seconds and settles into the familiar static banner.
Press Ctrl+C during animation to skip to the static banner immediately.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from pyrit.cli._banner_assets import BRAILLE_RACCOON, PYRIT_LETTERS, PYRIT_WIDTH, RACCOON_TAIL


class ColorRole(Enum):
    """Semantic color roles for banner elements."""

    BORDER = "border"
    PYRIT_TEXT = "pyrit_text"
    SUBTITLE = "subtitle"
    RACCOON_BODY = "raccoon_body"
    RACCOON_TAIL = "raccoon_tail"
    SPARKLE = "sparkle"
    COMMANDS = "commands"
    RESET = "reset"


# ANSI 4-bit color codes (work on virtually all terminals)
ANSI_COLORS = {
    "black": "\033[30m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "white": "\033[37m",
    "bright_black": "\033[90m",
    "bright_red": "\033[91m",
    "bright_green": "\033[92m",
    "bright_yellow": "\033[93m",
    "bright_blue": "\033[94m",
    "bright_magenta": "\033[95m",
    "bright_cyan": "\033[96m",
    "bright_white": "\033[97m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}

# Theme mappings: role -> ANSI color name
DARK_THEME: dict[ColorRole, str] = {
    ColorRole.BORDER: "cyan",
    ColorRole.PYRIT_TEXT: "bright_cyan",
    ColorRole.SUBTITLE: "bright_white",
    ColorRole.RACCOON_BODY: "bright_magenta",
    ColorRole.RACCOON_TAIL: "bright_magenta",
    ColorRole.SPARKLE: "bright_yellow",
    ColorRole.COMMANDS: "white",
    ColorRole.RESET: "reset",
}

LIGHT_THEME: dict[ColorRole, str] = {
    ColorRole.BORDER: "blue",
    ColorRole.PYRIT_TEXT: "blue",
    ColorRole.SUBTITLE: "black",
    ColorRole.RACCOON_BODY: "magenta",
    ColorRole.RACCOON_TAIL: "magenta",
    ColorRole.SPARKLE: "yellow",
    ColorRole.COMMANDS: "bright_black",
    ColorRole.RESET: "reset",
}


def _get_color(role: ColorRole, theme: dict[ColorRole, str]) -> str:
    """
    Resolve a color role to an ANSI escape sequence.

    Returns:
        The ANSI escape sequence string for the given role.
    """
    color_name = theme.get(role, "reset")
    return ANSI_COLORS.get(color_name, ANSI_COLORS["reset"])


def _detect_theme() -> dict[ColorRole, str]:
    """
    Detect whether terminal is light or dark themed. Defaults to dark.

    Returns:
        The theme color mapping dictionary.
    """
    # COLORFGBG is set by some terminals (e.g. xterm): "fg;bg"
    colorfgbg = os.environ.get("COLORFGBG", "")
    if colorfgbg:
        parts = colorfgbg.split(";")
        if len(parts) >= 2:
            try:
                bg = int(parts[-1])
                # bg >= 8 generally means light background
                if bg >= 8:
                    return LIGHT_THEME
            except ValueError:
                pass
    return DARK_THEME


@dataclass
class AnimationFrame:
    """A single frame of the banner animation."""

    lines: list[str]
    color_map: dict[int, ColorRole] = field(default_factory=dict)
    # Per-segment coloring: line_index -> [(start_col, end_col, role), ...]
    # When present, overrides color_map for that line
    segment_colors: dict[int, list[tuple[int, int, ColorRole]]] = field(default_factory=dict)
    duration: float = 0.15  # seconds to display this frame


def can_animate() -> bool:
    """
    Check whether the terminal supports animation.

    Returns:
        True if the terminal supports animation, False otherwise.
    """
    if not sys.stdout.isatty():
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("PYRIT_NO_ANIMATION"):
        return False
    # CI environments
    ci_val = os.environ.get("CI", "").strip().lower()
    return ci_val not in ("1", "true", "yes", "on")


# ── Banner layout constants ────────────────────────────────────────────────────

# Inner width between the left and right ║ border characters
BOX_W = 94
# Width reserved for the raccoon column in the header area (30 char art + 2 padding)
RACCOON_COL = 32
# Number of rows in the header section (matches braille raccoon art height)
HEADER_ROWS = 12
# Row offset where PYRIT block letters start within the header
PYRIT_START_ROW = 2


def _box_line(content: str) -> str:
    """
    Wrap content in box border chars, padded to BOX_W.

    Returns:
        The content wrapped in box border characters.
    """
    truncated_content = content[:BOX_W]
    return "║" + truncated_content.ljust(BOX_W) + "║"


def _empty_line() -> str:
    return _box_line("")


@dataclass
class StaticBannerData:
    """Result of building the static banner."""

    lines: list[str]
    color_map: dict[int, ColorRole]
    segment_colors: dict[int, list[tuple[int, int, ColorRole]]]


# ── Static banner (final frame / fallback) ─────────────────────────────────────


def _build_static_banner() -> StaticBannerData:
    """
    Build the static banner lines, color map, and per-segment colors.

    Returns:
        A StaticBannerData containing the lines, color map, and segment colors.
    """
    raccoon = BRAILLE_RACCOON
    lines: list[str] = []
    color_map: dict[int, ColorRole] = {}
    segment_colors: dict[int, list[tuple[int, int, ColorRole]]] = {}

    def add(line: str, role: ColorRole, segments: Optional[list[tuple[int, int, ColorRole]]] = None) -> None:
        idx = len(lines)
        color_map[idx] = role
        if segments:
            segment_colors[idx] = segments
        lines.append(line)

    # Top border + empty
    add("╔" + "═" * BOX_W + "╗", ColorRole.BORDER)
    add(_empty_line(), ColorRole.BORDER)

    # Header: braille raccoon + PYRIT text side by side
    subtitle_row_1 = PYRIT_START_ROW + len(PYRIT_LETTERS) + 1
    subtitle_row_2 = subtitle_row_1 + 1
    for i in range(HEADER_ROWS):
        r_part = (" " + raccoon[i] + " ").ljust(RACCOON_COL)
        pyrit_idx = i - PYRIT_START_ROW
        if 0 <= pyrit_idx < len(PYRIT_LETTERS):
            p_part = PYRIT_LETTERS[pyrit_idx]
        elif i == subtitle_row_1:
            p_part = "Python Risk Identification Tool"
        elif i == subtitle_row_2:
            p_part = "      Interactive Shell"
        else:
            p_part = ""

        full_line = _box_line(r_part + p_part)
        # Build per-segment colors: border ║, raccoon, PYRIT/subtitle, border ║
        segs: list[tuple[int, int, ColorRole]] = [
            (0, 1, ColorRole.BORDER),  # left ║
            (1, 1 + RACCOON_COL, ColorRole.RACCOON_BODY),  # raccoon area
        ]
        pyrit_start = 1 + RACCOON_COL
        pyrit_end = len(full_line) - 1
        if 0 <= pyrit_idx < len(PYRIT_LETTERS):
            segs.append((pyrit_start, pyrit_start + len(PYRIT_LETTERS[pyrit_idx]), ColorRole.PYRIT_TEXT))
            segs.append((pyrit_start + len(PYRIT_LETTERS[pyrit_idx]), pyrit_end, ColorRole.BORDER))
        elif i in (subtitle_row_1, subtitle_row_2):
            segs.append((pyrit_start, pyrit_end, ColorRole.SUBTITLE))
        else:
            segs.append((pyrit_start, pyrit_end, ColorRole.BORDER))
        segs.append((len(full_line) - 1, len(full_line), ColorRole.BORDER))  # right ║
        add(full_line, ColorRole.RACCOON_BODY, segs)

    add(_empty_line(), ColorRole.BORDER)

    # Mid divider (with tail attachment point)
    tail_col = 77
    tail = RACCOON_TAIL
    add("╠" + "═" * BOX_W + "╣", ColorRole.BORDER)

    # Commands section with striped tail hanging from divider
    commands = [
        "Commands:",
        "  • list-scenarios        - See all available scenarios",
        "  • list-initializers     - See all available initializers",
        "  • list-targets [opts]   - See all available targets in the registry",
        "  • run <scenario> [opts] - Execute a security scenario",
        "  • scenario-history      - View your session history",
        "  • print-scenario [N]    - Display detailed results",
        "  • help [command]        - Get help on any command",
        "  • clear                 - Clear the screen",
        "  • exit                  - Quit the shell",
    ]
    cmd_section: list[tuple[str, ColorRole]] = [
        ("", ColorRole.BORDER),  # empty line after divider
    ]
    cmd_section.extend(("  " + cmd, ColorRole.COMMANDS) for cmd in commands)
    cmd_section.append(("", ColorRole.BORDER))  # empty line after commands

    for i, (content, cmd_role) in enumerate(cmd_section):
        if i < len(tail):
            content = content.ljust(tail_col) + tail[i]
            full_line = _box_line(content)
            segs = [
                (0, 1, ColorRole.BORDER),
                (1, 1 + tail_col, ColorRole.COMMANDS),
                (1 + tail_col, 1 + tail_col + len(tail[i]), ColorRole.RACCOON_TAIL),
                (len(full_line) - 1, len(full_line), ColorRole.BORDER),
            ]
            add(full_line, cmd_role, segs)
        else:
            full_line = _box_line(content)
            if content:  # non-empty command line
                segs = [
                    (0, 1, ColorRole.BORDER),
                    (1, len(full_line) - 1, ColorRole.COMMANDS),
                    (len(full_line) - 1, len(full_line), ColorRole.BORDER),
                ]
                add(full_line, cmd_role, segs)
            else:
                add(full_line, cmd_role)

    add(_empty_line(), ColorRole.BORDER)

    # Quick start
    quick_start = [
        "Quick Start:",
        "  pyrit> list-scenarios",
        "  pyrit> run foundry.red_team_agent --target my_target --initializers target load_default_datasets",
    ]
    for qs in quick_start:
        full_line = _box_line("  " + qs)
        segs = [
            (0, 1, ColorRole.BORDER),
            (1, len(full_line) - 1, ColorRole.COMMANDS),
            (len(full_line) - 1, len(full_line), ColorRole.BORDER),
        ]
        add(full_line, ColorRole.COMMANDS, segs)

    add(_empty_line(), ColorRole.BORDER)

    # Bottom border
    add("╚" + "═" * BOX_W + "╝", ColorRole.BORDER)

    return StaticBannerData(lines=lines, color_map=color_map, segment_colors=segment_colors)


_STATIC_BANNER = _build_static_banner()
STATIC_BANNER_LINES = _STATIC_BANNER.lines
STATIC_COLOR_MAP = _STATIC_BANNER.color_map
STATIC_SEGMENT_COLORS = _STATIC_BANNER.segment_colors


def _build_animation_frames() -> list[AnimationFrame]:
    """
    Build the sequence of animation frames.

    Returns:
        A list of AnimationFrame objects.
    """
    frames: list[AnimationFrame] = []
    target_height = len(STATIC_BANNER_LINES)
    top = "╔" + "═" * BOX_W + "╗"
    bot = "╚" + "═" * BOX_W + "╝"
    mid = "╠" + "═" * BOX_W + "╣"
    empty = _empty_line()

    def _pad_to_height(lines: list[str], color_map: dict[int, ColorRole]) -> None:
        """Pad frame lines to match static banner height."""
        while len(lines) < target_height - 1:  # -1 for bottom border
            color_map[len(lines)] = ColorRole.BORDER
            lines.append(empty)
        color_map[len(lines)] = ColorRole.BORDER
        lines.append(bot)

    # ── Phase 1: Raccoon enters from right (4 frames) ──────────────────────
    raccoon = BRAILLE_RACCOON
    raccoon_w = max(len(line) for line in raccoon)
    raccoon_positions = [BOX_W - raccoon_w, (BOX_W - raccoon_w) * 2 // 3, (BOX_W - raccoon_w) // 3, 1]
    # Stars that appear during raccoon entry
    star_chars = ["✦", "✧", "·", "*"]
    star_positions = [(3, 70), (8, 55), (1, 80), (10, 65)]  # (row_offset, col)

    for i, x_pos in enumerate(raccoon_positions):
        lines = [top, empty]
        color_map: dict[int, ColorRole] = {0: ColorRole.BORDER, 1: ColorRole.BORDER}
        seg_colors: dict[int, list[tuple[int, int, ColorRole]]] = {}
        for r_idx, r_line in enumerate(raccoon):
            padded = " " * x_pos + r_line
            content = padded[:BOX_W].ljust(BOX_W)
            # Add trailing stars in later frames
            if i >= 2:
                for s_row, s_col in star_positions[: i - 1]:
                    if r_idx == s_row and s_col < BOX_W and content[s_col] == " ":
                        star = star_chars[(s_row + i) % len(star_chars)]
                        content = content[:s_col] + star + content[s_col + 1 :]
                        line_idx = len(lines)
                        seg_colors.setdefault(line_idx, [])
            line_idx = len(lines)
            boxed_line = "║" + content + "║"
            if line_idx in seg_colors:
                # Add base segments (border + raccoon body) before sparkle segments
                base_segs = [
                    (0, 1, ColorRole.BORDER),
                    (1, len(boxed_line) - 1, ColorRole.RACCOON_BODY),
                    (len(boxed_line) - 1, len(boxed_line), ColorRole.BORDER),
                ]
                for s_row, s_col in star_positions[: i - 1]:
                    if r_idx == s_row and s_col < BOX_W:
                        base_segs.append((s_col + 1, s_col + 2, ColorRole.SPARKLE))  # +1 for ║
                seg_colors[line_idx] = base_segs
            color_map[len(lines)] = ColorRole.RACCOON_BODY
            lines.append(boxed_line)
        color_map[len(lines)] = ColorRole.BORDER
        lines.append(empty)
        color_map[len(lines)] = ColorRole.BORDER
        lines.append(mid)
        _pad_to_height(lines, color_map)
        frames.append(AnimationFrame(lines=lines, color_map=color_map, segment_colors=seg_colors, duration=0.18))

    # ── Phase 2: PYRIT text reveals left-to-right (4 frames) ──────────────
    reveal_steps = [9, 18, 27, PYRIT_WIDTH]
    subtitle_row_1 = PYRIT_START_ROW + len(PYRIT_LETTERS) + 1
    subtitle_row_2 = subtitle_row_1 + 1

    for step_i, chars_visible in enumerate(reveal_steps):
        lines = [top, empty]
        color_map = {0: ColorRole.BORDER, 1: ColorRole.BORDER}
        seg_colors = {}

        for row_i in range(HEADER_ROWS):
            r_part = (" " + raccoon[row_i] + " ").ljust(RACCOON_COL)
            pyrit_idx = row_i - PYRIT_START_ROW
            if 0 <= pyrit_idx < len(PYRIT_LETTERS):
                full_letter = PYRIT_LETTERS[pyrit_idx]
                visible = full_letter[:chars_visible]
                p_part = visible.ljust(len(full_letter))
            elif row_i == subtitle_row_1 and step_i == len(reveal_steps) - 1:
                p_part = "Python Risk Identification Tool"
            elif row_i == subtitle_row_2 and step_i == len(reveal_steps) - 1:
                p_part = "      Interactive Shell"
            else:
                p_part = ""

            full_line = _box_line(r_part + p_part)
            line_idx = len(lines)
            # Per-segment: border + raccoon + PYRIT text + border
            segs: list[tuple[int, int, ColorRole]] = [
                (0, 1, ColorRole.BORDER),
                (1, 1 + RACCOON_COL, ColorRole.RACCOON_BODY),
            ]
            pyrit_start = 1 + RACCOON_COL
            if 0 <= pyrit_idx < len(PYRIT_LETTERS):
                segs.append((pyrit_start, pyrit_start + chars_visible, ColorRole.PYRIT_TEXT))
                segs.append((pyrit_start + chars_visible, len(full_line) - 1, ColorRole.BORDER))
            elif row_i in (subtitle_row_1, subtitle_row_2) and step_i == len(reveal_steps) - 1:
                segs.append((pyrit_start, len(full_line) - 1, ColorRole.SUBTITLE))
            else:
                segs.append((pyrit_start, len(full_line) - 1, ColorRole.BORDER))
            segs.append((len(full_line) - 1, len(full_line), ColorRole.BORDER))
            seg_colors[line_idx] = segs
            color_map[line_idx] = ColorRole.RACCOON_BODY
            lines.append(full_line)

        color_map[len(lines)] = ColorRole.BORDER
        lines.append(empty)
        color_map[len(lines)] = ColorRole.BORDER
        lines.append(mid)
        _pad_to_height(lines, color_map)
        frames.append(AnimationFrame(lines=lines, color_map=color_map, segment_colors=seg_colors, duration=0.15))

    # ── Phase 3: Sparkle celebration (3 frames) ───────────────────────────
    sparkle_spots = [
        [(2, 60, "✦"), (7, 70, "✧"), (11, 50, "*")],
        [(1, 55, "✧"), (5, 75, "✦"), (9, 45, "·"), (3, 80, "*")],
        [],  # final frame = clean (matches static banner)
    ]
    for spots in sparkle_spots:
        lines = [top, empty]
        color_map = {0: ColorRole.BORDER, 1: ColorRole.BORDER}
        seg_colors = {}

        for row_i in range(HEADER_ROWS):
            r_part = (" " + raccoon[row_i] + " ").ljust(RACCOON_COL)
            pyrit_idx = row_i - PYRIT_START_ROW
            if 0 <= pyrit_idx < len(PYRIT_LETTERS):
                p_part = PYRIT_LETTERS[pyrit_idx]
            elif row_i == subtitle_row_1:
                p_part = "Python Risk Identification Tool"
            elif row_i == subtitle_row_2:
                p_part = "      Interactive Shell"
            else:
                p_part = ""

            full_line = _box_line(r_part + p_part)
            line_idx = len(lines)

            # Add sparkle characters (+1 to account for left ║ border)
            for s_row, s_col, s_char in spots:
                target_col = s_col + 1
                if (
                    row_i == s_row
                    and 1 < s_col < BOX_W
                    and target_col < len(full_line) - 1
                    and full_line[target_col] == " "
                ):
                    full_line = full_line[:target_col] + s_char + full_line[target_col + 1 :]

            # Per-segment colors
            segs = [
                (0, 1, ColorRole.BORDER),
                (1, 1 + RACCOON_COL, ColorRole.RACCOON_BODY),
            ]
            pyrit_start = 1 + RACCOON_COL
            if 0 <= pyrit_idx < len(PYRIT_LETTERS):
                segs.append((pyrit_start, pyrit_start + PYRIT_WIDTH, ColorRole.PYRIT_TEXT))
                segs.append((pyrit_start + PYRIT_WIDTH, len(full_line) - 1, ColorRole.BORDER))
            elif row_i in (subtitle_row_1, subtitle_row_2):
                segs.append((pyrit_start, len(full_line) - 1, ColorRole.SUBTITLE))
            else:
                segs.append((pyrit_start, len(full_line) - 1, ColorRole.BORDER))
            # Add sparkle color segments only where a sparkle char was actually placed
            for s_row, s_col, s_char in spots:
                target_col = s_col + 1
                if (
                    row_i == s_row
                    and 1 < s_col < BOX_W
                    and target_col < len(full_line) - 1
                    and full_line[target_col] == s_char
                ):
                    segs.append((target_col, target_col + 1, ColorRole.SPARKLE))
            segs.append((len(full_line) - 1, len(full_line), ColorRole.BORDER))
            seg_colors[line_idx] = segs
            color_map[line_idx] = ColorRole.RACCOON_BODY
            lines.append(full_line)

        color_map[len(lines)] = ColorRole.BORDER
        lines.append(empty)
        color_map[len(lines)] = ColorRole.BORDER
        lines.append(mid)
        _pad_to_height(lines, color_map)
        frames.append(AnimationFrame(lines=lines, color_map=color_map, segment_colors=seg_colors, duration=0.2))

    # ── Phase 4: Commands section reveals (2 frames) ──────────────────────
    # Use the actual static banner lines, revealing commands section
    header_end = next(i for i, line in enumerate(STATIC_BANNER_LINES) if "╠" in line) + 1  # line after mid divider
    cmd_start = header_end
    cmd_lines = STATIC_BANNER_LINES[cmd_start:]

    for cmd_step in [0, 1]:
        lines = list(STATIC_BANNER_LINES[:cmd_start])
        color_map = {i: STATIC_COLOR_MAP.get(i, ColorRole.BORDER) for i in range(len(lines))}
        seg_colors = {i: STATIC_SEGMENT_COLORS[i] for i in range(len(lines)) if i in STATIC_SEGMENT_COLORS}

        if cmd_step == 0:
            half = len(cmd_lines) // 2
            for cl_idx, cl in enumerate(cmd_lines[:half]):
                src_idx = cmd_start + cl_idx
                color_map[len(lines)] = STATIC_COLOR_MAP.get(src_idx, ColorRole.COMMANDS)
                if src_idx in STATIC_SEGMENT_COLORS:
                    seg_colors[len(lines)] = STATIC_SEGMENT_COLORS[src_idx]
                lines.append(cl)
            _pad_to_height(lines, color_map)
        else:
            for j, cl in enumerate(cmd_lines):
                src_idx = cmd_start + j
                color_map[len(lines)] = STATIC_COLOR_MAP.get(src_idx, ColorRole.COMMANDS)
                if src_idx in STATIC_SEGMENT_COLORS:
                    seg_colors[len(lines)] = STATIC_SEGMENT_COLORS[src_idx]
                lines.append(cl)

        frames.append(AnimationFrame(lines=lines, color_map=color_map, segment_colors=seg_colors, duration=0.15))

    return frames


def _render_line_with_segments(
    line: str,
    segments: list[tuple[int, int, ColorRole]],
    theme: dict[ColorRole, str],
) -> str:
    """
    Render a line with per-segment coloring (handles overlapping segments).

    Returns:
        The rendered line string with ANSI color codes.
    """
    reset = _get_color(ColorRole.RESET, theme)
    # Build per-character color map (later segments override earlier ones)
    char_roles: list[Optional[ColorRole]] = [None] * len(line)
    for start, end, role in segments:
        for pos in range(start, min(end, len(line))):
            char_roles[pos] = role

    # Group consecutive same-role characters for efficient rendering
    result: list[str] = []
    current_role: Optional[ColorRole] = None
    for pos, ch in enumerate(line):
        char_role = char_roles[pos]
        if char_role != current_role:
            color = _get_color(char_role, theme) if char_role else reset
            result.append(color)
            current_role = char_role
        result.append(ch)
    result.append(reset)
    return "".join(result)


def _render_frame(frame: AnimationFrame, theme: dict[ColorRole, str]) -> str:
    """
    Render a single frame with colors applied.

    Returns:
        The rendered frame string with ANSI color codes.
    """
    reset = _get_color(ColorRole.RESET, theme)
    rendered_lines: list[str] = []
    for i, line in enumerate(frame.lines):
        if i in frame.segment_colors:
            rendered_lines.append(_render_line_with_segments(line, frame.segment_colors[i], theme))
        else:
            role = frame.color_map.get(i, ColorRole.BORDER)
            color = _get_color(role, theme)
            rendered_lines.append(f"{color}{line}{reset}")
    return "\n".join(rendered_lines)


def _render_static_banner(theme: dict[ColorRole, str]) -> str:
    """
    Render the static banner with colors.

    Returns:
        The rendered static banner string with ANSI color codes.
    """
    reset = _get_color(ColorRole.RESET, theme)
    rendered_lines: list[str] = []
    for i, line in enumerate(STATIC_BANNER_LINES):
        if i in STATIC_SEGMENT_COLORS:
            rendered_lines.append(_render_line_with_segments(line, STATIC_SEGMENT_COLORS[i], theme))
        else:
            role = STATIC_COLOR_MAP.get(i, ColorRole.BORDER)
            color = _get_color(role, theme)
            rendered_lines.append(f"{color}{line}{reset}")
    return "\n".join(rendered_lines)


def get_static_banner() -> str:
    """
    Get the static (non-animated) banner string, with colors if supported.

    Returns:
        The static banner string.
    """
    if sys.stdout.isatty() and not os.environ.get("NO_COLOR"):
        theme = _detect_theme()
        return _render_static_banner(theme)
    return "\n".join(STATIC_BANNER_LINES)


def play_animation(no_animation: bool = False) -> str:
    """
    Play the animated banner or return the static banner.

    Args:
        no_animation: If True, skip animation and return static banner.

    Returns:
        The static banner string when animation is skipped or unsupported.
        Returns empty string when animation ran (output was written directly to stdout).
    """
    if no_animation or not can_animate():
        return get_static_banner()

    theme = _detect_theme()
    frames = _build_animation_frames()
    frame_height = max(len(f.lines) for f in frames)

    try:
        # Hide cursor during animation
        sys.stdout.write("\033[?25l")

        # Reserve vertical space so the terminal doesn't scroll during animation.
        # Print blank lines to push content up, then move cursor back to the top.
        sys.stdout.write("\n" * (frame_height - 1))
        sys.stdout.write(f"\033[{frame_height - 1}A")
        sys.stdout.write("\r")
        sys.stdout.flush()

        for frame_idx, frame in enumerate(frames):
            rendered = _render_frame(frame, theme)

            if frame_idx > 0:
                # Move cursor back to the top of the reserved space
                sys.stdout.write(f"\033[{frame_height - 1}A\r")

            sys.stdout.write(rendered)
            sys.stdout.flush()
            time.sleep(frame.duration)

        # Final frame: overwrite with the static banner (colored)
        sys.stdout.write(f"\033[{frame_height - 1}A\r")
        static = _render_static_banner(theme)
        sys.stdout.write(static)
        sys.stdout.write("\n")
        sys.stdout.flush()

    except KeyboardInterrupt:
        # User pressed Ctrl+C — move cursor to top of reserved space, then show static banner
        sys.stdout.write(f"\033[{frame_height - 1}A\r")
        sys.stdout.write("\033[J")  # clear from cursor to end of screen
        static = _render_static_banner(theme)
        sys.stdout.write(static)
        sys.stdout.write("\n")
        sys.stdout.flush()

    finally:
        # Show cursor again
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()

    # Return empty string since we already printed the banner
    return ""
