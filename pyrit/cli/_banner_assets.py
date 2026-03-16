# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Visual assets for the PyRIT CLI banner.

Contains the braille raccoon art, PYRIT block letters, and the raccoon tail
used in the animated and static banners.
"""

# ── Raccoon braille art ────────────────────────────────────────────────────────
# High-detail raccoon face rendered in Unicode braille characters.
# The raccoon's bandit mask and features are visible as lighter dot patterns
# against the solid ⣿ background.

BRAILLE_RACCOON = [
    "⠀⠀⠀⠀⠀⠀⣀⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⡀⠀⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⣼⢻⠈⢑⣆⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⢎⠁⠉⣻⡀⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⡇⠀⠁⢙⣿⣮⢲⠀⠀⠀⠀⠀⠀⠀⢠⣾⣟⠀⠸⢫⡇⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⣧⢀⠀⠘⣷⣿⠆⠀⠐⠘⠿⠓⠀⠀⢾⣧⠃⠀⠐⣼⠀⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⠘⣇⢰⣶⠛⣁⣐⣷⣦⠐⢘⣼⣷⣂⡀⠛⢽⣆⣸⠁⠀⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠀⣚⣾⡿⢡⣴⣿⣿⣿⣿⠇⠸⣿⣿⣿⣿⣶⡄⠾⣷⣟⡀⠀⠀⠀⠀",
    "⠀⠀⠀⠀⠘⣻⠇⣲⡿⠟⠋⢉⠉⢿⠰⠆⡿⠋⠉⠙⠿⣿⣆⡻⣿⣓⠀⠀⠀⠀",
    "⠀⠀⠀⣰⢿⣷⠞⢩⠀⠀⠀⠈⢀⣀⠀⡀⣠⡀⠈⠀⠀⣨⠛⢷⣿⣭⠃⠀⠀⠀",
    "⠀⠀⠀⣶⠟⠁⠶⠀⠀⠀⠀⣠⣾⡟⠘⠃⢻⣿⣌⠀⠀⠀⠀⠀⠀⠻⣷⠀⠀⠀",
    "⠀⠀⠘⠿⣔⠺⠀⠀⠀⠀⢰⣿⣿⡀⠘⠀⢀⣿⣿⡆⡂⠀⡈⠡⠜⣙⣿⠇⠀⠀",
    "⠀⠀⠀⠐⠻⢿⣶⣅⢀⠐⠀⠙⣒⡃⡀⠄⢘⠉⠋⠁⠆⢀⢼⣿⣿⡟⠋⠁⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠭⠛⠿⠿⠛⠧⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀",
]

# ── PYRIT block letters (same style as existing banner) ────────────────────────

PYRIT_LETTERS = [
    "██████╗          ██████╗ ██╗████████╗",
    "██╔══██╗██╗   ██╗██╔══██╗██║╚══██╔══╝",
    "██████╔╝╚██╗ ██╔╝██████╔╝██║   ██║   ",
    "██╔═══╝  ╚████╔╝ ██╔══██╗██║   ██║   ",
    "██║       ╚██╔╝  ██║  ██║██║   ██║   ",
    "╚═╝        ██║   ╚═╝  ╚═╝╚═╝   ╚═╝   ",
    "           ╚═╝                       ",
]

# Approximate visible width of the PYRIT block letters
PYRIT_WIDTH = 37

# ── Raccoon tail (striped braille art) ─────────────────────────────────────────
# Curling tail: curves right then sweeps back left at the tip.
# Offset pattern: 0→0→1→2→3→3→3→2→1→0 creates the S-curve curl.
# Alternating dense (⣿) and sparse (⠇⠸) rows create the striped look.

RACCOON_TAIL = [
    "⣿⣿⣿⣿⣿⣿⣿⣿⠀",  # off=0 w=8 (dark)
    "⠇⠀⠀⠀⠀⠀⠀⠀⠸",  # off=0 w=9 (light edges)
    "⠀⣿⣿⣿⣿⣿⣿⣿⣿",  # off=1 w=8 (dark, curving right)
    "⠀⠀⠇⠀⠀⠀⠀⠀⠸",  # off=2 w=7 (light edges)
    "⠀⠀⠀⣿⣿⣿⣿⣿⣿",  # off=3 w=6 (dark, peak of curl)
    "⠀⠀⠀⠇⠀⠀⠀⠀⠸",  # off=3 w=6 (light edges)
    "⠀⠀⠀⣿⣿⣿⣿⣿⠀",  # off=3 w=5 (dark, starting back)
    "⠀⠀⠇⠀⠀⠸⠀⠀⠀",  # off=2 w=4 (light edges, curling back)
    "⠀⣿⣿⣿⠀⠀⠀⠀⠀",  # off=1 w=3 (dark, curling back)
    "⠇⠸⠀⠀⠀⠀⠀⠀⠀",  # off=0 w=2 (light edges / tip)
]
