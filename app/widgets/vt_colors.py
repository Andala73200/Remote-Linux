ANSI_16 = [
    "#1b1d20", "#ff6b6b", "#69db7c", "#ffd43b",
    "#74c0fc", "#da77f2", "#66d9e8", "#f1f3f5",
    "#868e96", "#ff8787", "#8ce99a", "#ffe066",
    "#a5d8ff", "#e599f7", "#99e9f2", "#ffffff",
]

DEC_GRAPHICS = {
    "j": "┘", "k": "┐", "l": "┌", "m": "└", "n": "┼",
    "q": "─", "t": "├", "u": "┤", "v": "┴", "w": "┬",
    "x": "│", "`": "◆", "a": "▒", "f": "°", "g": "±",
    "~": "·", ",": "←", "+": "→", ".": "↓", "-": "↑",
}


def xterm_color(index: int) -> str:
    index = max(0, min(255, index))
    if index < 16:
        return ANSI_16[index]
    if index < 232:
        value = index - 16
        r, value = divmod(value, 36)
        g, b = divmod(value, 6)
        component = lambda n: 0 if n == 0 else 55 + n * 40
        return f"#{component(r):02x}{component(g):02x}{component(b):02x}"
    gray = 8 + (index - 232) * 10
    return f"#{gray:02x}{gray:02x}{gray:02x}"
